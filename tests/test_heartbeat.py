"""The heartbeat: one wake-up measures the appetite's drives, lets them select a winner, and dispatches the
action wired to that drive."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from pravrudhi.agents.base import AgentRun, Diff
from pravrudhi.application import heartbeat, kshudha, requests
from pravrudhi.application.heartbeat import beat, history, load_config, log_path
from pravrudhi.application.objectives import Benchmark, Objective
from pravrudhi.application.objectives import write as write_objective
from pravrudhi.application.requests import Criterion
from pravrudhi.application.subagents import SubagentRun, record_run, runs
from pravrudhi_kernel.schema import LedgerEvent


def make_objective(oid: str, *, domain: str = "") -> Objective:
    return Objective(
        id=oid,
        intent=f"Improve {oid}.",
        track="model",
        benchmarks=(Benchmark(id="b1", tool="lm-eval", metric=f"{oid}_acc"),),
        domain=domain,
    )


def make_event(seq: int, night: int, payload: dict[str, object], kind: str = "audit") -> str:
    ev = LedgerEvent(
        seq=seq, t="2026-01-01T00:00:00.000Z", epoch=0, night=night, cycle=None, kind=kind,  # type: ignore[arg-type]
        actor="kernel", candidate_id=None, surface=None, bucket=None, provenance=None, kernel_release="0.1.0",
        payload=payload, prev_hash="0" * 64, this_hash="0" * 64,
    )
    return ev.model_dump_json()


def write_ledger(root: Path, lines: list[str]) -> None:
    ledger = root / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_config(root: Path, **overrides: object) -> None:
    path = root / ".pravrudhi" / "heartbeat.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    base = {"interval_min": 60, "max_dispatch_per_beat": 1, "quiet_hours": [], "allow_gpu": False}
    base.update(overrides)
    path.write_text(yaml.safe_dump(base), encoding="utf-8")


class OkAgent:
    """A fake agent that always produces an accepted-shaped diff. See tests/test_subagents.py's OkAgent."""

    name = "fake"

    def __init__(self, files: list[str]):
        self.files = files

    def create_workspace(self, task_id: str, base_ref: str = "HEAD") -> Path:
        return Path(tempfile.mkdtemp())

    def run(self, prompt: str, workspace: Path, timeout_s: int = 60) -> AgentRun:
        return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    def collect_changes(self, workspace: Path) -> Diff:
        return Diff(files=list(self.files))


def ok_dispatch(name: str, model: str | None) -> OkAgent:
    # A path under the real scratch directory a step's task declares (application/subagents.py's `_scratch_dir`),
    # so `TaskSpec.owns` accepts it and this fake agent's "work" is judged in scope rather than rejected as stray.
    return OkAgent(["proposals/obj-a/baseline-evaluation/README.md"])


def dispatch_into(*files: str) -> heartbeat.DispatchFn:
    """A fake agent whose "work" lands exactly at `files`, so it is judged in scope for whichever task declared
    those as its allowed paths (obligations' `proposals/requests/...`, unlike `ok_dispatch`'s objective scratch)."""

    def _dispatch(name: str, model: str | None) -> OkAgent:
        return OkAgent(list(files))

    return _dispatch


def make_drives(**deficits: float) -> list[kshudha.Drive]:
    """A full six-drive reading with a controlled deficit per drive, so `kshudha.select` picks a deterministic
    winner without ever touching this host's real doctor checks, tool catalogue or agent fleet. `pramana_navyata`
    (freshness) stays `unknown`, exactly as it always is in production (kshudha.py has no evidence-freshness
    source), unless the caller overrides it with a deficit of its own."""
    drives: list[kshudha.Drive] = []
    for drive_id in kshudha.DRIVE_IDS:
        if drive_id == "pramana_navyata" and drive_id not in deficits:
            drives.append(kshudha.Drive(
                id=drive_id, wire_name=kshudha.WIRE_NAMES[drive_id], value=None, target=1.0, deficit=None,
                weight=1.0, eligible=False,
                blocked_reason="no evidence-freshness source is wired into the engine yet",
                sources=(), unknown=True,
            ))
            continue
        deficit = deficits.get(drive_id, 0.0)
        drives.append(kshudha.Drive(
            id=drive_id, wire_name=kshudha.WIRE_NAMES[drive_id], value=1.0 - deficit, target=1.0, deficit=deficit,
            weight=1.0, eligible=True, blocked_reason="", sources=(f"{drive_id}={deficit}",), unknown=False,
        ))
    return drives


