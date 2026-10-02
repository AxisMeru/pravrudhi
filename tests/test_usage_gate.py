"""The usage gates as code (Lead-2, 2 Oct): `panel.ask_vendor` refuses a codex or claude call when the seat's latest
rate-limit reading is at/above the threshold, unverified, or older than the configured age. All readings here are
CONSTRUCTED; nothing calls a CLI. Thresholds and ages come from `configs/usage_gate.yaml`, never from code."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from pravrudhi.agents import cli_agents
from pravrudhi.application import panel, usage_gate

NOW = dt.datetime(2026, 10, 5, 10, 0, tzinfo=dt.UTC)
REPO = Path(__file__).resolve().parent.parent

CFG = {
    "max_age_min": {"codex": 60, "claude": 30},
    "codex": {"weekly_max_pct": 60, "five_hour_max_pct": 70},
    "claude": {
        "seat_key": "Claude-Axismeru",
        "weekly_max_pct": 60,
        "five_hour_max_pct": 70,
        "usage_file": "claude_usage.json",
    },
}


def iso(minutes_ago: float) -> str:
    return (NOW - dt.timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def codex_reading(weekly=10.0, five=5.0, age=5, verified=True, weekly_resets_in_h=48):
    return {
        "verified": verified,
        "latest_rate_limits": None
        if weekly is None and five is None and age is None
        else {
            "observed_at": iso(age),
            "weekly_used_pct": weekly,
            "five_hour_used_pct": five,
            "weekly_resets_at": (NOW + dt.timedelta(hours=weekly_resets_in_h)).isoformat(),
            "five_hour_resets_at": (NOW + dt.timedelta(hours=3)).isoformat(),
        },
    }


def claude_file(tmp_path, weekly=54.0, five=9.0, age=5, verified=True, key="Claude-Axismeru"):
    p = tmp_path / "claude_usage.json"
    p.write_text(
        json.dumps(
            {
                "seats": {
                    key: {
                        "verified": verified,
                        "observed_at": iso(age),
                        "seven_day_used_pct": weekly,
                        "five_hour_used_pct": five,
                        "reason": "ok" if verified else "tunnel_down",
                    }
                }
            }
        )
    )
    return p


@pytest.fixture
def root(tmp_path):
    (tmp_path / "configs").mkdir()
    cfg = json.loads(json.dumps(CFG))
    cfg["claude"]["usage_file"] = str(tmp_path / "claude_usage.json")
    (tmp_path / "configs" / "usage_gate.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


def _codex(monkeypatch, reading):
    monkeypatch.setattr(usage_gate, "_read_codex", lambda cfg, now: reading)


class TestCodexGate:
    def test_64_weekly_refuses(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(weekly=64.0))
        with pytest.raises(usage_gate.UsageGateRefused, match=r"weekly.*64.*60"):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_exactly_at_weekly_threshold_refuses(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(weekly=60.0))
        with pytest.raises(usage_gate.UsageGateRefused):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_59_weekly_passes_and_records_reading(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(weekly=59.0, five=12.0, age=7))
        g = usage_gate.gate_reading("codex", root, now=NOW)
        assert g["weekly_used_pct"] == 59.0 and g["five_hour_used_pct"] == 12.0
        assert g["observed_at"] == iso(7) and g["age_min"] == pytest.approx(7)
        assert g["vendor_kind"] == "codex" and g["passed"] is True
        assert g["thresholds"] == {"weekly_max_pct": 60, "five_hour_max_pct": 70, "max_age_min": 60}

    def test_five_hour_70_refuses_69_passes(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(five=70.0))
        with pytest.raises(usage_gate.UsageGateRefused, match="five-hour"):
            usage_gate.gate_reading("codex", root, now=NOW)
        _codex(monkeypatch, codex_reading(five=69.0))
        assert usage_gate.gate_reading("codex", root, now=NOW)["passed"]

    def test_stale_reading_refuses_fail_closed(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(weekly=10.0, age=61))
        with pytest.raises(usage_gate.UsageGateRefused, match="older than 60 min"):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_max_age_comes_from_config(self, root, monkeypatch):
        cfg = yaml.safe_load((root / "configs" / "usage_gate.yaml").read_text())
        cfg["max_age_min"]["codex"] = 5
        (root / "configs" / "usage_gate.yaml").write_text(yaml.safe_dump(cfg))
        _codex(monkeypatch, codex_reading(age=6))
        with pytest.raises(usage_gate.UsageGateRefused, match="older than 5 min"):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_unverified_refuses(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(verified=False))
        with pytest.raises(usage_gate.UsageGateRefused, match="unverified"):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_no_reading_refuses(self, root, monkeypatch):
        _codex(monkeypatch, {"verified": True, "latest_rate_limits": None})
        with pytest.raises(usage_gate.UsageGateRefused, match="no rate-limit reading"):
            usage_gate.gate_reading("codex", root, now=NOW)

    @pytest.mark.parametrize("field", ["weekly", "five"])
    def test_missing_percentage_refuses_never_assumes_zero(self, root, monkeypatch, field):
        _codex(monkeypatch, codex_reading(**{field: None, **({"five": 1.0} if field == "weekly" else {"weekly": 1.0})}))
        with pytest.raises(usage_gate.UsageGateRefused, match="missing"):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_window_reset_since_reading_refuses(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(weekly=10.0, age=5, weekly_resets_in_h=-1))
        with pytest.raises(usage_gate.UsageGateRefused, match="reset"):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_future_observed_at_refuses(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(age=-30))
        with pytest.raises(usage_gate.UsageGateRefused, match="future"):
            usage_gate.gate_reading("codex", root, now=NOW)


class TestClaudeGate:
    def test_64_weekly_refuses(self, root):
        claude_file(root, weekly=64.0)
        with pytest.raises(usage_gate.UsageGateRefused, match=r"weekly.*64.*60"):
            usage_gate.gate_reading("claude", root, now=NOW)

    def test_59_passes_and_records_reading(self, root):
        claude_file(root, weekly=59.0, five=20.0, age=3)
        g = usage_gate.gate_reading("claude", root, now=NOW)
        assert g["weekly_used_pct"] == 59.0 and g["five_hour_used_pct"] == 20.0
        assert g["observed_at"] == iso(3) and g["seat_key"] == "Claude-Axismeru" and g["passed"]

    def test_five_hour_70_refuses(self, root):
        claude_file(root, five=70.0)
        with pytest.raises(usage_gate.UsageGateRefused, match="five-hour"):
            usage_gate.gate_reading("claude", root, now=NOW)

    def test_stale_refuses(self, root):
        claude_file(root, age=31)
        with pytest.raises(usage_gate.UsageGateRefused, match="older than 30 min"):
            usage_gate.gate_reading("claude", root, now=NOW)

    def test_unverified_refuses_even_with_low_numbers(self, root):
        claude_file(root, weekly=1.0, five=1.0, verified=False)
        with pytest.raises(usage_gate.UsageGateRefused, match="unverified"):
            usage_gate.gate_reading("claude", root, now=NOW)

    def test_other_seat_numbers_are_never_used(self, root):
        claude_file(root, weekly=1.0, key="Claude")
        with pytest.raises(usage_gate.UsageGateRefused, match="Claude-Axismeru"):
            usage_gate.gate_reading("claude", root, now=NOW)

    def test_missing_or_corrupt_file_refuses(self, root):
        with pytest.raises(usage_gate.UsageGateRefused, match="cannot read"):
            usage_gate.gate_reading("claude", root, now=NOW)
        (root / "claude_usage.json").write_text("{not json")
        with pytest.raises(usage_gate.UsageGateRefused, match="cannot read"):
            usage_gate.gate_reading("claude", root, now=NOW)


class TestConfig:
    def test_missing_config_refuses(self, tmp_path):
        with pytest.raises(usage_gate.UsageGateRefused, match="usage_gate.yaml"):
            usage_gate.gate_reading("codex", tmp_path, now=NOW)

    def test_missing_key_refuses_no_default_invented(self, root):
        cfg = yaml.safe_load((root / "configs" / "usage_gate.yaml").read_text())
        claude_file(root)
        del cfg["max_age_min"]
        (root / "configs" / "usage_gate.yaml").write_text(yaml.safe_dump(cfg))
        with pytest.raises(usage_gate.UsageGateRefused, match="max_age_min"):
            usage_gate.gate_reading("claude", root, now=NOW)

    def test_shipped_config_matches_the_house_rules(self):
        cfg = yaml.safe_load((REPO / "configs" / "usage_gate.yaml").read_text())
        for kind in ("codex", "claude"):
            assert cfg[kind]["weekly_max_pct"] == 60 and cfg[kind]["five_hour_max_pct"] == 70
            assert cfg["max_age_min"][kind] > 0
        assert cfg["claude"]["seat_key"] == "Claude-Axismeru"  # seat 2 in claude_usage.json (sharath.sathish)


class TestAskVendorIsGated:
    @pytest.fixture(autouse=True)
    def _slim(self, tmp_path, monkeypatch):
        home = tmp_path / "loop"
        home.mkdir()
        (home / ".credentials.json").write_text("{}")
        monkeypatch.setenv("PRAVRUDHI_CLAUDE_CLI_CONFIG_DIR", str(home))
        monkeypatch.setattr(panel, "_claude_auth_email", lambda env: panel.CLAUDE_CLI_EXPECTED_EMAIL)

    def _stub_run(self, monkeypatch, out):
        calls = []

        def run(cmd, cwd, timeout_s, env=None, *, stdin_text=None):
            calls.append(cmd)
            return 0, out, "", 0.1

        monkeypatch.setattr(cli_agents, "_run", run)
        return calls

    def test_codex_over_gate_makes_no_call(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(weekly=64.0))
        calls = self._stub_run(monkeypatch, "x")
        with pytest.raises(usage_gate.UsageGateRefused):
            panel.ask_vendor(panel.VENDORS["codex-cli"], "q", root=root)
        assert calls == []

    def test_claude_over_gate_makes_no_call(self, root, monkeypatch):
        claude_file(root, weekly=64.0, age=0)
        monkeypatch.setattr(usage_gate, "_now", lambda: dt.datetime.now(dt.UTC))
        p = root / "claude_usage.json"
        d = json.loads(p.read_text())
        d["seats"]["Claude-Axismeru"]["observed_at"] = dt.datetime.now(dt.UTC).isoformat()
        p.write_text(json.dumps(d))
        calls = self._stub_run(monkeypatch, "x")
        with pytest.raises(usage_gate.UsageGateRefused):
            panel.ask_vendor(panel.VENDORS["claude-cli"], "q", root=root)
        assert calls == []

    def test_passing_claude_call_records_the_reading_on_the_answer(self, root, monkeypatch):
        now = dt.datetime.now(dt.UTC)
        monkeypatch.setattr(usage_gate, "_now", lambda: now)
        p = claude_file(root, weekly=59.0)
        d = json.loads(p.read_text())
        d["seats"]["Claude-Axismeru"]["observed_at"] = now.isoformat()
        p.write_text(json.dumps(d))
        env = json.dumps({"result": "ANSWER: A", "is_error": False, "modelUsage": {"claude-sonnet-5": {}}, "usage": {}})
        self._stub_run(monkeypatch, env)
        ans = panel.ask_vendor(panel.VENDORS["claude-cli"], "q", root=root)
        assert ans.usage_gate["weekly_used_pct"] == 59.0 and ans.usage_gate["passed"] is True

    def test_run_panel_aborts_on_a_closed_gate_not_one_gap_row_per_prompt(self, root, monkeypatch, tmp_path):
        _codex(monkeypatch, codex_reading(weekly=64.0))
        self._stub_run(monkeypatch, "x")
        vendor = replace(panel.VENDORS["codex-cli"])
        prompts = [{"id": f"p{i}", "prompt": "q"} for i in range(3)]
        monkeypatch.chdir(root)
        with pytest.raises(usage_gate.UsageGateRefused):
            panel.run_panel(tmp_path / "out", prompts, [vendor])
        rows = (tmp_path / "out" / "panel" / "answers.jsonl").read_text().splitlines()
        assert rows == []

    def test_openai_compat_vendors_are_not_gated(self, root):
        assert usage_gate.GATED_KINDS == ("codex", "claude")
