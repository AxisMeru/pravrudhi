"""Read-only usage reader over the codex rollout files (``<CODEX_HOME>/sessions/Y/M/D/rollout-*.jsonl``).

Per day: calls, turns, threads, tokens and the model (``turn_context.payload.model``). Latest rate-limit percentages
(5-hour and weekly) from the newest ``token_count`` event. Only counts, ids, model names and percentages are read
into the result; prompt, response and tool text is never kept. Codex records no cost, so none is reported.

A call is one ``token_usage_record`` (one model response). ``input`` includes ``cached_input`` (a subset, not additive).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_USAGE_KEYS = (
    ("input", "input_tokens"),
    ("cached_input", "cached_input_tokens"),
    ("output", "output_tokens"),
    ("reasoning_output", "reasoning_output_tokens"),
)


def _home(codex_home: str | os.PathLike[str] | None) -> Path:
    return Path(codex_home) if codex_home else Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _ts(s: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def _window(rl: dict[str, Any], minutes: int) -> dict[str, Any] | None:
    for k in ("primary", "secondary"):
        w = rl.get(k)
        if isinstance(w, dict) and w.get("window_minutes") == minutes:
            return w
    return None


def _limits(ts: dt.datetime, rl: dict[str, Any]) -> dict[str, Any]:
    def reset(w: dict[str, Any] | None) -> str | None:
        r = w.get("resets_at") if w else None
        return dt.datetime.fromtimestamp(r, dt.UTC).isoformat() if isinstance(r, (int, float)) else None

    five, week = _window(rl, 300), _window(rl, 10080)
    return {
        "observed_at": ts.astimezone(dt.UTC).isoformat().replace("+00:00", "Z"),
        "five_hour_used_pct": five.get("used_percent") if five else None,
        "five_hour_resets_at": reset(five),
        "weekly_used_pct": week.get("used_percent") if week else None,
        "weekly_resets_at": reset(week),
        "plan_type": rl.get("plan_type") if isinstance(rl.get("plan_type"), str) else None,
    }


def _files(sessions: Path, since: dt.date | None, until: dt.date | None) -> Iterator[Path]:
    for f in sorted(sessions.glob("*/*/*/rollout-*.jsonl")):
        try:
            d = dt.date(int(f.parts[-4]), int(f.parts[-3]), int(f.parts[-2]))
        except ValueError:
            continue
        if since and d < since - dt.timedelta(days=1) or until and d > until + dt.timedelta(days=1):
            continue
        yield f


def read_codex_usage(
    since: dt.date | None = None,
    until: dt.date | None = None,
    *,
    codex_home: str | os.PathLike[str] | None = None,
    tz: dt.tzinfo = dt.UTC,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    home = _home(codex_home)
    sessions = home / "sessions"
    now = now or dt.datetime.now(dt.UTC)
    skipped = Counter(files_unreadable=0, lines_bad=0)
    out: dict[str, Any] = {
        "source": "codex rollout files",
        "codex_home": str(home),
        "as_of": now.isoformat().replace("+00:00", "Z"),
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "verified": False,
        "days": [],
        "rate_limits": {},
        "latest_rate_limits": None,
        "skipped": dict(skipped),
    }
    if not sessions.is_dir():
        return out
    records: list[Any] = []
    seen: set[Any] = set()
    newest: dict[str, Any] = {}
    for f in _files(sessions, since, until):
        models: dict[Any, str] = {}
        recs: list[Any] = []
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        o = json.loads(line)
                        t, p, ts = o.get("type"), o.get("payload") or {}, _ts(o.get("timestamp"))
                        if t == "turn_context":
                            if isinstance(p.get("model"), str):
                                models[p.get("turn_id")] = p["model"]
                        elif t == "token_usage_record" and ts and isinstance(p.get("usage"), dict):
                            recs.append((ts, p, p["usage"]))
                        elif (
                            t == "event_msg" and p.get("type") == "token_count" and ts and isinstance(p.get("rate_limits"), dict)
                        ):
                            lid = str(p["rate_limits"].get("limit_id") or "codex")
                            if lid not in newest or ts >= newest[lid][0]:
                                newest[lid] = (ts, p["rate_limits"])
                    except (ValueError, AttributeError, TypeError):
                        skipped["lines_bad"] += 1
        except OSError:
            skipped["files_unreadable"] += 1
            continue
        for ts, p, u in recs:
            rid = p.get("response_id")
            if rid is not None:
                key = (p.get("thread_id"), rid)
                if key in seen:
                    continue
                seen.add(key)
            records.append((ts, p.get("thread_id"), p.get("turn_id"), models.get(p.get("turn_id"), "unknown"), u))
    days: dict[str, dict[str, Any]] = {}
    for ts, thread, turn, model, u in records:
        day = ts.astimezone(tz).date()
        if since and day < since or until and day > until:
            continue
        d = days.setdefault(
            day.isoformat(),
            {
                "date": day.isoformat(),
                "calls": 0,
                "_turns": set(),
                "_threads": set(),
                "tokens": dict.fromkeys([k for k, _ in _USAGE_KEYS], 0),
                "by_model": {},
            },
        )
        d["calls"] += 1
        d["_turns"].add((thread, turn))
        d["_threads"].add(thread)
        m = d["by_model"].setdefault(model, {"calls": 0, **dict.fromkeys([k for k, _ in _USAGE_KEYS], 0)})
        m["calls"] += 1
        for k, src in _USAGE_KEYS:
            d["tokens"][k] += _int(u.get(src))
            m[k] += _int(u.get(src))
    for d in days.values():
        d["turns"], d["threads"] = len(d.pop("_turns")), len(d.pop("_threads"))
        d["tokens"]["total"] = d["tokens"]["input"] + d["tokens"]["output"]
        d["tokens_total"] = d["tokens"]["total"]
        d["model"] = max(d["by_model"], key=lambda k: (d["by_model"][k]["calls"], k))
    out["days"] = [days[k] for k in sorted(days)]
    out["rate_limits"] = {lid: _limits(ts, rl) for lid, (ts, rl) in newest.items()}
    out["latest_rate_limits"] = max(out["rate_limits"].values(), key=lambda v: v["observed_at"], default=None)
    out["skipped"] = dict(skipped)
    out["verified"] = skipped["files_unreadable"] == 0
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Print codex usage from rollout files as JSON (read-only).")
    ap.add_argument("--since", type=dt.date.fromisoformat)
    ap.add_argument("--until", type=dt.date.fromisoformat)
    ap.add_argument("--codex-home")
    ap.add_argument("--tz", default="UTC", help="IANA zone for the day boundary, e.g. Europe/London")
    a = ap.parse_args(argv)
    json.dump(read_codex_usage(a.since, a.until, codex_home=a.codex_home, tz=ZoneInfo(a.tz)), sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