def sthiti_drive_with_failing_checks(*, deficit: float, failing: tuple[str, ...]) -> kshudha.Drive:
    """A `sthiti` reading with specific failing doctor checks, shaped exactly like `kshudha.sthiti_drive`'s own
    `doctor:<name>=ok|fail` sources — what `_beat_continuity` parses to find the cheapest remedy."""
    all_checks = ("initialised", "ledger", "docker", "pools", "prereg")
    sources = tuple(f"doctor:{name}={'fail' if name in failing else 'ok'}" for name in all_checks)
    return kshudha.Drive(
        id="sthiti", wire_name="continuity", value=1.0 - deficit, target=1.0, deficit=deficit, weight=1.0,
        eligible=True, blocked_reason="", sources=sources, unknown=False,
    )


def patch_measure(monkeypatch: pytest.MonkeyPatch, **deficits: float) -> None:
    """Force `beat`'s measurement step to return a controlled drive reading instead of `kshudha.measure`'s real,
    host-dependent one (doctor checks run real subprocesses; capability reads this host's real tool/agent
    catalogue) — the same dependency-injection trick `dispatch` already plays for the swarm."""
    monkeypatch.setattr(heartbeat.kshudha, "measure", lambda root, config=None: make_drives(**deficits))


def test_load_config_falls_back_to_packaged_defaults(tmp_path):
    config = load_config(tmp_path)
    assert config == load_config(tmp_path)
    assert config.interval_min == 60
    assert config.max_dispatch_per_beat == 1
    assert config.quiet_hours == ()
    assert config.allow_gpu is False


def test_quiet_hours_yield_a_no_op_with_the_reason(tmp_path, monkeypatch):
    write_objective(tmp_path, make_objective("obj-a"))
    set_config(tmp_path, quiet_hours=[3])
    patch_measure(monkeypatch, samarthya=0.9)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 3, 30, tzinfo=UTC))

    assert record.chose is None
    assert record.result is None
    assert "quiet hours" in record.reason
    assert record.looked_at == ()
    assert record.drive is None
    assert record.drive_deficit is None
    assert runs(tmp_path) == []


def test_capability_drive_dispatches_the_next_undone_step_and_records_an_accepted_run(tmp_path, monkeypatch):
    write_objective(tmp_path, make_objective("obj-a"))
    patch_measure(monkeypatch, samarthya=0.9)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.drive == "capability"
    assert record.drive_deficit == pytest.approx(0.9)
    assert record.chose == {"objective": "obj-a", "step": "baseline-evaluation"}
    assert record.result is not None
    assert record.result["accepted"] is True
    assert "dispatched obj-a:baseline-evaluation" in record.reason
    assert "capability" in record.sentence

    on_disk = runs(tmp_path, objective="obj-a")
    assert len(on_disk) == 1
    assert on_disk[0].step == "baseline-evaluation"
    assert on_disk[0].accepted is True


def test_capability_drive_with_no_objectives_is_a_recorded_no_op(tmp_path, monkeypatch):
    patch_measure(monkeypatch, samarthya=0.9)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.chose is None
    assert record.result is None
    assert "no objectives" in record.reason
    assert record.looked_at == ()
    assert record.drive == "capability"

    logged = history(tmp_path, 10)
    assert len(logged) == 1
    assert logged[0].reason == record.reason
    assert log_path(tmp_path).exists()


def test_picks_the_stalest_objective(tmp_path, monkeypatch):
    write_objective(tmp_path, make_objective("obj-old"))
    write_objective(tmp_path, make_objective("obj-new"))
    patch_measure(monkeypatch, samarthya=0.9)

    # Both objectives already have their baseline step accepted, so each one's next undone step is
    # candidate-evaluation; obj-old was dispatched long ago and obj-new only just now, so the stale one wins.
    record_run(tmp_path, SubagentRun(
        objective="obj-old", step="baseline-evaluation", task_id="obj-old:baseline-evaluation", route="fake",
        accepted=True, wall_s=1.0, at="2020-01-01T00:00:00Z",
    ))
    record_run(tmp_path, SubagentRun(
        objective="obj-new", step="baseline-evaluation", task_id="obj-new:baseline-evaluation", route="fake",
        accepted=True, wall_s=1.0, at="2030-01-01T00:00:00Z",
    ))

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.chose == {"objective": "obj-old", "step": "candidate-evaluation"}


