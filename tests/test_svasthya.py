"""CPU-only tests for `application/svasthya.py` against design doc §5.1's survival contract."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import svasthya

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _pass_all_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every built-in check report ok, so `assess()` reduces to `ready`."""
    monkeypatch.setattr(svasthya, "_check_health_endpoints", lambda: svasthya.CheckResult("health_endpoints", True, "ok"))
    monkeypatch.setattr(svasthya, "_check_request_capture", lambda root: svasthya.CheckResult("request_capture", True, "ok"))
    monkeypatch.setattr(
        svasthya, "_check_scheduler_fresh", lambda root, cfg, now: svasthya.CheckResult("scheduler_fresh", True, "ok")
    )
    monkeypatch.setattr(svasthya, "_check_disk", lambda root, cfg: svasthya.CheckResult("disk_available", True, "ok"))
    monkeypatch.setattr(
        svasthya, "_check_processes_reaped", lambda root, cfg, now: svasthya.CheckResult("processes_reaped", True, "ok")
    )
    monkeypatch.setattr(svasthya, "_check_ledger_integrity", lambda root: svasthya.CheckResult("ledger_integrity", True, "ok"))
    monkeypatch.setattr(
        svasthya, "_check_release_restorable", lambda root: svasthya.CheckResult("release_restorable", True, "ok")
    )
    monkeypatch.setattr(svasthya, "_check_route_fallback", lambda root, cfg: svasthya.CheckResult("route_fallback", True, "ok"))


# --------------------------------------------------------------------------------------------------------------
# assess(): ready / degraded / integrity_halt


def test_ready_when_every_check_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    health = svasthya.assess(tmp_path, now=NOW)
    assert health.state is svasthya.HealthState.READY
    assert all(c.ok for c in health.checks)


def test_degraded_on_a_failing_noncritical_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    monkeypatch.setattr(svasthya, "_check_disk", lambda root, cfg: svasthya.CheckResult("disk_available", False, "full"))
    health = svasthya.assess(tmp_path, now=NOW)
    assert health.state is svasthya.HealthState.DEGRADED
    assert "disk_available" in health.reason


def test_integrity_halt_overrides_a_noncritical_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    monkeypatch.setattr(svasthya, "_check_disk", lambda root, cfg: svasthya.CheckResult("disk_available", False, "full"))
    monkeypatch.setattr(
        svasthya, "_check_ledger_integrity",
        lambda root: svasthya.CheckResult("ledger_integrity", False, "chain broken", integrity=True),
    )
    health = svasthya.assess(tmp_path, now=NOW)
    assert health.state is svasthya.HealthState.INTEGRITY_HALT
    assert "ledger_integrity" in health.reason


def test_integrity_halt_permits_reading_and_refuses_new_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    monkeypatch.setattr(
        svasthya, "_check_ledger_integrity",
        lambda root: svasthya.CheckResult("ledger_integrity", False, "chain broken", integrity=True),
    )
    health = svasthya.assess(tmp_path, now=NOW)
    # Reading (assess itself, and reading the resulting Health) always works.
    assert health.state is svasthya.HealthState.INTEGRITY_HALT
    assert health.to_dict()["state"] == "integrity_halt"
    # New work is refused.
    assert svasthya.can_dispatch_new_work(health) is False
    with pytest.raises(svasthya.SurvivalPolicyError):
        svasthya.repair(tmp_path, "disk_available", lambda: True, now=NOW)


def test_integrity_halt_repair_allowed_when_explicitly_authorized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    box = {"broken": True}

    def ledger_check(root: Path) -> svasthya.CheckResult:
        return svasthya.CheckResult("ledger_integrity", not box["broken"], "ok", integrity=box["broken"])

    monkeypatch.setattr(svasthya, "_check_ledger_integrity", ledger_check)

    def runner() -> bool:
        box["broken"] = False
        return True

    result = svasthya.repair(tmp_path, "ledger_integrity", runner, now=NOW, authorized=True)
    assert result.fixed is True
    assert result.health.state is svasthya.HealthState.READY


# --------------------------------------------------------------------------------------------------------------
# repair(): recovering, ready-only-after-recheck, backoff on a failed fix


def test_repair_enters_recovering_while_the_runner_is_executing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    box = {"free": 0}
    monkeypatch.setattr(svasthya, "_check_disk", lambda root, cfg: svasthya.CheckResult("disk_available", box["free"] > 0, "d"))

    observed: list[svasthya.HealthState] = []

    def runner() -> bool:
        observed.append(svasthya._load_control(tmp_path).state)
        box["free"] = 1
        return True

    result = svasthya.repair(tmp_path, "disk_available", runner, now=NOW)
    assert observed == [svasthya.HealthState.RECOVERING]
    assert result.fixed is True
    assert result.health.state is svasthya.HealthState.READY


