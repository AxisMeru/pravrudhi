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
    "codex": {
        "weekly_max_pct": 60,
        "five_hour_max_pct": 70,
        "refresh_margin_pp": 5,
        "refresh_min_interval_min": 60,
        "refresh_timeout_s": 10,
        "refresh_prompt": "constructed refresh prompt",
        "refresh_state_file": "refresh_state.json",
    },
    "claude": {
        "seat_key": "Claude-Axismeru",
        "weekly_max_pct": 60,
        "five_hour_max_pct": 70,
        "usage_file": "claude_usage.json",
    },
    "opus_m4": {
        "five_hour_pause_pct": 70,
        "counter_file": "opus_m4_calls.json",
        "call_cap_per_arm": {"arm-a": 2, "arm-c": 3},
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
    cfg["codex"]["refresh_state_file"] = str(tmp_path / "refresh_state.json")
    cfg["opus_m4"]["counter_file"] = str(tmp_path / "opus_m4_calls.json")
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
        _codex(monkeypatch, codex_reading(weekly=58.0, age=61))
        with pytest.raises(usage_gate.UsageGateRefused, match="older than 60 min"):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_max_age_comes_from_config(self, root, monkeypatch):
        cfg = yaml.safe_load((root / "configs" / "usage_gate.yaml").read_text())
        cfg["max_age_min"]["codex"] = 5
        (root / "configs" / "usage_gate.yaml").write_text(yaml.safe_dump(cfg))
        _codex(monkeypatch, codex_reading(weekly=58.0, age=6))
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

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -50.0, -0.1, 100.1, 250.0])
    @pytest.mark.parametrize("field", ["weekly", "five", "both"])
    def test_non_finite_negative_or_over_100_refuses(self, root, monkeypatch, field, bad):
        vals = {"weekly": 1.0, "five": 1.0}
        for k in ("weekly", "five") if field == "both" else (field,):
            vals[k] = bad
        _codex(monkeypatch, codex_reading(**vals))
        with pytest.raises(usage_gate.UsageGateRefused, match="missing a weekly or five-hour percentage"):
            usage_gate.gate_reading("codex", root, now=NOW)

    @pytest.mark.parametrize("edge", [0.0, 100.0])
    def test_boundary_percentages_are_valid_readings(self, root, monkeypatch, edge):
        _codex(monkeypatch, codex_reading(weekly=edge, five=edge))
        if edge == 0.0:
            assert usage_gate.gate_reading("codex", root, now=NOW)["passed"]
        else:
            with pytest.raises(usage_gate.UsageGateRefused, match="at or above"):
                usage_gate.gate_reading("codex", root, now=NOW)

    def test_future_observed_at_refuses(self, root, monkeypatch):
        _codex(monkeypatch, codex_reading(age=-30))
        with pytest.raises(usage_gate.UsageGateRefused, match="future"):
            usage_gate.gate_reading("codex", root, now=NOW)