def test_honours_max_dispatch_per_beat(tmp_path, monkeypatch):
    write_objective(tmp_path, make_objective("obj-a"))
    set_config(tmp_path, max_dispatch_per_beat=0)
    patch_measure(monkeypatch, samarthya=0.9)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.chose == {"objective": "obj-a", "step": "baseline-evaluation"}
    assert record.result is None
    assert "max_dispatch_per_beat" in record.reason
    assert runs(tmp_path) == []


def test_skips_gpu_steps_while_a_run_is_in_progress(tmp_path, monkeypatch):
    objective = make_objective("legal-domain", domain="legal-domain")
    write_objective(tmp_path, objective)
    set_config(tmp_path, allow_gpu=True)
    patch_measure(monkeypatch, samarthya=0.9)

    # Fast-forward past the evaluate/corpus steps so the next undone step is the finetune step, which needs the GPU.
    for step_id in ("baseline-evaluation", "corpus"):
        record_run(tmp_path, SubagentRun(
            objective=objective.id, step=step_id, task_id=f"{objective.id}:{step_id}", route="fake",
            accepted=True, wall_s=1.0,
        ))
    write_ledger(tmp_path, [make_event(1, 1, {"kind": "night_start", "track": "model"})])

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.chose == {"objective": objective.id, "step": "finetune"}
    assert record.result is None
    assert "GPU" in record.reason and "in progress" in record.reason
    assert len(runs(tmp_path, objective=objective.id)) == 2


def test_obligations_drive_dispatches_the_oldest_unmet_request_criterion(tmp_path, monkeypatch):
    patch_measure(monkeypatch, seva=0.8)
    req = requests.capture(
        tmp_path, "Please add a widget.", request_id="req-1",
        criteria=[Criterion(text="a widget exists", source="operator")],
    )

    record = beat(
        tmp_path, dispatch=dispatch_into("proposals/requests/req-1/0/README.md"),
        now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )

    assert record.drive == "obligations"
    assert record.drive_deficit == pytest.approx(0.8)
    assert record.chose == {"request": req.id, "criterion": "0"}
    assert record.result is not None
    assert record.result["accepted"] is True
    assert f"dispatched request {req.id} criterion 0" in record.reason
    assert (tmp_path / "proposals" / "requests" / "req-1" / "0").exists()


def test_obligations_drive_with_no_unmet_request_is_a_recorded_no_op(tmp_path, monkeypatch):
    patch_measure(monkeypatch, seva=0.8)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.drive == "obligations"
    assert record.chose is None
    assert record.result is None
    # An obligation is not only a criterion still to be built: a request whose criteria all carry evidence is
    # still owed until it has been through the gate. Reporting "no unmet criterion" and stopping was how the
    # loop came to say it had nothing to do while three requests were outstanding.
    assert "nothing is owed" in record.reason


def test_continuity_drive_runs_the_cheapest_failing_checks_remedy(tmp_path, monkeypatch):
    sthiti = sthiti_drive_with_failing_checks(deficit=0.5, failing=("docker", "initialised"))
    drives = [d if d.id != "sthiti" else sthiti for d in make_drives()]
    monkeypatch.setattr(heartbeat.kshudha, "measure", lambda root, config=None: drives)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.drive == "continuity"
    assert record.drive_deficit == pytest.approx(0.5)
    assert record.chose == {"check": "initialised"}
    assert record.result == {
        "check": "initialised", "remedy": "run `pravrudhi init` to create the missing config or ledger",
    }
    assert "initialised" in record.reason


def test_unknown_drive_yields_a_diagnostic_rather_than_an_action(tmp_path, monkeypatch):
    patch_measure(monkeypatch)  # every known drive at deficit 0.0; only freshness is unknown, weight > 0

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.drive == "freshness"
    assert record.drive_deficit is None
    assert record.chose == {"drive": "freshness"}
    assert record.result is not None
    assert record.result["kind"] == "diagnostic"
    assert "no evidence-freshness source is wired into the engine yet" in record.reason
    assert runs(tmp_path) == []


def test_resources_drive_records_its_desire_and_steps_aside(tmp_path, monkeypatch):
    write_objective(tmp_path, make_objective("obj-a"))
    patch_measure(monkeypatch, sadhana=0.9, samarthya=0.4)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    # kshudha.select's own decision stays "resources" — that is the honest, measured winner...
    assert record.drive == "resources"
    assert record.drive_deficit == pytest.approx(0.9)
    # ...but nothing is actually dispatched for it; the beat yields to capability, the next eligible drive.
    assert record.chose == {"objective": "obj-a", "step": "baseline-evaluation"}
    assert record.result is not None
    assert record.result["accepted"] is True
    assert "resources" in record.reason and "yielding to capability" in record.reason


