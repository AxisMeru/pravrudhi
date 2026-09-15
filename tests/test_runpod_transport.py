"""RunPod pod lifecycle, under the house rules (docs/decisions/RUNPOD-HOUSE-RULES.md): one pod
account-wide, spend ceilings, checkpoint durability. Every test here uses an injected fake HTTP call --
nothing touches the real RunPod API or spends real money."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.hosts.transports import (
    ALLOWED_GPU_VRAM_GB_MAX,
    DAILY_CAP_USD,
    HARD_STOP_SPEND_USD,
    MAX_TOTAL_SPEND_USD,
    PAUSE_AT_SPEND_USD,
    GpuCandidate,
    OnePodGuardViolation,
    PodSpec,
    RunpodError,
    RunpodPodManager,
    append_ledger_entry,
    choose_gpu_type,
    rsync_checkpoint_command,
)


class FakeCall:
    """Records every call it receives and returns a scripted response per (method, path)."""

    def __init__(self, responses: dict[tuple[str, str], tuple[int, dict[str, Any]]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def __call__(self, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, dict[str, Any]]:
        self.calls.append((method, path, body))
        return self.responses.get((method, path), (404, {"error": "not scripted"}))


def _spec(**overrides: Any) -> PodSpec:
    fields: dict[str, Any] = {
        "name": "prabhasa-nyaya-p2", "gpu_type_id": "NVIDIA A40", "image": "runpod/pytorch:2.4.0-py3.11-cuda12.4",
        "gpu_vram_gb": 48.0, "cost_per_hour_usd": 0.49,
    }
    fields.update(overrides)
    return PodSpec(**fields)


class TestPodSpecEnforcesTheVramCeiling:
    def test_a_48gb_card_is_accepted(self) -> None:
        _spec(gpu_vram_gb=48.0)  # must not raise

    def test_an_80gb_card_is_refused_at_construction(self) -> None:
        with pytest.raises(RunpodError, match="exceeds the house-rule ceiling"):
            _spec(gpu_vram_gb=80.0)

    def test_the_ceiling_matches_the_house_rule_constant(self) -> None:
        assert ALLOWED_GPU_VRAM_GB_MAX == 48


class TestEstimatedBuildCost:
    """2026-09-15, reviewer's arm-N preflight: a Mamba-hybrid image needing a CUDA-toolkit build step at
    pod-start costs real minutes, and that cost must be visible in the same figure a per-job go states."""

    def test_a_zero_build_minute_spec_costs_nothing_extra(self) -> None:
        spec = _spec(estimated_build_minutes=0.0, cost_per_hour_usd=0.49)
        assert spec.estimated_build_cost_usd == 0.0

    def test_a_thirty_minute_build_step_is_costed_against_the_hourly_rate(self) -> None:
        spec = _spec(estimated_build_minutes=30.0, cost_per_hour_usd=0.60)
        assert spec.estimated_build_cost_usd == pytest.approx(0.30)


class TestTheOnePodGuard:
    """House rule 2: before create-pod, run list-pods; if anything is listed (running OR stopped), refuse."""

    def test_create_pod_succeeds_when_the_account_has_no_pods(self) -> None:
        call = FakeCall({
            ("GET", "/pods"): (200, {"pods": []}),
            ("POST", "/pods"): (201, {"id": "new-pod-1"}),
        })
        mgr = RunpodPodManager("fake-key", http_call=call)

        result = mgr.create_pod(_spec(), go="lead-msg-123")

        assert result["id"] == "new-pod-1"
        assert ("GET", "/pods") in [(m, p) for m, p, _ in call.calls], "the guard must check list-pods first"

    def test_create_pod_refuses_when_a_running_pod_already_exists(self) -> None:
        call = FakeCall({("GET", "/pods"): (200, {"pods": [{"id": "existing-1", "desiredStatus": "RUNNING"}]})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        with pytest.raises(OnePodGuardViolation, match="existing-1"):
            mgr.create_pod(_spec(), go="lead-msg-123")

        assert not any(m == "POST" and p == "/pods" for m, p, _ in call.calls), "must never reach create"

    def test_create_pod_refuses_when_a_stopped_pod_still_exists(self) -> None:
        """Stopped pods still bill disk and still count against the one-pod limit (house rule 2)."""
        call = FakeCall({("GET", "/pods"): (200, {"pods": [{"id": "existing-2", "desiredStatus": "EXITED"}]})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        with pytest.raises(OnePodGuardViolation, match="existing-2"):
            mgr.create_pod(_spec(), go="lead-msg-123")


class TestCreatePodRequiresAWrittenGo:
    """House rule 7: the lead gives the go per job, in writing. This is not the authorization decision
    itself (that is a person's judgement) - only the mechanical refusal when nothing was supplied."""

    def test_an_empty_go_is_refused_before_even_checking_list_pods(self) -> None:
        call = FakeCall({("GET", "/pods"): (200, {"pods": []})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        with pytest.raises(RunpodError, match="refuses without an explicit go"):
            mgr.create_pod(_spec(), go="")

        assert call.calls == [], "no API call at all without a go, not even list-pods"

    def test_a_whitespace_only_go_is_also_refused(self) -> None:
        mgr = RunpodPodManager("fake-key", http_call=FakeCall({}))
        with pytest.raises(RunpodError, match="refuses without an explicit go"):
            mgr.create_pod(_spec(), go="   ")


class TestChooseGpuType:
    """House rule 3 (operator amendment, 2026-09-15): cheapest in-stock card with >=80% headroom over the
    measured preflight peak, within the ceiling. 24 GB cards are now a legitimate answer."""

    def test_picks_the_cheapest_card_with_enough_headroom(self) -> None:
        candidates = [
            GpuCandidate("RTX 4090", 24.0, 0.34),
            GpuCandidate("L4", 24.0, 0.49),
            GpuCandidate("A40", 48.0, 0.49),
        ]

        chosen = choose_gpu_type(candidates, measured_peak_vram_gb=12.0)

        assert chosen.gpu_type_id == "RTX 4090"

    def test_a_24gb_card_is_a_legitimate_answer_not_just_48gb(self) -> None:
        candidates = [GpuCandidate("RTX 4090", 24.0, 0.34), GpuCandidate("A40", 48.0, 0.49)]
        chosen = choose_gpu_type(candidates, measured_peak_vram_gb=15.0)
        assert chosen.gpu_type_id == "RTX 4090"

    def test_a_card_without_enough_headroom_is_excluded(self) -> None:
        """measured peak must be <= 80% of the card's VRAM - a 24GB card against a 20GB peak (83%) does not
        qualify even though it technically fits."""
        candidates = [GpuCandidate("RTX 4090", 24.0, 0.34), GpuCandidate("A40", 48.0, 0.49)]

        chosen = choose_gpu_type(candidates, measured_peak_vram_gb=20.0)

        assert chosen.gpu_type_id == "A40"

    def test_a_card_above_the_house_rule_ceiling_is_excluded_even_if_cheapest(self) -> None:
        candidates = [GpuCandidate("H100-80GB", 80.0, 0.10), GpuCandidate("A40", 48.0, 0.49)]
        chosen = choose_gpu_type(candidates, measured_peak_vram_gb=20.0)
        assert chosen.gpu_type_id == "A40"

    def test_no_candidate_fitting_raises_rather_than_guessing(self) -> None:
        candidates = [GpuCandidate("A40", 48.0, 0.49)]
        with pytest.raises(RunpodError, match="no candidate card has"):
            choose_gpu_type(candidates, measured_peak_vram_gb=45.0)


class TestDailySpendStatus:
    """House rule 3a (operator amendment): ~$24/day cap across any card mix, surfaced not blocked."""

    def test_below_the_daily_cap_is_not_flagged(self) -> None:
        call = FakeCall({("GET", "/billing?bucketSize=day"): (200, {"records": [{"totalAmount": 5.0}]})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        status = mgr.daily_spend_status()

        assert status["today_spend_usd"] == 5.0
        assert status["daily_cap_usd"] == DAILY_CAP_USD
        assert status["approaching_cap"] is False
        assert status["over_cap"] is False

    def test_approaching_the_daily_cap_is_flagged_not_blocked(self) -> None:
        call = FakeCall({("GET", "/billing?bucketSize=day"): (200, {"records": [{"totalAmount": 20.0}]})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        status = mgr.daily_spend_status()

        assert status["approaching_cap"] is True
        assert status["over_cap"] is False

    def test_over_the_daily_cap_is_flagged(self) -> None:
        call = FakeCall({("GET", "/billing?bucketSize=day"): (200, {"records": [{"totalAmount": 30.0}]})})
        mgr = RunpodPodManager("fake-key", http_call=call)
        assert mgr.daily_spend_status()["over_cap"] is True

    def test_no_records_yet_reads_as_zero_spend(self) -> None:
        call = FakeCall({("GET", "/billing?bucketSize=day"): (200, {"records": []})})
        mgr = RunpodPodManager("fake-key", http_call=call)
        assert mgr.daily_spend_status()["today_spend_usd"] == 0.0


class TestSpendStatusAgainstTheHouseRuleCeilings:
    def test_below_the_pause_threshold_is_clear(self) -> None:
        call = FakeCall({("GET", "/billing"): (200, {"totalSpend": 40.0})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        status = mgr.spend_status()

        assert status["spent_usd"] == 40.0
        assert status["must_pause"] is False
        assert status["hard_stop"] is False
        assert status["remaining_usd"] == pytest.approx(MAX_TOTAL_SPEND_USD - 40.0)

    def test_at_the_pause_threshold_must_pause_is_true(self) -> None:
        call = FakeCall({("GET", "/billing"): (200, {"totalSpend": PAUSE_AT_SPEND_USD})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        assert mgr.spend_status()["must_pause"] is True

    def test_at_the_hard_stop_threshold_hard_stop_is_true(self) -> None:
        call = FakeCall({("GET", "/billing"): (200, {"totalSpend": HARD_STOP_SPEND_USD})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        assert mgr.spend_status()["hard_stop"] is True


class TestPodLifecycleCalls:
    def test_stop_pod_posts_the_stop_action(self) -> None:
        call = FakeCall({("POST", "/pods/pod-1/action"): (200, {"id": "pod-1", "desiredStatus": "EXITED"})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        mgr.stop_pod("pod-1")

        assert call.calls == [("POST", "/pods/pod-1/action", {"action": "stop"})]

    def test_terminate_pod_deletes_it(self) -> None:
        call = FakeCall({("DELETE", "/pods/pod-1"): (200, {})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        mgr.terminate_pod("pod-1")

        assert call.calls == [("DELETE", "/pods/pod-1", None)]

    def test_a_non_200_response_raises_runpod_error(self) -> None:
        call = FakeCall({("GET", "/pods"): (500, {"error": "internal"})})
        mgr = RunpodPodManager("fake-key", http_call=call)

        with pytest.raises(RunpodError, match="HTTP 500"):
            mgr.list_pods()


class TestLedgerAppend:
    """House rule 4: every pod creation, stop and delete is recorded (who, why, gpuTypeId, $/h, start,
    stop, measured cost from get-billing)."""

    def test_appends_one_row_with_the_required_fields(self, tmp_path: Path) -> None:
        ledger = tmp_path / "runpod-ledger.md"

        append_ledger_entry(
            ledger, event="create", pod_id="pod-1", gpu_type_id="NVIDIA A40", cost_per_hour_usd=0.49,
            spent_usd=12.5, why="P2 structure-SFT dry run", actor="agent-for-operator",
        )

        text = ledger.read_text()
        assert "pod-1" in text and "NVIDIA A40" in text and "0.49" in text and "12.5" in text
        assert "P2 structure-SFT dry run" in text and "agent-for-operator" in text

    def test_a_second_entry_appends_rather_than_overwrites(self, tmp_path: Path) -> None:
        ledger = tmp_path / "runpod-ledger.md"
        append_ledger_entry(ledger, event="create", pod_id="pod-1", gpu_type_id="A40",
                             cost_per_hour_usd=0.49, spent_usd=0.0, why="start", actor="agent-for-operator")
        append_ledger_entry(ledger, event="delete", pod_id="pod-1", gpu_type_id="A40",
                             cost_per_hour_usd=0.49, spent_usd=3.2, why="done", actor="agent-for-operator")

        rows = [line for line in ledger.read_text().splitlines() if line.startswith("| pod-1")]
        assert len(rows) == 2


class TestCheckpointRsyncCommand:
    """House rule 16: ship each checkpoint off the pod as it is written, target order (1) network volume,
    (2) the RTX 5090 box via rsync over SSH into
    /home/ss/fusion-project/prabhasa-nyaya/checkpoints/<run>/."""

    def test_the_command_targets_the_fixed_5090_checkpoint_path(self) -> None:
        cmd = rsync_checkpoint_command(
            pod_ssh_target="root@1.2.3.4", pod_ssh_port=22222,
            remote_checkpoint_dir="/workspace/checkpoints/latest", run_id="p2-a40-run1",
        )

        assert cmd[0] == "rsync"
        assert any("/home/ss/fusion-project/prabhasa-nyaya/checkpoints/p2-a40-run1/" in part for part in cmd)
        assert any("root@1.2.3.4" in part for part in cmd)
        assert any("22222" in part for part in cmd)

    def test_a_run_id_with_a_path_separator_is_refused(self) -> None:
        """A run id is a directory component, never a path - refusing '/' or '..' keeps the destination
        pinned under the fixed checkpoint root regardless of what a caller passes."""
        with pytest.raises(RunpodError, match="run_id"):
            rsync_checkpoint_command(
                pod_ssh_target="root@1.2.3.4", pod_ssh_port=22222,
                remote_checkpoint_dir="/workspace/checkpoints/latest", run_id="../escape",
            )
