"""Tests for incumbent adapter hash recording in paired observations (H5 fix)."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pravrudhi.application import execute
from pravrudhi.targets.harness_grammar import BASELINE
from pravrudhi_kernel.sandbox.observe import KernelHashes
from pravrudhi_kernel.stats import Variance


@pytest.mark.parametrize("incumbent_adapter_present", [True, False])
def test_incumbent_observation_hash_recorded(monkeypatch, tmp_path, incumbent_adapter_present):
    """With an incumbent adapter, the incumbent observation's harness hash should be the adapter's
    hash, not the base snapshot hash. harness_parent should also be set."""

    # Setup mock values for hashes
    base_hash = "0" * 64
    adapter_hash = "a" * 64
    candidate_hash = "c" * 64
    hashes_template = KernelHashes(
        items="1" * 64,
        manifest="2" * 64,
        scorer="3" * 64,
        harness=base_hash,
        model="4" * 64,
    )

    # Mock functions
    monkeypatch.setattr(execute, "sequential_boundary", Mock(return_value=SimpleNamespace(
        decision="continue", e_value=1.0, xbar=0.1, halfwidth=0.05, sigma_used=0.5, n=2
    )))
    monkeypatch.setattr(execute, "replay", lambda _: SimpleNamespace(
        candidates={"c-0001": SimpleNamespace(n_obs=0, xs=[])}
    ))
    monkeypatch.setattr(execute, "read_secret", lambda _: b"test")
    monkeypatch.setattr(execute, "draw_rotation", lambda *a, **kw: SimpleNamespace(rotation_id="rotation"))
    monkeypatch.setattr(execute, "record_exposure", lambda *a: None)
    monkeypatch.setattr(execute, "_distinct2", lambda *a: (0.5, 10.0))

    # Track the expected hashes passed to admit_observation
    recorded_hashes = []

    def track_admit(writer, **kwargs):
        expected = kwargs.get("expected")
        # Call model_dump to get the actual hashes dict
        if hasattr(expected, 'model_dump'):
            recorded_hashes.append(expected.model_dump())
        return SimpleNamespace(seq=1), SimpleNamespace(seq=2)

    monkeypatch.setattr(execute, "admit_observation", track_admit)

    # Setup scores and other mocks
    incumbent_scores = {"a": 1, "b": 1, "c": 0}
    candidate_scores = {"a": 1, "b": 0, "c": 0}

    def mock_eval_arm(ctx, rot, seed, adapter, template, tag):
        result = SimpleNamespace(exit_code=0, wall_s=1.0, peak_gib_smi=0.0)
        return tmp_path / tag, result, {
            "items_sha256": hashes_template.items,
            "model_sha256": hashes_template.model
        }

    def mock_score(job_dir, *args):
        if job_dir.name.startswith("inc-"):
            return incumbent_scores, job_dir / "scores"
        return candidate_scores, job_dir / "scores"

    # Mock expected_hashes to return consistent values
    def mock_expected_hashes(*args):
        return hashes_template

    # Mock model_dir_hash
    def mock_model_dir_hash(model_dir):
        if model_dir == tmp_path / "adapter":
            return adapter_hash if incumbent_adapter_present else base_hash
        elif model_dir == tmp_path / "candidate_adapter":
            return candidate_hash
        else:
            return base_hash

    monkeypatch.setattr(execute, "_eval_arm", mock_eval_arm)
    monkeypatch.setattr(execute, "score_job", mock_score)
    monkeypatch.setattr(execute, "expected_hashes", mock_expected_hashes)
    monkeypatch.setattr(execute, "model_dir_hash", mock_model_dir_hash)

    # Setup context
    incumbent_adapter = tmp_path / "adapter" if incumbent_adapter_present else None
    ctx = SimpleNamespace(
        root=tmp_path,
        pool_dir=tmp_path,
        snapshot=tmp_path,
        templates=tmp_path,
        state=SimpleNamespace(isolation="process"),
        night=1,
        variance=Variance(bench="test", sigma_seed=0.5, tau=0.1, delta_min=0.1),
        cfg={"model": "test", "evaluation": {"k_items": 3, "exposure_cap": 10}},
        eval_template="gsm8k_v1",
        train_template="gsm8k_v1",
        answer_kind="numeric",
        incumbent_id="c-0000",
        incumbent_adapter=incumbent_adapter,
        incumbent=BASELINE,
        sealed={},
        log=lambda _: None,
        bucket={"task_family": "test", "target_model": "test", "corpus": "test"},
    )

    # Create a mock recipe
    recipe = SimpleNamespace(
        eval_template="gsm8k_v1",
        strategy="test",
    )

    # Create the writer
    def mock_append(kind, actor, payload, **kwargs):
        return SimpleNamespace(seq=len(recorded_hashes))

    writer = SimpleNamespace(append=mock_append)

    # Run the evaluation
    outcome = execute.evaluate_and_dispose(
        ctx, writer, "c-0001", recipe, tmp_path / "candidate_adapter"
    )
    # Verify the function completed (outcome should be "continue" or "pruned" or "failed")
    assert outcome in ("continue", "pruned", "failed"), f"Unexpected outcome: {outcome}"

    # Should have recorded two observations (incumbent and candidate)
    assert len(recorded_hashes) == 2, f"Expected 2 recorded observations, got {len(recorded_hashes)}"

    incumbent_obs_hashes = recorded_hashes[0]
    candidate_obs_hashes = recorded_hashes[1]

    if incumbent_adapter_present:
        # With incumbent adapter: incumbent hash should be adapter_hash
        assert incumbent_obs_hashes["harness"] == adapter_hash, \
            f"Incumbent harness hash should be {adapter_hash}, got {incumbent_obs_hashes['harness']}"
        # harness_parent should be set to incumbent adapter hash
        assert incumbent_obs_hashes.get("harness_parent") == adapter_hash, \
            f"Incumbent harness_parent should be {adapter_hash}, got {incumbent_obs_hashes.get('harness_parent')}"
        # Candidate harness should be candidate_hash
        assert candidate_obs_hashes["harness"] == candidate_hash, \
            f"Candidate harness hash should be {candidate_hash}, got {candidate_obs_hashes['harness']}"
        # Candidate harness_parent should also be set to incumbent adapter hash
        assert candidate_obs_hashes.get("harness_parent") == adapter_hash, \
            f"Candidate harness_parent should be {adapter_hash}, got {candidate_obs_hashes.get('harness_parent')}"
    else:
        # Without incumbent adapter: hashes should be base_hash, no harness_parent
        assert incumbent_obs_hashes["harness"] == base_hash, \
            f"Incumbent harness hash should be {base_hash}, got {incumbent_obs_hashes['harness']}"
        assert incumbent_obs_hashes.get("harness_parent") is None, \
            f"Incumbent harness_parent should be None, got {incumbent_obs_hashes.get('harness_parent')}"
        assert candidate_obs_hashes["harness"] == candidate_hash, \
            f"Candidate harness hash should be {candidate_hash}, got {candidate_obs_hashes['harness']}"
        assert candidate_obs_hashes.get("harness_parent") is None, \
            f"Candidate harness_parent should be None, got {candidate_obs_hashes.get('harness_parent')}"


def test_rebase_detection_via_kernel_replay(monkeypatch, tmp_path):
    """Test that when harness_parent changes between observations, the kernel detects a rebase.
    This is an integration test that verifies the kernel's replay logic works with our hashes."""

    from pravrudhi_kernel.ledger import LedgerWriter, replay

    # Create a ledger with two observations for the same candidate, with different incumbent adapters
    ledger_path = tmp_path / "test_ledger.jsonl"
    w = LedgerWriter.open(ledger_path, "0.1.0")

    incumbent_hash_1 = "1" * 64
    incumbent_hash_2 = "2" * 64
    candidate_id = "c-0001"

    # Propose a candidate
    w.append(
        "propose",
        "proposer",
        {"op": "patch", "edit_family": "adapter"},
        epoch=0,
        night=1,
        cycle=1,
        candidate_id=candidate_id,
        surface="W3.adapter",
        bucket={"task_family": "test", "target_model": "test", "corpus": "test"},
        provenance="agama",
    )

    # First observation with incumbent_hash_1
    w.append(
        "observe",
        "kernel",
        {
            "run_id": "r-0001-0",
            "seed_index": 0,
            "observed": {"delta_in": 0.05, "n_items": 100, "seeds": [0]},
            "hashes": {"harness_parent": incumbent_hash_1},
            "stats": {"boundary": "continue"},
            "isolation": "container",
            "measure_class": "model-measured",
        },
        epoch=0,
        night=1,
        cycle=1,
        candidate_id=candidate_id,
        surface="W3.adapter",
        bucket={"task_family": "test", "target_model": "test", "corpus": "test"},
        provenance="pratyaksha",
    )

    # Second observation with incumbent_hash_2 (different incumbent)
    w.append(
        "observe",
        "kernel",
        {
            "run_id": "r-0001-1",
            "seed_index": 1,
            "observed": {"delta_in": 0.03, "n_items": 100, "seeds": [1]},
            "hashes": {"harness_parent": incumbent_hash_2},
            "stats": {"boundary": "continue"},
            "isolation": "container",
            "measure_class": "model-measured",
        },
        epoch=0,
        night=1,
        cycle=1,
        candidate_id=candidate_id,
        surface="W3.adapter",
        bucket={"task_family": "test", "target_model": "test", "corpus": "test"},
        provenance="pratyaksha",
    )

    # Replay and check that rebase was detected
    replayed = replay(ledger_path)

    # Find the candidate in replayed state
    candidate_state = replayed.candidates.get(candidate_id)
    assert candidate_state is not None, f"Candidate {candidate_id} not found in replayed state"

    # Check that rebase was detected (xs should be cleared after rebase)
    assert candidate_state.rebased == 1, \
        f"Expected 1 rebase detection, got {candidate_state.rebased}. " \
        f"xs={candidate_state.xs}, incumbent_hash={candidate_state.incumbent_hash}"

    # After rebase, xs should only contain the last delta (second observation)
    # because xs is cleared when rebase is detected
    assert len(candidate_state.xs) == 1, \
        f"After rebase, xs should be cleared and contain only the last delta. Got {candidate_state.xs}"
    assert candidate_state.xs[0] == pytest.approx(0.03), \
        f"After rebase, xs should contain the second delta (0.03), got {candidate_state.xs[0]}"

    # incumbent_hash should be the second one
    assert candidate_state.incumbent_hash == incumbent_hash_2, \
        f"incumbent_hash should be {incumbent_hash_2}, got {candidate_state.incumbent_hash}"