def test_resources_drive_with_no_other_eligible_drive_records_only_the_desire(tmp_path, monkeypatch):
    # pramana_navyata=0.0 makes freshness a known, satisfied drive instead of the default unknown one, so there
    # is truly nothing else for `_next_eligible` to fall back to.
    patch_measure(monkeypatch, sadhana=0.9, pramana_navyata=0.0)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.drive == "resources"
    assert record.chose == {"drive": "resources"}
    assert record.result == {"kind": "desire", "sources": ["sadhana=0.9"]}
    assert runs(tmp_path) == []


def test_a_beat_records_the_drive_and_the_sentence(tmp_path, monkeypatch):
    write_objective(tmp_path, make_objective("obj-a"))
    patch_measure(monkeypatch, samarthya=0.9)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))

    assert record.drive == "capability"
    assert record.drive_deficit == pytest.approx(0.9)
    assert record.sentence
    assert "capability" in record.sentence
    assert "I am working on" in record.sentence

    logged = history(tmp_path, 10)
    assert logged[0].drive == "capability"
    assert logged[0].sentence == record.sentence


def test_hysteresis_persists_across_two_beats(tmp_path, monkeypatch):
    write_objective(tmp_path, make_objective("obj-a"))

    patch_measure(monkeypatch, samarthya=0.8)
    first = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC))
    assert first.drive == "capability"

    state_after_first = kshudha.load_state(tmp_path)
    assert state_after_first.drives["samarthya"].phase == "hungry"
    assert state_after_first.committed == "samarthya"

    # On the second beat capability's own deficit has dropped into the 0.3-0.6 hysteresis band, below the 0.6
    # hungry threshold; without a persisted "hungry" phase this would go straight back to sated and rest, since
    # nothing else has any deficit at all. The persisted state is what keeps it committed instead.
    patch_measure(monkeypatch, samarthya=0.5)
    second = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 13, 0, tzinfo=UTC))

    assert second.drive == "capability"
    assert second.chose == {"objective": "obj-a", "step": "candidate-evaluation"}

    state_after_second = kshudha.load_state(tmp_path)
    assert state_after_second.beat == 2
    assert state_after_second.drives["samarthya"].phase == "hungry"


class TestABeatThatActsRatherThanDescribes:
    """A beat that can only name the next action is not a heartbeat.

    `_beat_obligations` used to answer a fully evidenced request with the command an operator could type and
    `result: None`. Every beat then chose that same request, did nothing, and reported it had chosen. The engine
    idled at night 16 for a day while its own adversarial reviewer sat on a finding nobody had read, because
    nothing ran the gate that would have surfaced it.
    """

    @staticmethod
    def _evidenced_request(root: Path) -> str:
        from pravrudhi.application import requests

        req = requests.capture(root, "make the thing work")
        requests.add_criteria(root, req.id, [requests.Criterion(text="the thing works", source="operator")])
        requests.meet(root, req.id, 0, [requests.Evidence(kind="file", ref="README.md", note="it is there")])
        requests.advance(root, req.id, "in_progress")
        requests.advance(root, req.id, "delivered")
        return req.id

    def test_a_passing_gate_closes_the_request_rather_than_naming_a_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import completion, heartbeat, requests

        (tmp_path / "README.md").write_text("it is there")
        request_id = self._evidenced_request(tmp_path)
        monkeypatch.setattr(
            completion, "gate",
            lambda root, rid, **kw: completion.GateResult(rid, True, "everything re-verified"),
        )

        chose, reason, result = heartbeat._beat_completion_gate(tmp_path, request_id)

        assert result is not None and result["passed"] is True, "the beat reported instead of acting"
        assert requests.get(tmp_path, request_id).state == "verified"
        assert chose == {"request": request_id}
        assert "verified" in reason

    def test_a_refused_gate_sends_the_request_back_to_building_so_the_loop_moves_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The wedge itself. While it sits at `delivered` it reads as "awaiting the gate" and every future beat
        chooses it again; at `in_progress` its unmet work is what the next beat sees."""
        from pravrudhi.application import completion, heartbeat, requests

        (tmp_path / "README.md").write_text("it is there")
        request_id = self._evidenced_request(tmp_path)
        review = completion.ReviewResult(findings="the rival named twice was never assessed", blocking=True,
                                         reason="the review found a reason this does not satisfy the request")
        monkeypatch.setattr(
            completion, "gate",
            lambda root, rid, **kw: completion.GateResult(rid, False, "adversarial review: refused", [], review),
        )

        _chose, _reason, result = heartbeat._beat_completion_gate(tmp_path, request_id)

        assert result is not None and result["passed"] is False
        after = requests.get(tmp_path, request_id)
        assert after.state == "in_progress", "a refused request stayed delivered and will be re-chosen forever"
        assert any("never assessed" in " ".join(n.values()) for n in after.notes), \
            "the reviewer's finding was not kept where the next beat can read it"

    def test_a_gate_that_cannot_run_reports_and_does_not_stop_the_heartbeat(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An unattended loop that raises on a broken gate stops beating altogether."""
        from pravrudhi.application import completion, heartbeat, requests

        request_id = self._evidenced_request(tmp_path)

        def explode(*_a: object, **_k: object) -> object:
            raise RuntimeError("no agent installed")

        monkeypatch.setattr(completion, "gate", explode)

        _chose, reason, result = heartbeat._beat_completion_gate(tmp_path, request_id)

        assert result is not None and result["ran"] is False
        assert "no agent installed" in reason
        assert requests.get(tmp_path, request_id).state == "delivered", "a broken gate must not move the request"


