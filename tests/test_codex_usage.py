"""Offline tests for pravrudhi.application.codex_usage (codex rollout reader).

The recorded fixtures are trimmed real rollouts (tests/fixtures/codex_usage/README.md). Rollouts built in tmp_path are
CONSTRUCTED to exercise days, models and the no-prompt-text guarantee; they are not measurements.
"""

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from pravrudhi.application import codex_usage as cu

FIX = Path(__file__).resolve().parent / "fixtures/codex_usage"
CANARY = "SECRET-PROMPT-CANARY-9f3"


def rollout(home, day, name, lines):
    d = home / "sessions" / day
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n")


def tctx(ts, turn, model):
    return {"timestamp": ts, "type": "turn_context", "payload": {"turn_id": turn, "model": model, "cwd": "/x", "note": CANARY}}


def usage(ts, turn, rid, i, c, o, thread="t1"):
    u = {
        "input_tokens": i,
        "cached_input_tokens": c,
        "cache_write_input_tokens": 0,
        "output_tokens": o,
        "reasoning_output_tokens": 0,
        "total_tokens": i + o,
    }
    return {
        "timestamp": ts,
        "type": "token_usage_record",
        "payload": {
            "thread_id": thread,
            "turn_id": turn,
            "response_id": rid,
            "usage": u,
            "turn_token_usage": u,
            "thread_token_usage": u,
        },
    }


def limits(ts, p5, pw, plan="plus"):
    return {
        "timestamp": ts,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": None,
            "rate_limits": {
                "limit_id": "codex",
                "primary": {"used_percent": p5, "window_minutes": 300, "resets_at": 1790963832},
                "secondary": {"used_percent": pw, "window_minutes": 10080, "resets_at": 1791056431},
                "plan_type": plan,
            },
        },
    }


def test_recorded_fixture_totals_and_model():
    r = cu.read_codex_usage(codex_home=FIX)
    assert r["verified"] is True and r["skipped"] == {"files_unreadable": 0, "lines_bad": 0}
    assert [d["date"] for d in r["days"]] == ["2026-10-02"]
    d = r["days"][0]
    assert d["calls"] == 3 and d["turns"] == 3 and d["threads"] == 3
    assert d["model"] == "gpt-6-astra" and set(d["by_model"]) == {"gpt-6-astra"}
    t = d["tokens"]
    assert t["input"] >= t["cached_input"] > 0 and t["total"] == t["input"] + t["output"] == d["tokens_total"]


def test_recorded_fixture_rate_limits():
    rl = cu.read_codex_usage(codex_home=FIX)["rate_limits"]["codex"]
    assert rl["five_hour_used_pct"] == 1.0 and rl["weekly_used_pct"] == 64.0
    assert rl["observed_at"].startswith("2026-10-02T12:58") and rl["plan_type"] == "example-plan"
    assert rl["five_hour_resets_at"].endswith("+00:00") and rl["weekly_resets_at"].endswith("+00:00")


def test_latest_rate_limits_alias():
    r = cu.read_codex_usage(codex_home=FIX)
    assert r["latest_rate_limits"] == r["rate_limits"]["codex"]


def test_no_prompt_text_in_output(tmp_path):
    rollout(
        tmp_path,
        "2026/10/02",
        "rollout-a-t1.jsonl",
        [
            tctx("2026-10-02T10:00:00Z", "u1", "m1"),
            usage("2026-10-02T10:00:01Z", "u1", "r1", 100, 40, 5),
            {"timestamp": "2026-10-02T10:00:02Z", "type": "response_item", "payload": {"type": "message", "content": CANARY}},
        ],
    )
    assert CANARY not in json.dumps(cu.read_codex_usage(codex_home=tmp_path))


def test_days_split_by_timezone(tmp_path):
    rollout(
        tmp_path,
        "2026/10/02",
        "rollout-a-t1.jsonl",
        [
            tctx("2026-10-02T22:30:00Z", "u1", "m1"),
            usage("2026-10-02T22:30:01Z", "u1", "r1", 10, 0, 1),
            usage("2026-10-02T23:30:00Z", "u1", "r2", 20, 0, 2),
        ],
    )
    utc = cu.read_codex_usage(codex_home=tmp_path)
    assert [(d["date"], d["calls"]) for d in utc["days"]] == [("2026-10-02", 2)]
    bst = cu.read_codex_usage(codex_home=tmp_path, tz=ZoneInfo("Europe/London"))
    assert [(d["date"], d["calls"]) for d in bst["days"]] == [("2026-10-02", 1), ("2026-10-03", 1)]