def test_repair_ignores_a_runner_claim_that_does_not_fix_the_predicate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    monkeypatch.setattr(svasthya, "_check_disk", lambda root, cfg: svasthya.CheckResult("disk_available", False, "full"))

    result = svasthya.repair(tmp_path, "disk_available", lambda: True, now=NOW)
    assert result.fixed is False
    assert result.health.state is svasthya.HealthState.DEGRADED
    assert result.backoff_until is not None


def test_failed_repair_returns_to_degraded_with_backoff_that_blocks_immediate_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _pass_all_checks(monkeypatch)
    monkeypatch.setattr(svasthya, "_check_disk", lambda root, cfg: svasthya.CheckResult("disk_available", False, "full"))
    calls = {"n": 0}

    def runner() -> bool:
        calls["n"] += 1
        return False

    first = svasthya.repair(tmp_path, "disk_available", runner, now=NOW)
    assert first.fixed is False
    assert calls["n"] == 1

    # Retrying immediately, before backoff_until, must not call the runner again.
    second = svasthya.repair(tmp_path, "disk_available", runner, now=NOW + timedelta(seconds=1))
    assert second.fixed is False
    assert calls["n"] == 1
    assert "backing off" in second.detail

    # Once backoff has elapsed, the runner is invoked again.
    later = svasthya._parse_iso(first.backoff_until) + timedelta(seconds=1)  # type: ignore[arg-type]
    third = svasthya.repair(tmp_path, "disk_available", runner, now=later)
    assert calls["n"] == 2
    assert third.fixed is False


def test_repair_of_an_already_passing_check_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    calls = {"n": 0}

    def runner() -> bool:
        calls["n"] += 1
        return True

    result = svasthya.repair(tmp_path, "disk_available", runner, now=NOW)
    assert result.fixed is True
    assert calls["n"] == 0


def test_repair_of_an_unknown_check_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    with pytest.raises(ValueError):
        svasthya.repair(tmp_path, "not_a_real_check", lambda: True, now=NOW)


# --------------------------------------------------------------------------------------------------------------
# paused: absorbing, survives a reload, only resume() reevaluates


def test_stop_enters_paused_regardless_of_check_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    health = svasthya.stop(tmp_path, reason="operator stop", now=NOW)
    assert health.state is svasthya.HealthState.PAUSED


def test_paused_is_absorbing_even_when_every_check_would_now_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    svasthya.stop(tmp_path, now=NOW)
    later = svasthya.assess(tmp_path, now=NOW + timedelta(hours=1))
    assert later.state is svasthya.HealthState.PAUSED
    assert later.checks == ()


def test_paused_survives_a_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    svasthya.stop(tmp_path, now=NOW)
    # A "reload" is a fresh call chain against the same root with no in-memory state carried over: `assess`
    # only ever reads `.pravrudhi/svasthya/control.json`, so this is exactly what a process restart sees.
    reloaded = svasthya.assess(tmp_path, now=NOW + timedelta(days=1))
    assert reloaded.state is svasthya.HealthState.PAUSED


def test_repair_refuses_while_paused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    svasthya.stop(tmp_path, now=NOW)
    with pytest.raises(svasthya.SurvivalPolicyError):
        svasthya.repair(tmp_path, "disk_available", lambda: True, now=NOW)


def test_resume_reevaluates_health_from_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    svasthya.stop(tmp_path, now=NOW)
    resumed = svasthya.resume(tmp_path, now=NOW + timedelta(minutes=5))
    assert resumed.state is svasthya.HealthState.READY

    # A failing check discovered on resume is reported honestly, not hidden behind an assumed "ready".
    svasthya.stop(tmp_path, now=NOW)
    monkeypatch.setattr(svasthya, "_check_disk", lambda root, cfg: svasthya.CheckResult("disk_available", False, "full"))
    resumed_degraded = svasthya.resume(tmp_path, now=NOW + timedelta(minutes=10))
    assert resumed_degraded.state is svasthya.HealthState.DEGRADED


def test_resume_when_not_paused_is_a_plain_reassessment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pass_all_checks(monkeypatch)
    health = svasthya.resume(tmp_path, now=NOW)
    assert health.state is svasthya.HealthState.READY


# --------------------------------------------------------------------------------------------------------------
# The forbidden actions: each refused by name, unconditionally.