class TestTheLoopDoesNotOscillate:
    """Two branches that only described their work also fed each other.

    The gate refused a `delivered` request back to `in_progress`; the next beat saw every existing criterion
    still carrying evidence, called it ready to move, and advanced it to `delivered`; the gate refused it again.
    Fixing one branch without the other buys a faster wedge, not a working loop.
    """

    @staticmethod
    def _evidenced(root: Path) -> str:
        from pravrudhi.application import requests

        req = requests.capture(root, "make the thing genuinely work")
        requests.add_criteria(root, req.id, [requests.Criterion(text="the thing works", source="operator")])
        requests.meet(root, req.id, 0, [requests.Evidence(kind="file", ref="README.md", note="there")])
        requests.advance(root, req.id, "in_progress")
        requests.advance(root, req.id, "delivered")
        return req.id

    def test_a_refusal_leaves_unmet_work_behind(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from pravrudhi.application import completion, heartbeat, requests

        (tmp_path / "README.md").write_text("there")
        rid = self._evidenced(tmp_path)
        review = completion.ReviewResult(
            findings="## Verdict\n\nThe rival named twice was never assessed.", blocking=True, reason="refused")
        monkeypatch.setattr(completion, "gate",
                            lambda root, r, **kw: completion.GateResult(r, False, "review", [], review))

        heartbeat._beat_completion_gate(tmp_path, rid)

        after = requests.get(tmp_path, rid)
        unmet = [c for c in after.criteria if not c.met]
        assert unmet, "a refused request kept every criterion met, so the next beat has nothing to build"
        assert "never assessed" in unmet[0].text

    def test_the_same_objection_does_not_accumulate_criteria(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An hourly loop repeating one objection would bury the request under identical demands."""
        from pravrudhi.application import completion, heartbeat, requests

        (tmp_path / "README.md").write_text("there")
        rid = self._evidenced(tmp_path)
        review = completion.ReviewResult(findings="the same objection", blocking=True, reason="refused")
        monkeypatch.setattr(completion, "gate",
                            lambda root, r, **kw: completion.GateResult(r, False, "review", [], review))

        # Twice, as an hourly loop would: the second refusal must recognise its own open criterion.
        heartbeat._beat_completion_gate(tmp_path, rid)
        heartbeat._beat_completion_gate(tmp_path, rid)

        after = requests.get(tmp_path, rid)
        review_criteria = [c for c in after.criteria if c.text.startswith(heartbeat._REVIEW_CRITERION_PREFIX)]
        assert len(review_criteria) == 1, f"one objection produced {len(review_criteria)} criteria"

    def test_a_request_ready_to_move_is_moved(self, tmp_path: Path) -> None:
        from pravrudhi.application import heartbeat, requests

        req = requests.capture(tmp_path, "something owed")
        requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="done", source="operator")])
        requests.meet(tmp_path, req.id, 0, [requests.Evidence(kind="file", ref="README.md")])
        requests.advance(tmp_path, req.id, "in_progress")

        _chose, _reason, result = heartbeat._beat_obligations(tmp_path, lambda _t: "")

        assert result is not None and result.get("kind") == "advance", "the beat described the move again"
        assert requests.get(tmp_path, req.id).state == "delivered"


def test_the_criterion_from_a_finding_carries_the_finding_not_its_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review opens with headings and label lines. Taking the first non-heading line produced a criterion
    reading "Summary of the strongest reason:" — a demand with the demand missing, useless to the builder that
    picks it up next beat."""
    from pravrudhi.application import completion, heartbeat, requests

    (tmp_path / "README.md").write_text("there")
    req = requests.capture(tmp_path, "do the work")
    requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="works", source="operator")])
    requests.meet(tmp_path, req.id, 0, [requests.Evidence(kind="file", ref="README.md")])
    requests.advance(tmp_path, req.id, "in_progress")
    requests.advance(tmp_path, req.id, "delivered")

    findings = (
        "## Verdict: does not satisfy the request\n\n"
        "Summary of the strongest reason:\n\n"
        "The rival named twice by the operator was never assessed anywhere in the product.\n"
    )
    review = completion.ReviewResult(findings=findings, blocking=True, reason="refused")
    monkeypatch.setattr(completion, "gate",
                        lambda root, r, **kw: completion.GateResult(r, False, "review", [], review))

    heartbeat._beat_completion_gate(tmp_path, req.id)

    added = [c for c in requests.get(tmp_path, req.id).criteria
             if c.text.startswith(heartbeat._REVIEW_CRITERION_PREFIX)][0]
    assert "never assessed" in added.text
    assert not added.text.rstrip().endswith(":"), "the criterion is a label with no finding behind it"