class TestCodexRefresh:
    """One bounded refresh call (Lead-2, 2 Oct), codex only. `_run_refresh` is stubbed; no CLI runs."""

    @pytest.fixture
    def refresh(self, monkeypatch):
        state = {"calls": 0, "next": None}

        def run(cfg):
            state["calls"] += 1
            if state["next"] == "boom":
                raise RuntimeError("codex exited 1")
            _codex(monkeypatch, state["next"])

        monkeypatch.setattr(usage_gate, "_run_refresh", run)
        monkeypatch.setattr(usage_gate, "_now", lambda: NOW)
        return state

    def test_stale_with_last_64_refuses_without_refresh(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=64.0, age=120))
        with pytest.raises(usage_gate.UsageGateRefused, match="older than"):
            usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 0

    def test_stale_inside_the_margin_refuses_without_refresh(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=56.0, age=120))  # 60 - 5 = 55 is the last eligible value
        with pytest.raises(usage_gate.UsageGateRefused):
            usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 0

    def test_five_hour_inside_the_margin_refuses_without_refresh(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=10.0, five=66.0, age=120))
        with pytest.raises(usage_gate.UsageGateRefused):
            usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 0

    def test_stale_with_last_50_refreshes_once_then_passes_on_the_fresh_value(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        refresh["next"] = codex_reading(weekly=52.0, five=8.0, age=0)
        g = usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 1 and g["passed"] and g["weekly_used_pct"] == 52.0
        r = g["refresh"]
        assert r["reason"] == "stale" and r["before"]["weekly_used_pct"] == 50.0
        assert r["after"]["weekly_used_pct"] == 52.0 and r["refreshed_at"].startswith("2026-10-05")
        assert (root / "refresh_state.json").exists()

    def test_exactly_threshold_minus_margin_is_eligible(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=55.0, five=65.0, age=120))
        refresh["next"] = codex_reading(weekly=55.0, age=0)
        assert usage_gate.gate_reading("codex", root, now=NOW)["refresh"]

    def test_refresh_that_shows_over_the_limit_refuses(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        refresh["next"] = codex_reading(weekly=61.0, age=0)
        with pytest.raises(usage_gate.UsageGateRefused, match=r"weekly.*61.*60"):
            usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 1

    def test_refresh_that_leaves_the_reading_stale_refuses(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        refresh["next"] = codex_reading(weekly=50.0, age=120)
        with pytest.raises(usage_gate.UsageGateRefused, match="still stale"):
            usage_gate.gate_reading("codex", root, now=NOW)

    def test_a_failed_refresh_call_refuses_and_still_counts(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        refresh["next"] = "boom"
        with pytest.raises(usage_gate.UsageGateRefused, match="refresh call failed"):
            usage_gate.gate_reading("codex", root, now=NOW)
        with pytest.raises(usage_gate.UsageGateRefused, match="already ran"):
            usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 1

    def test_window_reset_since_the_reading_refreshes_even_from_64(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=64.0, age=5, weekly_resets_in_h=-1))
        refresh["next"] = codex_reading(weekly=2.0, age=0)
        g = usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 1 and g["weekly_used_pct"] == 2.0
        assert g["refresh"]["reason"] == "weekly window reset" and g["refresh"]["before"]["weekly_used_pct"] == 64.0

    def test_a_second_refresh_inside_the_interval_refuses(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        refresh["next"] = codex_reading(weekly=50.0, age=0)
        usage_gate.gate_reading("codex", root, now=NOW)
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        with pytest.raises(usage_gate.UsageGateRefused, match="at most one per 60 min"):
            usage_gate.gate_reading("codex", root, now=NOW + dt.timedelta(minutes=59))
        assert refresh["calls"] == 1

    def test_a_refresh_is_allowed_again_after_the_interval(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        refresh["next"] = codex_reading(weekly=50.0, age=0)
        usage_gate.gate_reading("codex", root, now=NOW)
        later = NOW + dt.timedelta(minutes=61)
        monkeypatch.setattr(usage_gate, "_now", lambda: later)
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120 + 61))
        refresh["next"] = {
            **codex_reading(weekly=50.0, age=0),
            "latest_rate_limits": {**codex_reading(weekly=50.0, age=0)["latest_rate_limits"], "observed_at": later.isoformat()},
        }
        usage_gate.gate_reading("codex", root, now=later)
        assert refresh["calls"] == 2

    def test_the_interval_comes_from_config(self, root, monkeypatch, refresh):
        cfg = yaml.safe_load((root / "configs" / "usage_gate.yaml").read_text())
        cfg["codex"]["refresh_min_interval_min"] = 5
        (root / "configs" / "usage_gate.yaml").write_text(yaml.safe_dump(cfg))
        (root / "refresh_state.json").write_text(json.dumps({"last_refresh_at": iso(6)}))
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        refresh["next"] = codex_reading(weekly=50.0, age=0)
        assert usage_gate.gate_reading("codex", root, now=NOW)["refresh"]

    def test_corrupt_state_file_refuses_without_refresh(self, root, monkeypatch, refresh):
        (root / "refresh_state.json").write_text("{nope")
        _codex(monkeypatch, codex_reading(weekly=50.0, age=120))
        with pytest.raises(usage_gate.UsageGateRefused, match="unreadable"):
            usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 0

    @pytest.mark.parametrize(
        "reading",
        [
            codex_reading(weekly=10.0, age=120, verified=False),
            {"verified": True, "latest_rate_limits": None},
            codex_reading(weekly=None, five=1.0, age=120),
            codex_reading(weekly=10.0, age=-30),
        ],
        ids=["unverified", "no-reading", "missing-percentage", "future"],
    )
    def test_never_refreshes_on_an_unverified_or_corrupt_reading(self, root, monkeypatch, refresh, reading):
        _codex(monkeypatch, reading)
        with pytest.raises(usage_gate.UsageGateRefused):
            usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 0

    def test_a_fresh_passing_reading_never_refreshes(self, root, monkeypatch, refresh):
        _codex(monkeypatch, codex_reading(weekly=10.0, age=5))
        assert "refresh" not in usage_gate.gate_reading("codex", root, now=NOW)
        assert refresh["calls"] == 0

    def test_claude_is_never_refreshed(self, root, monkeypatch, refresh):
        claude_file(root, weekly=10.0, age=120)
        with pytest.raises(usage_gate.UsageGateRefused, match="older than 30 min"):
            usage_gate.gate_reading("claude", root, now=NOW)
        assert refresh["calls"] == 0


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

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -50.0, -0.1, 100.1, 250.0])
    @pytest.mark.parametrize("field", ["weekly", "five", "both"])
    def test_non_finite_negative_or_over_100_refuses(self, root, field, bad):
        vals = {"weekly": 1.0, "five": 1.0}
        for k in ("weekly", "five") if field == "both" else (field,):
            vals[k] = bad
        claude_file(root, **vals)
        with pytest.raises(usage_gate.UsageGateRefused, match="missing a weekly or five-hour percentage"):
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