FORBIDDEN_ACTIONS = (
    ("rechain_ledger", svasthya.rechain_ledger),
    ("buy_credits", svasthya.buy_credits),
    ("create_account", svasthya.create_account),
    ("replicate_to_host", svasthya.replicate_to_host),
    ("relaunch_after_stop", svasthya.relaunch_after_stop),
    ("hide_process", svasthya.hide_process),
    ("change_credentials", svasthya.change_credentials),
)


@pytest.mark.parametrize("name,fn", FORBIDDEN_ACTIONS, ids=[n for n, _ in FORBIDDEN_ACTIONS])
def test_forbidden_action_is_refused_by_name(name: str, fn: Any) -> None:
    with pytest.raises(svasthya.SurvivalPolicyError) as excinfo:
        fn()
    assert name in str(excinfo.value)


# --------------------------------------------------------------------------------------------------------------
# Individual checks, exercised for real rather than through a monkeypatched stand-in.


def test_check_disk_available_real_filesystem(tmp_path: Path) -> None:
    cfg = svasthya.SurvivalConfig(min_free_disk_bytes=1)
    result = svasthya._check_disk(tmp_path, cfg)
    assert result.ok is True

    cfg_impossible = svasthya.SurvivalConfig(min_free_disk_bytes=2**62)
    result_impossible = svasthya._check_disk(tmp_path, cfg_impossible)
    assert result_impossible.ok is False


def test_check_health_endpoints_reflects_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> Any:
        raise ImportError("no such module")

    monkeypatch.setattr(svasthya, "_import_app_module", boom)
    result = svasthya._check_health_endpoints()
    assert result.ok is False
    assert "no such module" in result.detail


def test_check_health_endpoints_passes_for_the_real_app_module() -> None:
    result = svasthya._check_health_endpoints()
    assert result.ok is True


def test_check_request_capture_real_store(tmp_path: Path) -> None:
    result = svasthya._check_request_capture(tmp_path)
    assert result.ok is True


def test_check_scheduler_fresh_no_heartbeat_yet(tmp_path: Path) -> None:
    cfg = svasthya.load_config()
    result = svasthya._check_scheduler_fresh(tmp_path, cfg, NOW)
    assert result.ok is False
    assert "no heartbeat" in result.detail


def test_check_scheduler_fresh_and_stale(tmp_path: Path) -> None:
    log = tmp_path / ".pravrudhi" / "heartbeat.jsonl"
    log.parent.mkdir(parents=True)
    record: dict[str, Any] = {
        "at": "2026-01-01T00:00:00Z", "looked_at": [], "chose": None, "reason": "nothing to do",
        "result": None, "drive": None, "drive_deficit": None, "sentence": "",
    }
    log.write_text(json.dumps(record) + "\n")
    cfg = svasthya.SurvivalConfig(scheduler_max_stale_s=3600.0)

    fresh = svasthya._check_scheduler_fresh(tmp_path, cfg, datetime(2026, 1, 1, 0, 30, tzinfo=UTC))
    assert fresh.ok is True

    stale = svasthya._check_scheduler_fresh(tmp_path, cfg, datetime(2026, 1, 1, 3, 0, tzinfo=UTC))
    assert stale.ok is False


def test_check_release_restorable(tmp_path: Path) -> None:
    releases = tmp_path / ".pravrudhi" / "releases"
    releases.mkdir(parents=True)
    missing = svasthya._check_release_restorable(tmp_path)
    assert missing.ok is False

    target = releases / "0.1.0"
    target.mkdir()
    (releases / "current").symlink_to(target, target_is_directory=True)
    present = svasthya._check_release_restorable(tmp_path)
    assert present.ok is True


def test_check_route_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.agents import registry as agents_registry
    from pravrudhi.application import availability

    cfg = svasthya.SurvivalConfig(min_independent_routes=2, independent_route_names=("codex", "hosted"))

    def statuses_two_ready(root: Path) -> list[agents_registry.AgentStatus]:
        return [
            agents_registry.AgentStatus("codex", True, "ready"),
            agents_registry.AgentStatus("hosted", True, "ready"),
            agents_registry.AgentStatus("claude-code", True, "ready"),  # installed only; not in the config allowlist
        ]

    monkeypatch.setattr(agents_registry, "survey", statuses_two_ready)
    monkeypatch.setattr(availability, "cooling", lambda root, now=None: {})
    ok = svasthya._check_route_fallback(tmp_path, cfg)
    assert ok.ok is True

    def statuses_one_ready(root: Path) -> list[agents_registry.AgentStatus]:
        return [agents_registry.AgentStatus("codex", True, "ready"), agents_registry.AgentStatus("hosted", False, "no quota")]

    monkeypatch.setattr(agents_registry, "survey", statuses_one_ready)
    not_enough = svasthya._check_route_fallback(tmp_path, cfg)
    assert not_enough.ok is False

    # A cooling-down route does not count, even if reported available.
    monkeypatch.setattr(agents_registry, "survey", statuses_two_ready)
    monkeypatch.setattr(availability, "cooling", lambda root, now=None: {"hosted": "2099-01-01T00:00:00Z"})
    cooling = svasthya._check_route_fallback(tmp_path, cfg)
    assert cooling.ok is False