def test_continuity_says_it_is_proposing_a_remedy_rather_than_running_one() -> None:
    """The beat must not claim to have done what it has deliberately not done.

    `_beat_continuity` never executes a repair, by design and by its own docstring: installing Docker or repairing
    a ledger is not something a heartbeat does unattended. It wrote "running the remedy for the failing 'pools'
    continuity check" all the same. On the product workspace that line appeared once an hour for eight hours with
    the pool never sealed and the deficit never moving, which reads in the journal as work happening. A beat that
    proposes is useful; a beat that says it ran something it did not is worse than silence.
    """
    drive = sthiti_drive_with_failing_checks(deficit=0.17, failing=("pools",))
    _chose, reason, result = heartbeat._beat_continuity(drive)
    assert "pools" in reason and result == {"check": "pools", "remedy": heartbeat._continuity_remedy("pools")[1]}
    assert not reason.startswith("running "), reason
    assert "propos" in reason, reason


def test_review_criterion_carries_the_finding_not_the_reviewer_s_preamble(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review's opening sentence often announces that a finding exists without stating it.

    The real one that stalled the studio loop read "I inspected the actual code and assets behind each criterion
    rather than trusting the citations, and found a real reason the completion does not satisfy the operator's
    request." It clears the label guard - long, and no trailing colon - so it became the criterion. Three
    dispatches each produced twenty-odd files guessing at what the reason might have been, none of them could be
    marked met, and the attempt budget then parked the whole request. A finding a builder can act on names the
    thing it is about; a preamble names nothing.
    """
    from pravrudhi.application import completion, heartbeat, requests

    (tmp_path / "README.md").write_text("there")
    req = requests.capture(tmp_path, "do the work")
    requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="works", source="operator")])
    requests.meet(tmp_path, req.id, 0, [requests.Evidence(kind="file", ref="README.md")])
    requests.advance(tmp_path, req.id, "in_progress")
    requests.advance(tmp_path, req.id, "delivered")

    findings = (
        "## Summary\n\n"
        "I inspected the actual code and assets behind each criterion rather than trusting the citations, "
        "and found a real reason the completion does not satisfy the operator's request.\n\n"
        "The parity matrix in `parity.py` documents itself as not autonomous, and nothing in the drive loop "
        "ever calls it, so no agent persists on parity.\n"
    )
    review = completion.ReviewResult(findings=findings, blocking=True, reason="refused")
    monkeypatch.setattr(completion, "gate",
                        lambda root, r, **kw: completion.GateResult(r, False, "review", [], review))

    heartbeat._beat_completion_gate(tmp_path, req.id)

    added = [c for c in requests.get(tmp_path, req.id).criteria
             if c.text.startswith(heartbeat._REVIEW_CRITERION_PREFIX)][0]
    assert "parity.py" in added.text, added.text
    assert "found a real reason" not in added.text, "the criterion announces a finding instead of stating one"