class TestOpusM4Budget:
    GATE = {"five_hour_used_pct": 20.0}

    def test_each_call_is_counted_before_it_is_made_and_the_cap_refuses(self, root):
        for n in (1, 2):
            c = usage_gate.claim_opus_call(root, "arm-a", self.GATE)
            assert c["arm"] == "arm-a" and c["call_number"] == n and c["call_cap"] == 2
        with pytest.raises(usage_gate.UsageGateRefused, match=r"cap for arm 'arm-a' is spent \(2 of 2\)"):
            usage_gate.claim_opus_call(root, "arm-a", self.GATE)
        assert json.loads((root / "opus_m4_calls.json").read_text()) == {"arm-a": 2}

    def test_the_cap_is_per_arm(self, root):
        for _ in range(2):
            usage_gate.claim_opus_call(root, "arm-a", self.GATE)
        assert usage_gate.claim_opus_call(root, "arm-c", self.GATE)["call_number"] == 1

    @pytest.mark.parametrize("arm", [None, "", "arm-x", 3, "ARM-A"])
    def test_an_arm_without_a_pre_registered_cap_is_refused_and_nothing_is_counted(self, root, arm):
        with pytest.raises(usage_gate.UsageGateRefused, match="m4_arm"):
            usage_gate.claim_opus_call(root, arm, self.GATE)
        assert not (root / "opus_m4_calls.json").exists()

    def test_five_hour_window_above_the_pause_line_refuses_without_using_budget(self, root):
        with pytest.raises(usage_gate.UsageGateRefused, match="above the M4 pause line"):
            usage_gate.claim_opus_call(root, "arm-a", {"five_hour_used_pct": 70.1})
        assert not (root / "opus_m4_calls.json").exists()
        assert usage_gate.claim_opus_call(root, "arm-a", {"five_hour_used_pct": 70.0})["call_number"] == 1

    @pytest.mark.parametrize("five", [None, float("nan"), -1.0, 101.0, "9", True])
    def test_a_missing_or_malformed_five_hour_reading_refuses(self, root, five):
        with pytest.raises(usage_gate.UsageGateRefused, match="usable five-hour reading"):
            usage_gate.claim_opus_call(root, "arm-a", {"five_hour_used_pct": five})

    @pytest.mark.parametrize("raw", ["{not json", "[1]", '{"arm-a": -1}', '{"arm-a": "2"}', '{"arm-a": true}'])
    def test_an_unreadable_counter_refuses(self, root, raw):
        (root / "opus_m4_calls.json").write_text(raw)
        with pytest.raises(usage_gate.UsageGateRefused, match="counter"):
            usage_gate.claim_opus_call(root, "arm-a", self.GATE)

    def test_a_bad_cap_or_missing_section_refuses(self, root):
        cfg = yaml.safe_load((root / "configs" / "usage_gate.yaml").read_text())
        cfg["opus_m4"]["call_cap_per_arm"]["arm-a"] = "many"
        (root / "configs" / "usage_gate.yaml").write_text(yaml.safe_dump(cfg))
        with pytest.raises(usage_gate.UsageGateRefused, match="non-negative integer"):
            usage_gate.claim_opus_call(root, "arm-a", self.GATE)
        del cfg["opus_m4"]
        (root / "configs" / "usage_gate.yaml").write_text(yaml.safe_dump(cfg))
        with pytest.raises(usage_gate.UsageGateRefused, match="opus_m4"):
            usage_gate.claim_opus_call(root, "arm-a", self.GATE)

    def test_the_shipped_config_lists_only_the_registered_arms(self):
        """Phase 1 (10 + 500), the #306 B-opus arm (650 = 625 effective calls + the 25-call probe) and the
        G-opus arm (152 = 127 scored calls + the 25-call probe). Phase 2 is NOT listed."""
        cfg = yaml.safe_load((REPO / "configs" / "usage_gate.yaml").read_text())
        assert cfg["opus_m4"]["call_cap_per_arm"] == {
            "m4-phase1-config-a": 10,
            "m4-phase1-config-c": 500,
            "m4-306-b-opus": 650,
            "m4-306-g-opus": 152,
        }
        assert cfg["opus_m4"]["five_hour_pause_pct"] == 70

    def test_the_306_b_opus_arm_is_claimable_up_to_its_cap_and_refused_beyond(self, tmp_path):
        """The registered arm works through claim_opus_call on a copy of the shipped config; an unregistered arm is refused."""
        cfg = yaml.safe_load((REPO / "configs" / "usage_gate.yaml").read_text())
        cfg["opus_m4"]["counter_file"] = str(tmp_path / "opus.json")
        (tmp_path / "configs").mkdir()
        (tmp_path / "configs" / "usage_gate.yaml").write_text(yaml.safe_dump(cfg))
        gate = {"five_hour_used_pct": 10.0}
        first = usage_gate.claim_opus_call(tmp_path, "m4-306-b-opus", gate)
        assert first["call_number"] == 1 and first["call_cap"] == 650
        with pytest.raises(usage_gate.UsageGateRefused):
            usage_gate.claim_opus_call(tmp_path, "m4-306-b-opus-typo", gate)


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
        assert cfg["codex"]["refresh_margin_pp"] == 5 and cfg["codex"]["refresh_min_interval_min"] == 60
        assert cfg["claude"]["seat_key"] == "Claude-Axismeru"  # seat 2 in claude_usage.json (seat-a)


