"""Paired screen telemetry leaves the rate-based boundary and admission intact."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from pravrudhi.application import execute, harness_track
from pravrudhi.targets.harness_grammar import BASELINE
from pravrudhi_kernel.sandbox import admit_observation
from pravrudhi_kernel.sandbox.observe import KernelHashes
from pravrudhi_kernel.stats import Variance, sequential_boundary


@pytest.mark.parametrize("track", ["adapter", "harness"])
@pytest.mark.parametrize("candidate", [[0, 1, 1, 1, 0], [0, 0, 0, 1, 0], [1, 0, 1, 1, 0]])
@pytest.mark.parametrize("history", [[], [0.0] * 5])
def test_paired_discordance_is_recorded_without_changing_decision(monkeypatch, tmp_path, track, candidate, history):
    module = execute if track == "adapter" else harness_track
    incumbent = dict(zip("abcde", [1, 0, 0, 1, 0], strict=True))
    candidate = dict(zip("abcde", candidate, strict=True))
    variance = Variance(bench="test", sigma_seed=0.5, tau=0.1, delta_min=0.1)
    delta = sum(candidate.values()) / len(candidate) - sum(incumbent.values()) / len(incumbent)
    expected = sequential_boundary([*history, delta], variance)
    boundary = Mock(wraps=sequential_boundary)
    monkeypatch.setattr(module, "sequential_boundary", boundary)
    monkeypatch.setattr(
        module, "replay", lambda _: SimpleNamespace(candidates={"c-0001": SimpleNamespace(n_obs=len(history), xs=history)})
    )
    monkeypatch.setattr(module, "read_secret", lambda _: b"test")
    monkeypatch.setattr(module, "draw_rotation", lambda *a, **kw: SimpleNamespace(rotation_id="rotation"))
    monkeypatch.setattr(module, "record_exposure", lambda *a: None)
    hashes = KernelHashes(**dict.fromkeys(["items", "manifest", "scorer", "harness", "model"], "0" * 64))
    result = SimpleNamespace(exit_code=0, wall_s=1.0, peak_gib_smi=0.0)

    def arm(*args):
        tag = args[-1]
        return tmp_path / tag, result, {"items_sha256": hashes.items, "model_sha256": hashes.model}

    def scores(jd):
        return incumbent if jd.name.startswith("inc-") else candidate

    ctx = SimpleNamespace(
        root=tmp_path,
        pool_dir=tmp_path,
        snapshot=tmp_path,
        templates=tmp_path,
        state=SimpleNamespace(isolation="process"),
        night=1,
        variance=variance,
        cfg={"model": "test", "evaluation": {"k_items": 5, "exposure_cap": 10}},
        # A real NightContext derives these from the night config's `answer_kind`; the stub must carry them or
        # it models an object that cannot exist.
        eval_template="gsm8k_v1",
        train_template="gsm8k_v1",
        answer_kind="numeric",
        incumbent_id="c-0000",
        incumbent_adapter=None,
        incumbent=BASELINE,
        sealed={},
        log=lambda _: None,
        bucket={"task_family": "test", "target_model": "test", "corpus": "test"},
    )
    if track == "adapter":
        monkeypatch.setattr(module, "_eval_arm", arm)
        monkeypatch.setattr(module, "score_job", lambda jd, *a: (scores(jd), jd / "scores"))
        monkeypatch.setattr(module, "expected_hashes", lambda *a: hashes)
        monkeypatch.setattr(module, "model_dir_hash", lambda *a: hashes.harness)
        monkeypatch.setattr(module, "_distinct2", lambda *a: (0.5, 10.0))
    else:
        monkeypatch.setattr(module, "run_agent", arm)
        monkeypatch.setattr(module, "score_agent", lambda ctx, jd, rot: (scores(jd), jd / "scores", result))
        monkeypatch.setattr(module, "_hashes", lambda *a: hashes)

    def run(strip_discordance):
        events = []

        def append(kind, actor, payload, **kwargs):
            events.append((kind, actor, payload, kwargs))
            return SimpleNamespace(seq=len(events))

        def admit(writer, **kwargs):
            if strip_discordance:
                kwargs["extra"].pop("discordance", None)
            return admit_observation(writer, **kwargs)

        monkeypatch.setattr(module, "admit_observation", admit)
        writer = SimpleNamespace(append=append)
        if track == "adapter":
            outcome = module.evaluate_and_dispose(
                ctx, writer, "c-0001", SimpleNamespace(eval_template="test", strategy="test"), tmp_path
            )
        else:
            outcome = module._execute_one(ctx, writer, "c-0001", BASELINE, 5)
        return outcome, events

    outcome, events = run(False)
    baseline_outcome, baseline_events = run(True)
    assert outcome == baseline_outcome == ("pruned" if expected.decision == "prune" else "continue")
    assert boundary.call_count == 2
    for call in boundary.call_args_list:
        assert call.args == ([*history, delta], variance)
    observation = next(payload for kind, _, payload, _ in events if kind == "observe" and payload["arm"] == "candidate")
    block = observation.pop("discordance")
    assert type(block) is dict
    assert set(block) == {"n", "concordant", "wins", "losses", "delta", "p_mcnemar", "or_lower", "or_upper"}
    wins = sum(candidate[item] > incumbent[item] for item in incumbent)
    losses = sum(candidate[item] < incumbent[item] for item in incumbent)
    assert block["wins"] == wins
    assert block["losses"] == losses
    assert block["delta"] == pytest.approx((wins - losses) / len(incumbent))
    assert block["n"] == len(incumbent)
    assert block["concordant"] == len(incumbent) - wins - losses
    assert observation["stats"] == {
        key: getattr(expected, "decision" if key == "boundary" else key)
        for key in ["boundary", "e_value", "xbar", "halfwidth", "sigma_used", "n"]
    }
    assert events == baseline_events
