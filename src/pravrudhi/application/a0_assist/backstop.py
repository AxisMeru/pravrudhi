"""The backstop sweeper: one pass, from a separate process that holds ONLY the sweeper database role.

    python -m pravrudhi.application.a0_assist.backstop [--root DIR] [--canary] [--prune]
    python -m pravrudhi.application.a0_assist.backstop [--root DIR] --spend

It reads the sessions the store lists as pending for more than `BACKSTOP_MIN_AGE_MIN` minutes (ids, created time and state
only), deletes each at Anthropic with its own key, records the observed status, and optionally runs the standing canary
and the daily prune. It is what makes "deleted within the retention window" hold when no engine worker is alive.

Environment (names only here; values come from a secrets file, never from arguments, logs or output):
  SUPABASE_ASSIST_DB_URL_SWEEPER (the sweeper role's URL), ANTHROPIC_SWEEPER_KEY (this process's own Anthropic key),
  and the workspace id variable named in the config.

Output: ONE JSON line of counts and flags, never an id or a value. Exit code 0 = healthy; 2 = ALERT (a session pending past
the retention window, a failed canary or any canary or store error, a failed delete); 3 = not configured.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.application.a0_assist import config as C
from pravrudhi.application.a0_assist import guard
from pravrudhi.application.a0_assist import store as ST
from pravrudhi.application.a0_assist import sweeper as SW
from pravrudhi.application.a0_assist import transport as TR

BACKSTOP_MIN_AGE_MIN = 60
KEY_ENV = "ANTHROPIC_SWEEPER_KEY"
EXIT_OK, EXIT_ALERT, EXIT_NOT_CONFIGURED = 0, 2, 3


def _age_s(created: Any, now: float) -> float | None:
    if isinstance(created, datetime):
        return now - created.astimezone(UTC).timestamp() if created.tzinfo else None
    if isinstance(created, str):
        try:
            return now - datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(UTC).timestamp()
        except ValueError:
            return None
    return None


def run(
    root: Path,
    env: Mapping[str, str],
    *,
    canary: bool,
    prune: bool,
    store: ST.SpendStore | None = None,
    transport: TR.AnthropicTransport | None = None,
    now: float | None = None,
) -> tuple[int, dict[str, Any]]:
    cfg, why = C.load_or_none(Path(root))
    if cfg is None:
        return EXIT_NOT_CONFIGURED, {"ok": False, "alert": "config_unreadable"}
    if store is None:
        store, _ = ST.from_env(env, cfg.sweeper_dsn_env, timeout_s=cfg.store_timeout_s, ca_file=cfg.store_ca_file)
    if transport is None:
        try:
            transport = TR.AnthropicTransport(
                env.get(KEY_ENV, ""), env.get(cfg.workspace_id_env, ""), timeout_s=cfg.call_timeout_s
            )
        except TR.TransportRefused:
            transport = None
    if store is None or transport is None:
        return EXIT_NOT_CONFIGURED, {"ok": False, "alert": "not_configured"}
    t = time.time() if now is None else now
    pauses = guard.Pauses()
    sweeper = SW.Sweeper(
        cfg,
        store,
        transport,
        pauses,
        mode="backstop",
        min_age_min=BACKSTOP_MIN_AGE_MIN,
        canary_enabled=canary,
        prune_enabled=prune,
    )
    # the oldest pending row decides the retention alert (read before the pass: the pass deletes what it can)
    oldest: float | None = None
    try:
        for row in store.list_pending(BACKSTOP_MIN_AGE_MIN):
            a = _age_s(row.get("created_at"), t)
            oldest = a if oldest is None or (a is not None and a > oldest) else oldest
    except ST.StoreError:
        pass
    out = sweeper.run_once()
    alerts: list[str] = []
    if out.get("error"):
        alerts.append(str(out["error"]))
    if out.get("failed"):
        alerts.append("delete_failed")
    if oldest is not None and oldest > cfg.retention_max_hours * 3600:  # the window was breached, even if this pass cleared it
        alerts.append("pending_past_retention")
    canary_res = out.get("canary")
    if canary and (canary_res is None or not canary_res.get("ok")):
        error = (canary_res or {}).get("error")
        alerts.append(
            "canary_failed" if canary_res and error is None else "canary_timeout" if error == "timeout" else "canary_error"
        )
    summary = {
        "ok": not alerts,
        "alert": ",".join(alerts) or None,
        "pending": out.get("pending"),
        "deleted": out.get("deleted"),
        "failed": out.get("failed"),
        "canary": None if canary_res is None else bool(canary_res.get("ok")),
        "pruned": out.get("pruned"),
    }
    return (EXIT_ALERT if alerts else EXIT_OK), summary


SPEND_KEYS = ("day", "month_start", "spent_today_cents", "spent_month_cents", "reserved_open_cents")


def spend(root: Path, env: Mapping[str, str], *, store: ST.SpendStore | None = None) -> tuple[int, dict[str, Any]]:
    """The read-only spend totals for the daily billing close: cents and two dates, nothing else. Needs only the sweeper
    role (no Anthropic key, no deletion, no write). Exit 0 with the numbers, 2 if the store cannot answer, 3 if not configured."""
    cfg, _why = C.load_or_none(Path(root))
    if cfg is None:
        return EXIT_NOT_CONFIGURED, {"ok": False, "alert": "config_unreadable"}
    if store is None:
        store, _ = ST.from_env(env, cfg.sweeper_dsn_env, timeout_s=cfg.store_timeout_s, ca_file=cfg.store_ca_file)
    if store is None:
        return EXIT_NOT_CONFIGURED, {"ok": False, "alert": "not_configured"}
    try:
        doc = store.spend_summary()
    except ST.StoreError:
        return EXIT_ALERT, {"ok": False, "alert": "store"}
    out: dict[str, Any] = {k: doc.get(k) for k in SPEND_KEYS}
    ints = all(isinstance(v := out[k], int) and not isinstance(v, bool) and v >= 0 for k in SPEND_KEYS[2:])
    if not ints or not all(isinstance(out[k], str) for k in SPEND_KEYS[:2]):
        return EXIT_ALERT, {"ok": False, "alert": "unexpected_shape"}
    return EXIT_OK, {"ok": True, **out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="a0-assist-backstop")
    ap.add_argument("--root", default=os.environ.get("PRAVRUDHI_ROOT", "."))
    ap.add_argument("--canary", action="store_true")
    ap.add_argument("--prune", action="store_true")
    ap.add_argument("--spend", action="store_true", help="print the read-only spend totals (cents) and nothing else")
    a = ap.parse_args(argv)
    if a.spend:
        code, summary = spend(Path(a.root), os.environ)
        print(json.dumps(summary, sort_keys=True))
        return code
    code, summary = run(Path(a.root), os.environ, canary=a.canary, prune=a.prune)
    print(json.dumps(summary, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