class TestAskVendorIsGated:
    @pytest.fixture(autouse=True)
    def _slim(self, tmp_path, monkeypatch):
        home = tmp_path / "loop"
        home.mkdir()
        (home / ".credentials.json").write_text("{}")
        monkeypatch.setenv("PRAVRUDHI_CLAUDE_CLI_CONFIG_DIR", str(home))
        monkeypatch.setattr(panel, "_claude_auth_email", lambda env: panel.claude_cli_expected_email())
        # The readings below are built relative to the fixed NOW; the gate must judge them at that same instant, never at the
        # real clock (a real clock past NOW + max_age makes the stub reading stale, and the gate's own refresh probe is then
        # the one recorded call: 7 Oct, the red main CI).
        monkeypatch.setattr(usage_gate, "_now", lambda: NOW)

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
            base = panel.VENDORS["codex-cli"]
            panel.ask_vendor(replace(base, params={**base.params, "codex_model": "gpt-x-1"}), "q", root=root)
        assert calls == []

    def test_codex_stale_reading_makes_only_the_gates_own_refresh_probe_never_the_question(self, root, monkeypatch):
        # under both limits, 10 h old at NOW (max 60 min): only staleness can refuse it
        _codex(monkeypatch, codex_reading(weekly=20.0, age=600))
        calls = self._stub_run(monkeypatch, "x")
        with pytest.raises(usage_gate.UsageGateRefused):
            base = panel.VENDORS["codex-cli"]
            panel.ask_vendor(replace(base, params={**base.params, "codex_model": "gpt-x-1"}), "q", root=root)
        assert all("-m" not in c for c in calls), calls   # the question call pins a model with -m; none was made
        assert len(calls) == 1                            # exactly the gate's own refresh probe ran

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
        base = panel.VENDORS["codex-cli"]
        vendor = replace(base, params={**base.params, "codex_model": "gpt-x-1"})
        prompts = [{"id": f"p{i}", "prompt": "q"} for i in range(3)]
        monkeypatch.chdir(root)
        with pytest.raises(usage_gate.UsageGateRefused):
            panel.run_panel(tmp_path / "out", prompts, [vendor])
        rows = (tmp_path / "out" / "panel" / "answers.jsonl").read_text().splitlines()
        assert rows == []

    def test_openai_compat_vendors_are_not_gated(self, root):
        assert usage_gate.GATED_KINDS == ("codex", "claude")