def test_multi_model_dominant_and_unknown(tmp_path):
    rollout(
        tmp_path,
        "2026/10/02",
        "rollout-a-t1.jsonl",
        [
            tctx("2026-10-02T10:00:00Z", "u1", "big"),
            usage("2026-10-02T10:00:01Z", "u1", "r1", 1, 0, 1),
            usage("2026-10-02T10:00:02Z", "u1", "r2", 1, 0, 1),
            tctx("2026-10-02T10:01:00Z", "u2", "small"),
            usage("2026-10-02T10:01:01Z", "u2", "r3", 1, 0, 1),
            usage("2026-10-02T10:02:00Z", "u3", "r4", 1, 0, 1),
        ],
    )
    d = cu.read_codex_usage(codex_home=tmp_path)["days"][0]
    assert d["by_model"]["big"]["calls"] == 2 and d["by_model"]["small"]["calls"] == 1 and d["by_model"]["unknown"]["calls"] == 1
    assert d["model"] == "big"


def test_duplicate_response_counted_once(tmp_path):
    line = usage("2026-10-02T10:00:01Z", "u1", "r1", 100, 0, 5)
    rollout(tmp_path, "2026/10/02", "rollout-a-t1.jsonl", [tctx("2026-10-02T10:00:00Z", "u1", "m"), line])
    rollout(tmp_path, "2026/10/02", "rollout-b-t1.jsonl", [line])
    assert cu.read_codex_usage(codex_home=tmp_path)["days"][0]["calls"] == 1


def test_latest_rate_limits_wins_across_files(tmp_path):
    rollout(tmp_path, "2026/10/02", "rollout-a.jsonl", [limits("2026-10-02T12:00:00Z", 5.0, 60.0)])
    rollout(tmp_path, "2026/10/02", "rollout-b.jsonl", [limits("2026-10-02T09:00:00Z", 1.0, 59.0)])
    assert cu.read_codex_usage(codex_home=tmp_path)["rate_limits"]["codex"]["five_hour_used_pct"] == 5.0


def test_since_until_filter(tmp_path):
    for day in ("2026/10/01", "2026/10/02", "2026/10/03"):
        rollout(tmp_path, day, "rollout-x.jsonl", [usage(f"{day.replace('/', '-')}T10:00:00Z", "u", f"r{day}", 1, 0, 1)])
    r = cu.read_codex_usage(codex_home=tmp_path, since=dt.date(2026, 10, 2), until=dt.date(2026, 10, 2))
    assert [d["date"] for d in r["days"]] == ["2026-10-02"]


def test_missing_home_is_unverified(tmp_path):
    r = cu.read_codex_usage(codex_home=tmp_path / "nope")
    assert r["verified"] is False and r["days"] == [] and r["rate_limits"] == {} and r["latest_rate_limits"] is None


def test_empty_sessions_dir_is_unverified(tmp_path):
    (tmp_path / "sessions").mkdir()
    r = cu.read_codex_usage(codex_home=tmp_path)
    assert r["verified"] is False and r["latest_rate_limits"] is None
    rollout(tmp_path, "2026/10/02", "rollout-blank.jsonl", ["", "   "])
    assert cu.read_codex_usage(codex_home=tmp_path)["verified"] is False


def test_all_garbage_sessions_dir_is_unverified(tmp_path):
    rollout(tmp_path, "2026/10/02", "rollout-a.jsonl", ["{not json", "123", "[1, 2]", "null", '"text"'])
    r = cu.read_codex_usage(codex_home=tmp_path)
    assert r["verified"] is False and r["skipped"]["lines_bad"] == 5 and r["days"] == []


def test_one_parseable_record_among_garbage_is_verified(tmp_path):
    rollout(tmp_path, "2026/10/02", "rollout-a.jsonl", ["{not json", usage("2026-10-02T10:00:00Z", "u", "r", 1, 0, 1)])
    assert cu.read_codex_usage(codex_home=tmp_path)["verified"] is True


def test_unreadable_file_makes_unverified_and_bad_lines_counted(tmp_path):
    rollout(tmp_path, "2026/10/02", "rollout-a.jsonl", ["{not json", usage("2026-10-02T10:00:00Z", "u", "r", 1, 0, 1)])
    r = cu.read_codex_usage(codex_home=tmp_path)
    assert r["days"][0]["calls"] == 1 and r["skipped"]["lines_bad"] == 1 and r["verified"] is True
    (tmp_path / "sessions/2026/10/02/rollout-b.jsonl").write_bytes(b"\xff\xfe\x00bad\n")
    r = cu.read_codex_usage(codex_home=tmp_path)
    assert r["skipped"]["files_unreadable"] + r["skipped"]["lines_bad"] >= 2 and r["days"][0]["calls"] == 1
    d = tmp_path / "sessions/2026/10/02/rollout-c.jsonl"
    d.mkdir()
    assert cu.read_codex_usage(codex_home=tmp_path)["verified"] is False


def test_codex_home_env_honoured(tmp_path, monkeypatch):
    rollout(tmp_path, "2026/10/02", "rollout-a.jsonl", [usage("2026-10-02T10:00:00Z", "u", "r", 1, 0, 1)])
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert cu.read_codex_usage()["days"][0]["calls"] == 1


def test_cli_prints_json(capsys):
    assert cu.main(["--codex-home", str(FIX), "--tz", "Europe/London"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {"as_of", "days", "verified", "rate_limits"} <= set(out) and out["days"][0]["calls"] == 3