def test_check_ledger_integrity_missing_ledger(tmp_path: Path) -> None:
    result = svasthya._check_ledger_integrity(tmp_path)
    assert result.ok is False
    assert result.integrity is False


class _FakeVerifyResult:
    def __init__(self, ok: bool, first_bad_seq: int | None, reason: str | None) -> None:
        self.ok = ok
        self.first_bad_seq = first_bad_seq
        self.reason = reason


def test_check_ledger_integrity_chain_broken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"seq": 0}\n')
    monkeypatch.setattr(svasthya, "kernel_verify", lambda path: _FakeVerifyResult(False, 3, "prev_hash mismatch"))
    result = svasthya._check_ledger_integrity(tmp_path)
    assert result.ok is False
    assert result.integrity is True
    assert "seq 3" in result.detail


class _FakeReplayState:
    def __init__(self, seq: int, ledger_head: str) -> None:
        self.seq = seq
        self.ledger_head = ledger_head


def test_check_ledger_integrity_state_diverges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"seq": 0}\n')
    state_path = tmp_path / "research" / "state.json"
    state_path.write_text(json.dumps({"seq": 5, "ledger_head": "deadbeef"}))

    monkeypatch.setattr(svasthya, "kernel_verify", lambda path: _FakeVerifyResult(True, None, None))
    monkeypatch.setattr(svasthya, "kernel_replay", lambda path: _FakeReplayState(9, "different-head"))
    monkeypatch.setattr(svasthya, "state_bytes", lambda state: "not-what-is-on-disk")
    monkeypatch.setattr(svasthya, "_hash_at", lambda ledger, seq: "not-deadbeef")

    result = svasthya._check_ledger_integrity(tmp_path)
    assert result.ok is False
    assert result.integrity is True
    assert not state_path.read_text().startswith('{"seq": 9')  # read-only: never rewrites state.json


def test_check_ledger_integrity_state_merely_stale_is_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ledger = tmp_path / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"seq": 0}\n')
    state_path = tmp_path / "research" / "state.json"
    state_path.write_text(json.dumps({"seq": 2, "ledger_head": "deadbeef"}))

    monkeypatch.setattr(svasthya, "kernel_verify", lambda path: _FakeVerifyResult(True, None, None))
    monkeypatch.setattr(svasthya, "kernel_replay", lambda path: _FakeReplayState(9, "new-head"))
    monkeypatch.setattr(svasthya, "state_bytes", lambda state: "not-what-is-on-disk")
    monkeypatch.setattr(svasthya, "_hash_at", lambda ledger, seq: "deadbeef")

    result = svasthya._check_ledger_integrity(tmp_path)
    assert result.ok is True
    assert result.integrity is False


# --------------------------------------------------------------------------------------------------------------
# Owned-process tracking, exercised against a real subprocess.


def test_processes_reaped_flags_a_long_running_owned_process(tmp_path: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        cfg = svasthya.SurvivalConfig(process_max_lifetime_s=1.0)
        svasthya.track_process(tmp_path, proc.pid, label="test-child", now=NOW)

        fresh = svasthya._check_processes_reaped(tmp_path, cfg, NOW)
        assert fresh.ok is True  # not yet overdue

        overdue = svasthya._check_processes_reaped(tmp_path, cfg, NOW + timedelta(seconds=10))
        assert overdue.ok is False
        assert str(proc.pid) in overdue.detail
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    # Once the process has actually exited, it no longer counts as unreaped, even if still tracked.
    exited = svasthya._check_processes_reaped(tmp_path, cfg, NOW + timedelta(seconds=10))
    assert exited.ok is True

    svasthya.release_process(tmp_path, proc.pid)
    assert svasthya._load_owned_pids(tmp_path) == {}


def test_track_and_release_process_registry(tmp_path: Path) -> None:
    svasthya.track_process(tmp_path, 999999, label="fake", now=NOW)
    assert "999999" in svasthya._load_owned_pids(tmp_path)
    svasthya.release_process(tmp_path, 999999)
    assert svasthya._load_owned_pids(tmp_path) == {}
