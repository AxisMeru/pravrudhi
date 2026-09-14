"""The heartbeat: one wake-up measures the appetite's drives, lets them select a winner, and dispatches the
action wired to that drive."""

from __future__ import annotations

import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from pravrudhi.agents.base import AgentRun, Diff
from pravrudhi.application import availability, heartbeat, kshudha, requests
from pravrudhi.application.heartbeat import beat, history, load_config, log_path
from pravrudhi.application.objectives import Benchmark, Objective
from pravrudhi.application.objectives import write as write_objective
from pravrudhi.application.requests import Criterion
from pravrudhi.application.sandbox_policy import Policy
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


def test_a_proposal_naming_a_bare_filename_in_prose_is_still_accepted(tmp_path, monkeypatch):
    """The product install's 08:10 beat on 2026-09-12 dispatched r-3981d7e0 criterion 3 - `mode: "proposal"`,
    asking for a file under `proposals/`, nowhere near a recognised build prefix - and it was rejected:
    "wrote outside its declared scope" naming exactly the `proposals/requests/<id>/<idx>/` files a proposal
    dispatch is supposed to write. `dispatch_mode` had reclassified it as "build" because the bare (no
    backtick needed) mention of `INSTALL.md` in the criterion's prose matched `code_extensions`, and because
    `build_paths_for`'s own `tests/*` filler (always added, even when nothing else was found) defeated
    `dispatch_mode`'s "nothing found -> proposal" gate - so the dispatch was scoped to `tests/*` instead of
    the scratch directory the agent (correctly) wrote to."""
    patch_measure(monkeypatch, seva=0.8)
    requests.capture(
        tmp_path, "sort out the IL-TUR install instructions", request_id="req-2",
        criteria=[Criterion(
            text=(
                "Write proposals/prabhasa-nyaya/harness/INSTALL.md: the exact commands to install lm-eval in "
                "this workspace and to fetch IL-TUR (Exploration-Lab/IL-TUR), with the dataset's actual size "
                "and licence stated from its Hugging Face page. Do not run them."
            ),
            source="operator",
        )],
    )

    record = beat(
        tmp_path, dispatch=dispatch_into("proposals/requests/req-2/0/README.md"),
        now=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    )

    assert record.result is not None
    assert record.result["accepted"] is True, record.reason
    assert "outside its declared scope" not in record.reason


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


def test_beat_reprobes_a_cooling_route_even_when_the_beat_dispatches_elsewhere(tmp_path, monkeypatch):
    """The route recovers because `beat` probed it directly, not because whatever it dispatched this beat
    happened to run through it -- here nothing does: `patch_measure` with no overrides leaves every known drive
    satisfied, so the winner is the unwired `freshness` diagnostic and no route is ever dispatched to at all."""
    patch_measure(monkeypatch)
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    availability.mark_limited(tmp_path, "claude-code", minutes=120, now=now)

    probed: list[str] = []

    def probe(agent_id: str) -> bool:
        probed.append(agent_id)
        return agent_id == "claude-code"

    record = beat(tmp_path, dispatch=ok_dispatch, probe=probe, now=now)

    assert probed == ["claude-code"]
    assert not availability.is_cool(tmp_path, "claude-code", now=now)
    assert record.drive == "freshness"


def test_beat_leaves_a_cooling_route_alone_when_the_probe_still_finds_it_limited(tmp_path, monkeypatch):
    patch_measure(monkeypatch)
    now = datetime(2026, 1, 1, 12, tzinfo=UTC)
    availability.mark_limited(tmp_path, "claude-code", minutes=120, now=now)

    beat(tmp_path, dispatch=ok_dispatch, probe=lambda _agent_id: False, now=now)

    assert availability.is_cool(tmp_path, "claude-code", now=now)


def test_beat_does_not_build_an_agent_to_reprobe_when_nothing_is_cooling(tmp_path, monkeypatch):
    """The default probe is real production wiring (it builds and runs an actual agent, like `_default_judge`
    does its own judging, independent of whatever `dispatch` was injected); a beat with nothing cooling must
    never reach it, or every ordinary unit test in this file would spin one up unasked."""
    patch_measure(monkeypatch)

    def build_agent(root: Path, name: str, model: str | None) -> None:
        raise AssertionError(f"must not build an agent for {name!r}: nothing is cooling")

    monkeypatch.setattr(heartbeat, "_registry_build_agent", build_agent)

    record = beat(tmp_path, dispatch=ok_dispatch, now=datetime(2026, 1, 1, 12, tzinfo=UTC))

    assert record.drive == "freshness"


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


def test_a_reviewer_naming_a_required_toolchain_carries_it_onto_the_new_criterion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`completion._review_brief` asks the reviewer to name a `TOOLCHAIN:` line when the gap it found needs a
    particular runtime. This is the other half: that line must reach the new criterion's own `toolchain` field,
    not sit unread in the finding text, and must not itself become the criterion's headline."""
    from pravrudhi.application import completion, heartbeat, requests

    (tmp_path / "README.md").write_text("there")
    req = requests.capture(tmp_path, "do the work")
    requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="works", source="operator")])
    requests.meet(tmp_path, req.id, 0, [requests.Evidence(kind="file", ref="README.md")])
    requests.advance(tmp_path, req.id, "in_progress")
    requests.advance(tmp_path, req.id, "delivered")

    findings = (
        "## Verdict: does not satisfy the request\n\n"
        "The proposal drives the Electron shell via Python's playwright package, which has no Electron "
        "support at all - only the Node/TypeScript bindings ship one.\n\n"
        "TOOLCHAIN: Node.js/@playwright/test\n"
    )
    review = completion.ReviewResult(findings=findings, blocking=True, reason="refused")
    monkeypatch.setattr(completion, "gate",
                        lambda root, r, **kw: completion.GateResult(r, False, "review", [], review))

    heartbeat._beat_completion_gate(tmp_path, req.id)

    added = [c for c in requests.get(tmp_path, req.id).criteria
             if c.text.startswith(heartbeat._REVIEW_CRITERION_PREFIX)][0]
    assert added.toolchain == "Node.js/@playwright/test"
    assert "no Electron support" in added.text
    assert "TOOLCHAIN:" not in added.text, "the toolchain line is a field, not part of the criterion's own text"


def test_a_reviewer_naming_no_toolchain_leaves_the_field_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pravrudhi.application import completion, heartbeat, requests

    (tmp_path / "README.md").write_text("there")
    req = requests.capture(tmp_path, "do the work")
    requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="works", source="operator")])
    requests.meet(tmp_path, req.id, 0, [requests.Evidence(kind="file", ref="README.md")])
    requests.advance(tmp_path, req.id, "in_progress")
    requests.advance(tmp_path, req.id, "delivered")

    findings = "## Verdict\n\nThe rival named twice was never assessed anywhere in the product.\n"
    review = completion.ReviewResult(findings=findings, blocking=True, reason="refused")
    monkeypatch.setattr(completion, "gate",
                        lambda root, r, **kw: completion.GateResult(r, False, "review", [], review))

    heartbeat._beat_completion_gate(tmp_path, req.id)

    added = [c for c in requests.get(tmp_path, req.id).criteria
             if c.text.startswith(heartbeat._REVIEW_CRITERION_PREFIX)][0]
    assert added.toolchain is None


class TestGateAttemptBudget:
    """A completion gate that crashes should not be retried indefinitely.

    H3 from ADVERSARIAL-2026-09-11: when a request's completion gate raises, the exception is caught and the
    request stays `delivered`; every subsequent beat selects the same request again and re-runs the gate, building
    a new review agent each time. Unlike criteria, gates have no attempt counter. This wastes API spend in an
    unattended loop and starves younger requests.

    Fix: record gate attempts on the request, refuse to run the gate after a configured maximum, and have
    next_obligation skip such requests the way it skips parked criteria.
    """

    @staticmethod
    def _evidenced_request(tmp_path: Path) -> str:
        """A request with all criteria met, ready for the gate."""
        (tmp_path / "README.md").write_text("it is there")
        req = requests.capture(tmp_path, "do the work")
        requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="works", source="operator")])
        requests.meet(tmp_path, req.id, 0, [requests.Evidence(kind="file", ref="README.md")])
        requests.advance(tmp_path, req.id, "in_progress")
        requests.advance(tmp_path, req.id, "delivered")
        return req.id

    def test_gate_attempts_are_tracked_per_request(self, tmp_path: Path) -> None:
        """Gate attempts must be recorded and persisted like criterion attempts."""
        rid = "r-gate-1"
        assert heartbeat.gate_attempts(tmp_path, rid) == 0
        heartbeat.record_gate_attempt(tmp_path, rid)
        assert heartbeat.gate_attempts(tmp_path, rid) == 1
        heartbeat.record_gate_attempt(tmp_path, rid)
        assert heartbeat.gate_attempts(tmp_path, rid) == 2

    def test_gate_attempts_survive_restart(self, tmp_path: Path) -> None:
        """The count lives on disk so an hourly loop that restarts still respects the budget."""
        rid = "r-gate-1"
        for _ in range(heartbeat.MAX_GATE_ATTEMPTS):
            heartbeat.record_gate_attempt(tmp_path, rid)
        assert heartbeat.gate_stalled(tmp_path, rid)
        # Simulate restart by creating a new heartbeat instance; the file persists
        assert heartbeat.gate_attempts(tmp_path, rid) == heartbeat.MAX_GATE_ATTEMPTS

    def test_a_crashing_gate_is_not_retried_forever(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """After MAX_GATE_ATTEMPTS failures, the beat must stop running the gate."""
        from pravrudhi.application import completion, heartbeat, requests

        request_id = self._evidenced_request(tmp_path)

        def explode(*_a: object, **_k: object) -> object:
            raise RuntimeError("gate crash")

        monkeypatch.setattr(completion, "gate", explode)

        # Run the gate MAX_GATE_ATTEMPTS times; each one fails but request stays delivered
        for i in range(heartbeat.MAX_GATE_ATTEMPTS):
            _chose, reason, result = heartbeat._beat_completion_gate(tmp_path, request_id)
            assert result is not None and result["ran"] is False
            assert "gate crash" in reason
            req_after = requests.get(tmp_path, request_id)
            assert req_after.state == "delivered", "a broken gate must not move the request"
            assert heartbeat.gate_attempts(tmp_path, request_id) == i + 1

        # The request should now be parked (gate stalled)
        assert heartbeat.gate_stalled(tmp_path, request_id)

    def test_a_parked_gate_is_not_chosen_again(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Once a gate is parked, next_obligation must skip it and select a younger request."""
        from datetime import timedelta

        from pravrudhi.application import completion, heartbeat, requests

        # Old request with a crashing gate
        old_at = (datetime.now(UTC) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
        old_req = requests.capture(tmp_path, "the ask with the crashing gate", asked_at=old_at)
        requests.add_criteria(tmp_path, old_req.id, [requests.Criterion(text="works", source="operator")])
        (tmp_path / "README.md").write_text("it is there")
        requests.meet(tmp_path, old_req.id, 0, [requests.Evidence(kind="file", ref="README.md")])
        requests.advance(tmp_path, old_req.id, "in_progress")
        requests.advance(tmp_path, old_req.id, "delivered")

        # Fresh request ready to move
        fresh_req = requests.capture(tmp_path, "an ask waiting for work")
        requests.add_criteria(tmp_path, fresh_req.id, [requests.Criterion(text="fresh work", source="operator")])
        (tmp_path / "fresh.md").write_text("fresh")
        requests.meet(tmp_path, fresh_req.id, 0, [requests.Evidence(kind="file", ref="fresh.md")])
        requests.advance(tmp_path, fresh_req.id, "in_progress")
        requests.advance(tmp_path, fresh_req.id, "delivered")

        def explode(*_a: object, **_k: object) -> object:
            raise RuntimeError("gate crash")

        monkeypatch.setattr(completion, "gate", explode)

        # Park the old request's gate by running it MAX_GATE_ATTEMPTS times
        for _ in range(heartbeat.MAX_GATE_ATTEMPTS):
            heartbeat._beat_completion_gate(tmp_path, old_req.id)

        # Now the next obligation should be the fresh request, not the parked one
        owed = requests.next_obligation(tmp_path)
        assert owed is not None
        if owed["kind"] == "parked_request":
            # This is a parked criterion, not a parked gate; skip to the next one
            assert owed["request"] != old_req.id or "gate" in owed["description"].lower()
        else:
            # Should be able to verify the fresh request, not repeat the old gate
            assert owed["request"] == fresh_req.id


class TestDispatchLevelFailureHandling:
    """Dispatch-level failures must not consume criterion attempts.

    H4 from ADVERSARIAL-2026-09-11: a criterion attempt is recorded BEFORE dispatch; if swarm.run_wave returns
    accepted=False for a dispatch-level reason (validation, workspace race), the code returns before any judge
    runs, and the attempt is still consumed. Three transient failures park a solvable criterion permanently.

    Fix: consume an attempt only when the dispatch produced work the judge evaluated (after the accepted branch
    reaches the judge), and record dispatch-level failures separately with their own cap.
    """

    @staticmethod
    def _setup_criterion(tmp_path: Path, request_id: str = "r-test") -> str:
        """Set up a request with an unmet criterion, ready to dispatch."""
        req = requests.capture(tmp_path, "do the work", request_id=request_id)
        requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="build it", source="operator")])
        return req.id

    def test_dispatch_failure_does_not_consume_judged_attempt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When dispatch rejects the work before judging, the criterion's judged attempts should not be consumed."""
        from pravrudhi.application import heartbeat, swarm

        req_id = self._setup_criterion(tmp_path)

        def failing_wave(build_agent: object, wave: object, **kw: object) -> list[object]:
            return [swarm.Verdict(
                task_id="req:test:0", agent="test", accepted=False,
                reasons=["validation failed"], files=[]
            )]

        monkeypatch.setattr(swarm, "run_wave", failing_wave)

        # Dispatch fails at validation level
        def dispatch_fn(name: str, model: str | None) -> object:
            return object()

        _chose, reason, result = heartbeat._beat_obligations(tmp_path, dispatch_fn)

        # Judged attempts should NOT be consumed when dispatch fails
        assert heartbeat.attempts(tmp_path, req_id, 0) == 0, \
            "dispatch-level failure must not consume a judged attempt"
        assert result is not None and result["accepted"] is False

    def test_repeated_dispatch_failures_are_bounded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Three dispatch-level failures should park the criterion separately from judged attempts."""
        from pravrudhi.application import heartbeat, swarm

        req_id = self._setup_criterion(tmp_path)

        def failing_wave(build_agent: object, wave: object, **kw: object) -> list[object]:
            return [swarm.Verdict(
                task_id="req:test:0", agent="test", accepted=False,
                reasons=["workspace race"], files=[]
            )]

        monkeypatch.setattr(swarm, "run_wave", failing_wave)

        def dispatch_fn(name: str, model: str | None) -> object:
            return object()

        # Three dispatch failures
        for _ in range(3):
            _chose, reason, result = heartbeat._beat_obligations(tmp_path, dispatch_fn)
            assert result is not None and result["accepted"] is False

        # Judged attempts should still be 0
        assert heartbeat.attempts(tmp_path, req_id, 0) == 0
        # Dispatch failures should be recorded separately
        dispatch_fails = heartbeat.dispatch_failures(tmp_path, req_id, 0)
        assert dispatch_fails == 3

    def test_accepted_dispatch_with_judge_rejection_consumes_attempt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When dispatch accepts but judge rejects, the attempt should be consumed."""
        from pravrudhi.application import heartbeat, swarm

        req_id = self._setup_criterion(tmp_path)

        def accepting_wave(build_agent: object, wave: object, **kw: object) -> list[object]:
            return [swarm.Verdict(
                task_id="req:test:0", agent="test", accepted=True,
                reasons=[], files=["proposals/requests/r-test/0/output.md"]
            )]

        def rejecting_judge(prompt: str) -> str:
            return "VERDICT: not met\nThe work is incomplete."

        monkeypatch.setattr(swarm, "run_wave", accepting_wave)

        def dispatch_fn(name: str, model: str | None) -> object:
            return object()

        _chose, reason, result = heartbeat._beat_obligations(tmp_path, dispatch_fn, judge=rejecting_judge)

        # Attempt SHOULD be consumed when judge runs
        assert heartbeat.attempts(tmp_path, req_id, 0) == 1, \
            "judged rejection must consume an attempt"
        assert result is not None and result.get("judged") == "not met"


class TestNoOpDispatchIsAResultNotAFailure:
    """ADR-0053 §5: an agent that concluded nothing needed changing has reported a result, not failed a dispatch.

    The Studio heartbeat's 17:15 beat on 2026-09-12 recorded `dispatch_failures: 18` and parked a criterion whose
    dispatched agent had honestly reported the work already done ("No files were created or modified, and I made
    no commit"). `delegate.dispatch`'s `diff.empty` branch already tells a genuine no-op from a real escape: an
    escape adds a second reason ("wrote outside its worktree..."); a genuine no-op leaves the explanation as the
    only reason. So a no-op with exactly that one reason is re-judged against the repository's current state
    (`root`, not the agent's empty worktree - there is nothing else to check there) instead of being counted as
    a dispatch failure. Fail-closed still applies: `_first_unevidenced_claim` still runs, and only three
    consecutive genuine no-ops (this session's own convention: a false claim resets the streak) close the
    criterion as already-met."""

    @staticmethod
    def _setup_criterion(tmp_path: Path, request_id: str = "r-test") -> str:
        req = requests.capture(tmp_path, "do the work", request_id=request_id)
        requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="build it", source="operator")])
        return req.id

    @staticmethod
    def _noop_wave(build_agent: object, wave: object, **kw: object) -> list[object]:
        from pravrudhi.application import swarm

        return [swarm.Verdict(
            task_id="req:test:0", agent="test", accepted=False,
            reasons=["no change produced: I checked and this is already done."], files=[],
        )]

    @staticmethod
    def _dispatch_fn(name: str, model: str | None) -> object:
        return object()

    def test_a_genuine_noop_does_not_record_a_dispatch_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import heartbeat, swarm

        req_id = self._setup_criterion(tmp_path)
        monkeypatch.setattr(swarm, "run_wave", self._noop_wave)

        def met_judge(prompt: str) -> str:
            return "VERDICT: met\nThe file already contains what the criterion asks for."

        _chose, reason, result = heartbeat._beat_obligations(tmp_path, self._dispatch_fn, judge=met_judge)

        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == 0, \
            "a genuine no-op must not be booked as a dispatch failure"
        assert heartbeat.attempts(tmp_path, req_id, 0) == 0, \
            "a genuine no-op is not a judged build attempt either"
        assert result is not None and result.get("judged") == "met"

    def test_a_noop_with_a_second_reason_is_still_a_dispatch_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The escape this check exists to keep catching: an empty worktree that is not a genuine no-op."""
        from pravrudhi.application import heartbeat, swarm

        req_id = self._setup_criterion(tmp_path)

        def escaped_wave(build_agent: object, wave: object, **kw: object) -> list[object]:
            return [swarm.Verdict(
                task_id="req:test:0", agent="test", accepted=False,
                reasons=[
                    "no change produced: done.",
                    "wrote outside its worktree, into the main checkout: proposals/probe/output.md",
                ],
                files=[],
            )]

        monkeypatch.setattr(swarm, "run_wave", escaped_wave)

        def met_judge(prompt: str) -> str:
            raise AssertionError("the judge must not run for a two-reason verdict")

        _chose, reason, result = heartbeat._beat_obligations(tmp_path, self._dispatch_fn, judge=met_judge)

        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == 1
        assert result is not None and result["accepted"] is False and "judged" not in result

    def test_three_consecutive_genuine_noops_close_the_criterion_as_already_met(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import heartbeat, requests, swarm

        req_id = self._setup_criterion(tmp_path)
        monkeypatch.setattr(swarm, "run_wave", self._noop_wave)

        def met_judge(prompt: str) -> str:
            return "VERDICT: met\nThe file already contains what the criterion asks for."

        for _ in range(2):
            _chose, reason, result = heartbeat._beat_obligations(tmp_path, self._dispatch_fn, judge=met_judge)
            assert result is not None and result.get("judged") == "met"
            assert requests.get(tmp_path, req_id).criteria[0].met is False, \
                "fewer than three consecutive no-ops must not close the criterion"

        _chose, reason, result = heartbeat._beat_obligations(tmp_path, self._dispatch_fn, judge=met_judge)
        assert requests.get(tmp_path, req_id).criteria[0].met is True
        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == 0

    def test_a_false_noop_claim_resets_the_streak_rather_than_closing_the_criterion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import heartbeat, requests, swarm

        req_id = self._setup_criterion(tmp_path)
        monkeypatch.setattr(swarm, "run_wave", self._noop_wave)

        def met_judge(prompt: str) -> str:
            return "VERDICT: met\nAlready there."

        def not_met_judge(prompt: str) -> str:
            return "VERDICT: not met\nThe criterion asks for something this file does not have."

        heartbeat._beat_obligations(tmp_path, self._dispatch_fn, judge=met_judge)
        heartbeat._beat_obligations(tmp_path, self._dispatch_fn, judge=not_met_judge)
        heartbeat._beat_obligations(tmp_path, self._dispatch_fn, judge=met_judge)
        heartbeat._beat_obligations(tmp_path, self._dispatch_fn, judge=met_judge)

        # Two genuine no-ops since the false claim broke the streak - not yet three, not yet closed.
        assert requests.get(tmp_path, req_id).criteria[0].met is False
        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == 0


class TestExternalWallDispatchFailuresDoNotCountTowardsTheBudget:
    """A dispatch that failed because of a vendor usage limit or a host memory floor says nothing about whether
    the criterion is buildable - the same distinction ADR-0053 §5 already draws for a genuine no-op, drawn here
    for a wall outside the criterion's own reach.

    2026-09-13: Studio's r-cad91781 criterion 13 and product's r-55c7083e criterion 1 were both parked (5 and 3
    dispatch failures) with the exact same second reason - `"no change produced: You've hit your session limit
    · resets 11:40am (Europe/London)"` - recorded while the account was mid-quota, on both loops, at the
    same time. cli-lead had to hand-edit `.pravrudhi/dispatch-failures.json` to clear them because nothing else
    would. These tests use that literal text, not an invented string."""

    _SESSION_LIMIT_REASONS = [
        "agent exited non-zero: no detail",
        "no change produced: You've hit your session limit · resets 11:40am (Europe/London)",
    ]

    @staticmethod
    def _setup_criterion(tmp_path: Path, request_id: str = "r-test") -> str:
        req = requests.capture(tmp_path, "do the work", request_id=request_id)
        requests.add_criteria(tmp_path, req.id, [requests.Criterion(text="build it", source="operator")])
        return req.id

    @staticmethod
    def _dispatch_fn(name: str, model: str | None) -> object:
        return object()

    def test_external_wall_reason_recognises_the_real_recorded_text(self) -> None:
        assert heartbeat.external_wall_reason(self._SESSION_LIMIT_REASONS) == "session limit"
        assert heartbeat.external_wall_reason(["agent exited non-zero: no detail"]) is None

    def test_a_memory_floor_refusal_is_recognised_unambiguously(self) -> None:
        assert heartbeat.external_wall_reason(
            ["MemAvailable 4.8GB is below the 6.0GB floor"]
        ) == "below the 6.0gb floor"

    def test_a_genuine_failure_naming_permissions_is_not_a_wall(self) -> None:
        assert heartbeat.external_wall_reason(
            ["permission requested: external_directory (/tmp/*); auto-rejecting"]
        ) is None

    def test_prose_about_rate_limiting_is_not_mistaken_for_a_wall(self) -> None:
        """cli-lead, 2026-09-13: a criterion whose own text is about rate limiting can put the bare phrase
        into an agent's dispatch-failure reason for a dispatch that genuinely failed. Without wall-context the
        phrase alone would exempt it from MAX_DISPATCH_FAILURES forever - the opposite failure from the one
        this module exists to fix."""
        assert heartbeat.external_wall_reason(
            ["The script should rate limit its requests to the API, but the diff left that unimplemented."]
        ) is None

    def test_rate_limited_with_wall_context_is_still_recognised(self) -> None:
        assert heartbeat.external_wall_reason(
            ["agent exited non-zero: rate limited (429), try again later"]
        ) == "rate limited"

    def test_a_session_limit_dispatch_failure_is_not_recorded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import swarm

        req_id = self._setup_criterion(tmp_path)

        def limited_wave(build_agent: object, wave: object, **kw: object) -> list[object]:
            return [swarm.Verdict(
                task_id="req:test:0", agent="test", accepted=False,
                reasons=list(self._SESSION_LIMIT_REASONS), files=[],
            )]

        monkeypatch.setattr(swarm, "run_wave", limited_wave)

        for _ in range(5):
            _chose, _reason, result = heartbeat._beat_obligations(tmp_path, self._dispatch_fn)
            assert result is not None and result.get("external_wall") == "session limit"

        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == 0, \
            "an external wall must never be counted towards MAX_DISPATCH_FAILURES"
        assert requests.get(tmp_path, req_id).criteria[0].met is False

    def test_an_unrecognised_dispatch_failure_still_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import swarm

        req_id = self._setup_criterion(tmp_path)

        def ordinary_failure(build_agent: object, wave: object, **kw: object) -> list[object]:
            return [swarm.Verdict(task_id="req:test:0", agent="test", accepted=False,
                                   reasons=["workspace race"], files=[])]

        monkeypatch.setattr(swarm, "run_wave", ordinary_failure)
        heartbeat._beat_obligations(tmp_path, self._dispatch_fn)

        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == 1

    def test_clear_dispatch_failures_forgets_the_count_and_its_timestamp(self, tmp_path: Path) -> None:
        req_id = self._setup_criterion(tmp_path)
        heartbeat.record_dispatch_failure(tmp_path, req_id, 0)
        heartbeat.record_dispatch_failure(tmp_path, req_id, 0)
        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == 2

        heartbeat.clear_dispatch_failures(tmp_path, req_id, 0)

        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == 0
        # Clearing a criterion with nothing recorded must not raise.
        heartbeat.clear_dispatch_failures(tmp_path, "r-nothing-here", 0)

    def test_an_unrecognised_wall_still_expires_after_the_configured_hours(self, tmp_path: Path) -> None:
        """The fallback for a phrasing `external_wall_reason` does not know: a dispatch failure this old is
        presumed to have been caused by something that has since passed, rather than left parked forever."""
        req_id = self._setup_criterion(tmp_path)
        stale = datetime(2020, 1, 1, tzinfo=UTC)
        for _ in range(heartbeat.MAX_DISPATCH_FAILURES):
            heartbeat.record_dispatch_failure(tmp_path, req_id, 0, now=stale)

        assert heartbeat.dispatch_failures(tmp_path, req_id, 0) == heartbeat.MAX_DISPATCH_FAILURES
        assert heartbeat.dispatch_failures_exhausted(tmp_path, req_id, 0) is False, \
            "a dispatch-failure record older than the expiry window must not keep a criterion parked"

    def test_a_recent_unrecognised_failure_still_exhausts_normally(self, tmp_path: Path) -> None:
        req_id = self._setup_criterion(tmp_path)
        for _ in range(heartbeat.MAX_DISPATCH_FAILURES):
            heartbeat.record_dispatch_failure(tmp_path, req_id, 0)

        assert heartbeat.dispatch_failures_exhausted(tmp_path, req_id, 0) is True


class TestStalledAlsoRespectsDispatchFailureExhaustion:
    """cli-web/cli-lead, 2026-09-14: `_apply_verdict` already labels a beat's result "parked after N dispatch
    failures" once `dispatch_failures_exhausted` is true, but `stalled()` - the function that actually gates
    re-selection - never consulted it, only `attempts()` (never incremented by a dispatch failure) and
    `network_capability_gap()`. A criterion that only ever dispatch-fails (r-55c7083e:3's scratch-scope
    rejection, fixed alongside this) was therefore never actually stalled: the label said "parked" and the
    loop re-selected it every beat regardless."""

    def test_stalled_is_true_once_dispatch_failures_are_exhausted_even_with_zero_judged_attempts(
        self, tmp_path: Path,
    ) -> None:
        req_id = "r-test"
        for _ in range(heartbeat.MAX_DISPATCH_FAILURES):
            heartbeat.record_dispatch_failure(tmp_path, req_id, 0)

        assert heartbeat.attempts(tmp_path, req_id, 0) == 0
        assert heartbeat.stalled(tmp_path, req_id, 0) is True

    def test_stalled_is_false_below_the_dispatch_failure_budget(self, tmp_path: Path) -> None:
        req_id = "r-test"
        heartbeat.record_dispatch_failure(tmp_path, req_id, 0)

        assert heartbeat.stalled(tmp_path, req_id, 0) is False

    def test_an_expired_dispatch_failure_record_does_not_stall_via_this_path_either(self, tmp_path: Path) -> None:
        req_id = "r-test"
        stale = datetime(2020, 1, 1, tzinfo=UTC)
        for _ in range(heartbeat.MAX_DISPATCH_FAILURES):
            heartbeat.record_dispatch_failure(tmp_path, req_id, 0, now=stale)

        assert heartbeat.stalled(tmp_path, req_id, 0) is False


def test_is_genuine_noop_recognises_what_delegate_dispatch_actually_produces(tmp_path: Path) -> None:
    """`_is_genuine_noop` matches a literal built at delegate.py's own `reasons.append(f"no change produced:
    ...")` call - a string-prefix coupling between two modules that a reword of that one line would break
    silently: every no-op would fall back to the dispatch-failure counter, and TestNoOpDispatchIsAResultNotAFailure
    would not notice, because its tests build `Verdict`s with the literal themselves rather than through
    `delegate.dispatch`. This drives the real function so a reword fails loudly here instead of quietly at
    runtime."""
    from pravrudhi.agents.base import AgentRun, Diff
    from pravrudhi.application.delegate import TaskSpec, dispatch

    class NoOpAgent:
        """Writes nothing and explains why, exactly as a real agent that concluded the work was already done."""

        name = "fake"

        def create_workspace(self, task_id: str, base_ref: str = "HEAD") -> Path:
            ws = tmp_path / task_id
            ws.mkdir(parents=True, exist_ok=True)
            return ws

        def run(self, prompt: str, workspace: Path, timeout_s: int = 60) -> AgentRun:
            return AgentRun(
                agent=self.name, ok=True, exit_code=0, wall_s=0.1,
                text="I checked and this is already done.", workspace=workspace,
            )

        def collect_changes(self, workspace: Path) -> Diff:
            return Diff(files=[])

    task = TaskSpec(task_id="t-noop", prompt="p", allowed_paths=("a.py",), validate="true")
    verdict = dispatch(NoOpAgent(), task, log=lambda s: None)

    assert not verdict.accepted
    assert heartbeat._is_genuine_noop(verdict), verdict.reasons


def test_a_judgement_keeps_enough_of_its_reason_for_the_next_attempt_to_act_on() -> None:
    """r-1977143a criterion 1 was refused twice on 2026-09-11 and both stored reasons stopped mid-sentence at 300
    characters, before the part that said what was missing; the next attempt started from a truncated hint."""
    from pravrudhi.application.heartbeat import _judged

    reason = " ".join(f"point {i} about what the change still lacks." for i in range(40))
    met, why = _judged(f"VERDICT: not met\n{reason}")
    assert not met
    assert len(why) >= 1000 and why.startswith("point 0")


class TestLoopSyncsBeforeEveryBeat:
    """ADR-0053 §3: before any dispatch, a loop root fast-forwards/rebases onto origin/main, so a hypothesis is
    tested against what the team has actually landed rather than an increasingly stale clone. On conflict the
    beat parks itself, not any one criterion (the drive/criterion that would run this beat hasn't even been
    selected yet at this point in `beat()`) - it reports the reason and the loop never resolves the conflict
    itself. A root that is not a git repository at all (every other test in this file, and any root not yet
    re-rooted under this ADR) is left untouched: there is nothing to sync."""

    @staticmethod
    def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=check,
        )

    @classmethod
    def _origin_and_clone(cls, tmp_path: Path, *, branch: str | None = "loop/studio") -> tuple[Path, Path, Path]:
        """A bare `origin`, a `seed` checkout used to advance it, and a `clone` standing in for the loop root,
        checked out on `branch` (a `loop/*` name by default - the only kind of root `_sync_loop_branch` acts on).
        Pass `branch=None` to leave the clone on plain `main`, standing in for a root this ADR has not moved."""
        origin = tmp_path / "origin.git"
        cls._git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
        seed = tmp_path / "seed"
        cls._git("clone", str(origin), str(seed), cwd=tmp_path)
        (seed / "file.txt").write_text("v0\n")
        cls._git("add", ".", cwd=seed)
        cls._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "seed", cwd=seed)
        cls._git("push", "origin", "main", cwd=seed)
        clone = tmp_path / "clone"
        cls._git("clone", str(origin), str(clone), cwd=tmp_path)
        cls._git("config", "user.name", "t", cwd=clone)
        cls._git("config", "user.email", "t@t.example", cwd=clone)
        if branch is not None:
            cls._git("checkout", "-b", branch, cwd=clone)
        return origin, seed, clone

    def test_a_plain_non_git_root_is_left_untouched(self, tmp_path: Path) -> None:
        from pravrudhi.application.heartbeat import _sync_loop_branch

        assert _sync_loop_branch(tmp_path) is None

    def test_a_git_root_on_main_is_left_untouched(self, tmp_path: Path) -> None:
        """The lead's own main checkout is a git work tree on `main`, not a loop root. Syncing it unconditionally
        would rebase the tree the lead cherry-picks assistant branches into and the publisher commits `demo.json`
        into - rewriting shas the lead has not yet pushed, mid-merge, and aborting a rebase in a tree someone
        else is actively working in. Only a branch matching `loop/*` is a root this ADR moved deliberately;
        anything else must be left alone by construction, not by the accident of "happens not to be a git repo"."""
        from pravrudhi.application.heartbeat import _sync_loop_branch

        _origin, seed, clone = self._origin_and_clone(tmp_path, branch=None)
        # The team advances origin/main after the clone - if the guard were merely "is this a git repo", this
        # would rebase and silently succeed instead of doing nothing.
        (seed / "team.txt").write_text("team work\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)

        assert _sync_loop_branch(clone) is None
        assert not (clone / "team.txt").exists(), "a root on main must never be synced, only a loop/* root"

    def test_a_git_root_on_a_loop_branch_syncs(self, tmp_path: Path) -> None:
        from pravrudhi.application.heartbeat import _sync_loop_branch

        _origin, seed, clone = self._origin_and_clone(tmp_path)
        (seed / "team.txt").write_text("team work\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)

        assert _sync_loop_branch(clone) is None
        assert (clone / "team.txt").exists(), "a loop/* root must sync with origin/main"

    def test_a_clean_rebase_advances_the_loop_root_onto_origin_main(self, tmp_path: Path) -> None:
        from pravrudhi.application.heartbeat import _sync_loop_branch

        _origin, seed, clone = self._origin_and_clone(tmp_path)

        # The team lands unrelated work on main.
        (seed / "team.txt").write_text("team work\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)

        # The loop has its own unpushed commit, on a different file - no conflict.
        (clone / "loop.txt").write_text("loop work\n")
        self._git("add", ".", cwd=clone)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "loop", cwd=clone)

        result = _sync_loop_branch(clone)

        assert result is None
        assert (clone / "team.txt").exists(), "the rebase must have pulled the team's commit in"
        assert (clone / "loop.txt").exists(), "the loop's own commit must survive, replayed on top"
        log = self._git("log", "--oneline", cwd=clone).stdout
        assert "team" in log and "loop" in log

    def test_a_rebase_that_must_recreate_a_commit_works_with_no_ambient_git_identity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """2026-09-14: Studio's own `loop/studio` checkout had one local commit ahead of `origin/main` (real,
        valid content recovered from a stuck dispatch) and no local `user.name`/`user.email` in that checkout,
        nor any `~/.gitconfig` on the machine at all. Every beat's rebase has to recreate that commit's object
        to replay it on the new `origin/main`, which needs a committer identity - and failed with "Committer
        identity unknown" every single time for over an hour, silently stopping the loop from picking up
        anything from `main` while the label just said "rebase-conflict". `COMMIT_IDENTITY` must be supplied
        by `_sync_loop_branch` itself, not assumed to already be configured in the checkout or the environment
        - this test deliberately configures neither, pointing HOME at an empty directory so no ambient global
        gitconfig on the machine running the test can mask the bug either."""
        from pravrudhi.application.heartbeat import _sync_loop_branch

        _origin, seed, clone = self._origin_and_clone(tmp_path)
        # Unset the identity `_origin_and_clone` configured locally, and point HOME somewhere with no
        # `.gitconfig` at all, so nothing ambient can supply an identity except the fix under test.
        self._git("config", "--unset", "user.name", cwd=clone)
        self._git("config", "--unset", "user.email", cwd=clone)
        empty_home = tmp_path / "empty_home"
        empty_home.mkdir()
        monkeypatch.setenv("HOME", str(empty_home))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_home / "does-not-exist"))

        (seed / "team.txt").write_text("team work\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)

        # The loop's own unpushed commit, made with an explicit identity (as `integrate.py`'s real commits
        # are) so only the REBASE's need for an identity is under test here, not the original commit.
        (clone / "loop.txt").write_text("loop work\n")
        self._git("add", ".", cwd=clone)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "loop", cwd=clone)

        result = _sync_loop_branch(clone)

        assert result is None, f"the rebase must succeed using COMMIT_IDENTITY alone: {result}"
        assert (clone / "team.txt").exists()
        assert (clone / "loop.txt").exists()

    def test_a_conflicting_rebase_aborts_and_reports_rather_than_resolving_itself(self, tmp_path: Path) -> None:
        from pravrudhi.application.heartbeat import _sync_loop_branch

        _origin, seed, clone = self._origin_and_clone(tmp_path)

        # The team changes the same file the loop is about to change.
        (seed / "file.txt").write_text("team version\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team edit", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)

        (clone / "file.txt").write_text("loop version\n")
        self._git("add", ".", cwd=clone)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "loop edit", cwd=clone)

        result = _sync_loop_branch(clone)

        assert result is not None and "loop edit" in result
        # Left clean: no rebase in progress, and the loop's own commit is exactly as it was before the attempt.
        assert not (clone / ".git" / "rebase-merge").exists()
        assert not (clone / ".git" / "rebase-apply").exists()
        assert (clone / "file.txt").read_text() == "loop version\n"

    def test_a_dirty_tree_inside_the_declared_build_scope_is_tagged_own_scope(self, tmp_path: Path) -> None:
        """2026-09-13, cli-lead: a dirty-tree precheck failure is a different problem from a genuine content
        conflict (nothing has even been compared yet), and should be distinguished so a person reading the
        streak knows whether it looks like the loop's own uncommitted build output or something it does not
        recognise. This case: every dirty path is inside the declared `build.allowed_prefixes`."""
        from pravrudhi.application.heartbeat import _sync_loop_branch

        _origin, seed, clone = self._origin_and_clone(tmp_path)
        (seed / ".gitignore").write_text(".pravrudhi/\n")
        (seed / "frontend").mkdir()
        (seed / "frontend" / "app.txt").write_text("v0\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)
        self._git("fetch", "origin", "main", cwd=clone)
        self._git("merge", "origin/main", cwd=clone)  # bring frontend/app.txt in before dirtying the tree
        (clone / ".pravrudhi").mkdir(exist_ok=True)
        (clone / ".pravrudhi" / "config.yaml").write_text(
            "build:\n  allowed_prefixes:\n    - frontend/\n  validate: 'true'\n"
        )
        (seed / "team2.txt").write_text("more team work\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team2", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)
        # A MODIFICATION to an already-tracked file is what actually triggers git's dirty-tree precheck --
        # a bare untracked file does not block a rebase at all (confirmed by hand before writing this test).
        (clone / "frontend" / "app.txt").write_text("uncommitted build output\n")

        detail = _sync_loop_branch(clone)

        assert detail is not None
        assert detail.startswith("dirty-tree(own-scope):")
        assert not (clone / ".git" / "rebase-merge").exists()
        assert (clone / "frontend" / "app.txt").read_text() == "uncommitted build output\n", (
            "nothing here may be discarded"
        )

    def test_a_dirty_tree_outside_the_declared_build_scope_is_tagged_unrecognized(self, tmp_path: Path) -> None:
        """The product loop's actual 2026-09-13 case: `harness/` and `research/` were left dirty from a prior
        mis-rooted period and are outside every declared prefix (`frontend/`, `desktop/`, `engine/`, `scripts/`,
        `docs/`). This must be reported distinctly from `own-scope`, and never auto-resolved."""
        from pravrudhi.application.heartbeat import _sync_loop_branch

        _origin, seed, clone = self._origin_and_clone(tmp_path)
        (seed / ".gitignore").write_text(".pravrudhi/\n")
        (seed / "team.txt").write_text("team work\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)
        self._git("fetch", "origin", "main", cwd=clone)
        self._git("merge", "origin/main", cwd=clone)  # bring .gitignore in before dirtying the tree
        (clone / ".pravrudhi").mkdir(exist_ok=True)
        (clone / ".pravrudhi" / "config.yaml").write_text(
            "build:\n  allowed_prefixes:\n    - frontend/\n  validate: 'true'\n"
        )
        (seed / "team2.txt").write_text("more team work\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team2", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)
        # Production's actual shape (2026-09-13, product loop): a modified TRACKED file outside every declared
        # prefix (there, .gitignore itself) plus untracked stray dirs (harness/, research/). The modification is
        # what actually blocks the rebase; the untracked dir is included here for realism, not to trigger it.
        (clone / ".gitignore").write_text(".pravrudhi/\nharness/\n")
        (clone / "harness").mkdir(exist_ok=True)
        (clone / "harness" / "stray.json").write_text("{}\n")

        detail = _sync_loop_branch(clone)

        assert detail is not None
        assert detail.startswith("dirty-tree(unrecognized):")
        assert (clone / "harness" / "stray.json").exists(), "nothing here may be discarded"
        assert (clone / ".gitignore").read_text() == ".pravrudhi/\nharness/\n", "nothing here may be discarded"

    def test_beat_parks_itself_not_a_criterion_on_a_rebase_conflict(self, tmp_path: Path) -> None:
        from pravrudhi.application import heartbeat

        _origin, seed, clone = self._origin_and_clone(tmp_path)
        (seed / "file.txt").write_text("team version\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team edit", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)
        (clone / "file.txt").write_text("loop version\n")
        self._git("add", ".", cwd=clone)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "loop edit", cwd=clone)

        def dispatch_fn(name: str, model: str | None) -> object:
            raise AssertionError("a rebase conflict must stop the beat before any dispatch is even considered")

        record = heartbeat.beat(clone, dispatch=dispatch_fn)

        assert record.chose is None
        assert record.result is None
        assert "rebase-conflict" in record.reason

    def test_beat_proceeds_normally_after_a_clean_rebase(self, tmp_path: Path) -> None:
        from pravrudhi.application import heartbeat

        _origin, seed, clone = self._origin_and_clone(tmp_path)
        (seed / "team.txt").write_text("team work\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)

        record = heartbeat.beat(clone)

        assert "rebase-conflict" not in record.reason
        assert (clone / "team.txt").exists()

    def _make_persistent_conflict(self, tmp_path: Path) -> Path:
        """A clone whose local unpushed commit conflicts with origin/main every time it is rebased - `beat()`
        aborts and restores it identically, so calling `beat` again reproduces the same conflict, simulating
        consecutive stuck beats without needing a fake clock."""
        _origin, seed, clone = self._origin_and_clone(tmp_path)
        (seed / "file.txt").write_text("team version\n")
        self._git("add", ".", cwd=seed)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "team edit", cwd=seed)
        self._git("push", "origin", "main", cwd=seed)
        (clone / "file.txt").write_text("loop version\n")
        self._git("add", ".", cwd=clone)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "loop edit", cwd=clone)
        return clone

    def test_the_streak_builds_across_beats_and_does_not_alert_before_the_threshold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import heartbeat, notifications

        clone = self._make_persistent_conflict(tmp_path)
        alerts: list[dict[str, object]] = []
        monkeypatch.setattr(notifications, "emit", lambda root, **kw: alerts.append(kw))

        for _ in range(heartbeat.MAX_REBASE_CONFLICTS_BEFORE_ALERT - 1):
            heartbeat.beat(clone)

        assert alerts == [], "must not alert before the streak reaches the threshold"
        assert heartbeat.rebase_conflict_streak(clone) == heartbeat.MAX_REBASE_CONFLICTS_BEFORE_ALERT - 1

    def test_reaching_the_threshold_alerts_so_the_stall_does_not_depend_on_reading_the_journal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-0053 §3 amendment: quiet hours resolve themselves; a rebase conflict does not. A heartbeat alive
        while convergence is zero, with nobody told, is the failure the operator named."""
        from pravrudhi.application import heartbeat, notifications

        clone = self._make_persistent_conflict(tmp_path)
        alerts: list[dict[str, object]] = []
        monkeypatch.setattr(notifications, "emit", lambda root, **kw: alerts.append(kw))

        for _ in range(heartbeat.MAX_REBASE_CONFLICTS_BEFORE_ALERT):
            heartbeat.beat(clone)

        assert len(alerts) == 1
        assert alerts[0]["kind"] == "rebase_conflict_streak"

        # Kept stuck: the next beat alerts again rather than falling silent once the threshold has been crossed.
        heartbeat.beat(clone)
        assert len(alerts) == 2

    def test_a_clean_sync_resets_the_rebase_conflict_streak(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import heartbeat, notifications

        clone = self._make_persistent_conflict(tmp_path)
        monkeypatch.setattr(notifications, "emit", lambda root, **kw: None)
        heartbeat.beat(clone)
        assert heartbeat.rebase_conflict_streak(clone) == 1

        self._git("reset", "--hard", "origin/main", cwd=clone)  # the person resolves it by hand
        heartbeat.beat(clone)
        assert heartbeat.rebase_conflict_streak(clone) == 0


class TestLoopPublishesOnIntegration:
    """pravrudhi-app ADR-0002: the pull side (`_sync_loop_branch` above) already kept a `loop/*` root current
    with `origin/main`, but nothing pushed in the other direction - the product loop closed a real criterion
    and the commit sat unreachable from any remote ref, because nothing ever published it. `_push_loop_branch`
    is that other direction: after a successful build-mode integration, the branch is pushed to `origin` under
    its own name, never `main`."""

    @staticmethod
    def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=check,
        )

    @classmethod
    def _origin_and_clone(cls, tmp_path: Path, *, branch: str | None = "loop/product") -> tuple[Path, Path]:
        origin = tmp_path / "origin.git"
        cls._git("init", "--bare", "-b", "main", str(origin), cwd=tmp_path)
        seed = tmp_path / "seed"
        cls._git("clone", str(origin), str(seed), cwd=tmp_path)
        (seed / "file.txt").write_text("v0\n")
        cls._git("add", ".", cwd=seed)
        cls._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "seed", cwd=seed)
        cls._git("push", "origin", "main", cwd=seed)
        clone = tmp_path / "clone"
        cls._git("clone", str(origin), str(clone), cwd=tmp_path)
        cls._git("config", "user.name", "t", cwd=clone)
        cls._git("config", "user.email", "t@t.example", cwd=clone)
        if branch is not None:
            cls._git("checkout", "-b", branch, cwd=clone)
        return origin, clone

    def test_a_plain_non_git_root_is_left_untouched(self, tmp_path: Path) -> None:
        from pravrudhi.application.heartbeat import _push_loop_branch

        assert _push_loop_branch(tmp_path) is None

    def test_a_git_root_on_main_is_never_pushed(self, tmp_path: Path) -> None:
        """The lead's own main checkout, or any root not yet re-rooted onto `loop/<name>`, must never be
        pushed - only a `loop/*` branch is a root this ADR moved deliberately, and this must never become a
        way to push `main` itself."""
        from pravrudhi.application.heartbeat import _push_loop_branch

        origin, clone = self._origin_and_clone(tmp_path, branch=None)
        (clone / "local.txt").write_text("unpushed local work\n")
        self._git("add", ".", cwd=clone)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "local", cwd=clone)

        assert _push_loop_branch(clone) is None
        remote_log = self._git("log", "--oneline", "main", cwd=origin).stdout
        assert "local" not in remote_log, "a root on main must never be pushed, only a loop/* root"

    def test_a_loop_branch_with_a_new_commit_is_pushed_to_origin_under_its_own_name(self, tmp_path: Path) -> None:
        from pravrudhi.application.heartbeat import _push_loop_branch

        origin, clone = self._origin_and_clone(tmp_path)
        (clone / "criterion.txt").write_text("closed\n")
        self._git("add", ".", cwd=clone)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "criterion closed", cwd=clone)
        local_head = self._git("rev-parse", "HEAD", cwd=clone).stdout.strip()

        result = _push_loop_branch(clone)

        assert result is None
        remote_heads = self._git("ls-remote", "--heads", str(origin), "loop/product", cwd=clone).stdout
        assert local_head in remote_heads, "the loop's branch must now exist on origin under its own name"
        # And never as main - this must not be a backdoor to the branch main deploys and releases from.
        remote_main = self._git("ls-remote", "--heads", str(origin), "main", cwd=clone).stdout
        assert local_head not in remote_main

    def test_a_push_failure_is_reported_not_raised(self, tmp_path: Path) -> None:
        """No origin configured at all (a loop root that was re-rooted but never had a remote set up, or a
        transient network failure in the real case) must come back as a detail string, never an exception -
        the same swallow-and-continue discipline `_sync_loop_branch` uses on the pull side."""
        from pravrudhi.application.heartbeat import _push_loop_branch

        root = tmp_path / "detached"
        self._git("init", "-q", "-b", "loop/product", str(root), cwd=tmp_path)
        self._git("config", "user.name", "t", cwd=root)
        self._git("config", "user.email", "t@t.example", cwd=root)
        (root / "file.txt").write_text("v0\n")
        self._git("add", ".", cwd=root)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "seed", cwd=root)

        result = _push_loop_branch(root)

        assert result is not None and result != ""

    def test_a_successful_build_integration_publishes_the_criterion_immediately(self, tmp_path: Path) -> None:
        """The mechanism end to end: a criterion closes via `integrate.integrate_build_criterion` (exactly as
        `TestIntegrateBuildCriterion` in test_heartbeat_build_mode.py exercises it) inside a `loop/*` root with
        a real origin, then `_push_loop_branch` is what stands between that commit and the fate the product
        loop actually hit - unreachable from any remote ref."""
        from pravrudhi.agents.base import GitWorktreeMixin
        from pravrudhi.application import heartbeat, integrate, requests

        origin, clone = self._origin_and_clone(tmp_path)
        (clone / "src").mkdir()
        (clone / "src" / "mod.py").write_text("VALUE = 1\n")
        self._git("add", ".", cwd=clone)
        self._git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "add mod", cwd=clone)
        requests.capture(
            clone, "make VALUE two", request_id="r-1",
            criteria=[requests.Criterion(text="`src/mod.py` sets VALUE = 2", source="operator", mode="build")],
        )
        task_id = "request:r-1:0"
        wt = clone / ".worktrees" / f"agent-{GitWorktreeMixin.ref_safe(task_id)}"
        wt.parent.mkdir(exist_ok=True)
        self._git("worktree", "add", "-q", "-b", f"agent/{GitWorktreeMixin.ref_safe(task_id)}", str(wt), "HEAD", cwd=clone)
        (wt / "src" / "mod.py").write_text("VALUE = 2\n")

        outcome = integrate.integrate_build_criterion(clone, {task_id: wt}, "r-1", 0, validate="true")
        assert outcome.ok, outcome.why
        push_conflict = heartbeat._push_loop_branch(clone)

        assert push_conflict is None
        remote_heads = self._git("ls-remote", "--heads", str(origin), "loop/product", cwd=clone).stdout
        assert outcome.commit in remote_heads or self._git(
            "rev-parse", "HEAD", cwd=clone,
        ).stdout.strip() in remote_heads


class TestStructuralIncapability:
    """cli-lead, 2026-09-13: 'a fact about the dispatch mode or policy, knowable before the first attempt, that
    makes the criterion's acceptance bar unreachable.' Two categories, checked in order; a third is a new
    branch in `structural_incapability`, not a restructuring. Real motivating case:
    `r-5795501a` criterion 8 on the live Studio loop - proposal mode, brief stating nothing written counts as
    evidence, criterion asking for a recorded Electron-shell run. The two real judged-not-met notes quoted below
    are pulled verbatim from `/home/ss/pravrudhi-loop/.pravrudhi/requests.json`'s notes for that request/index
    (timestamps 2026-09-09T18:49:06.824Z and 2026-09-09T19:23:49.128Z, both stored truncated at 325 chars by
    whatever wrote them - reproduced here exactly as recorded, not retyped from memory)."""

    _R5795501A_C8_TEXT = (
        "Answer the completion review's finding that the demo's 'real clicks' holds for the web app but not the "
        "desktop one, which is a single screenshot captured before anyone clicked. Produce, under this "
        "criterion's proposal directory, a runnable script that drives the Electron shell through an update and "
        "records it, plus the exact command to run it."
    )
    # 2026-09-09T18:49:06.824Z - does not mention what the bar actually is, only that the script isn't finished.
    _R5795501A_REJECTION_1 = (
        "The proposal provides a well-structured Python script framework and clear command examples, but the "
        "script is not ready to actually drive the desktop app through an update as written. The command shown "
        "uses placeholder values (`<path-to-electron-main.js-or-packaged-binary>` and example selectors lik"
    )
    # 2026-09-09T19:23:49.128Z - the one that actually reveals the bar is executed evidence, not a proposal.
    _R5795501A_REJECTION_2 = (
        'The proposal provides well-designed scripts and exact commands, but the README explicitly frames this '
        'as "not that evidence" — it is "a runnable script plus a definition of what output from running it '
        'would count as evidence." The criterion forbids proposals that explain what would meet it; it requi'
    )

    @staticmethod
    def _policy(network: str) -> Policy:
        return Policy(
            id="test", allowed_paths=("proposals/**",), denied_paths=(), network=network,
            tools=(), max_wall_s=1800, validate="true",
        )

    def test_network_category_fires_on_criterion_text_alone_no_rejection_needed(self) -> None:
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("none"),
            criterion_text="State the dataset's actual size and licence from its own Hugging Face page.",
            rejection_text=None,
        )
        assert gap is not None and gap.category == "network"

    def test_network_category_does_not_fire_when_network_is_not_none(self) -> None:
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("provider-only"),
            criterion_text="State the dataset's actual size and licence from its own Hugging Face page.",
            rejection_text=None,
        )
        assert gap is None

    def test_no_evidence_category_fires_on_criterion_text_alone_when_the_bar_is_explicit(self) -> None:
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("none"),
            criterion_text="Produce a script and a recording of an actual run demonstrating the update.",
            rejection_text=None,
        )
        assert gap is not None and gap.category == "no_evidence_in_proposal_mode"

    def test_r5795501a_criterion_8_is_not_caught_by_criterion_text_alone(self) -> None:
        """The live case's own criterion text ('a runnable script ... and records it') does not unambiguously
        state the bar is executed evidence rather than a capable script - this is the asymmetry cli-lead named:
        the mode fact alone proves nothing, and here even the criterion text alone is not enough."""
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("none"),
            criterion_text=self._R5795501A_C8_TEXT, rejection_text=None,
        )
        assert gap is None

    def test_r5795501a_criterion_8_first_rejection_does_not_reveal_the_bar_either(self) -> None:
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("none"),
            criterion_text=self._R5795501A_C8_TEXT, rejection_text=self._R5795501A_REJECTION_1,
        )
        assert gap is None

    def test_r5795501a_criterion_8_second_rejection_reveals_the_bar_the_live_case_this_must_catch(self) -> None:
        """This is the test cli-lead asked for by name: 'if your detector does not catch that one, it has not
        done its job.'"""
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("none"),
            criterion_text=self._R5795501A_C8_TEXT, rejection_text=self._R5795501A_REJECTION_2,
        )
        assert gap is not None and gap.category == "no_evidence_in_proposal_mode"

    def test_ordinary_proposal_mode_criteria_are_not_flagged_by_mode_alone(self) -> None:
        """The asymmetry that matters most: most criteria run in proposal mode and are perfectly satisfiable
        there. A detector that fired on `mode == "proposal"` alone would park nearly the whole backlog."""
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("none"),
            criterion_text="Write a short design note explaining the tradeoffs between option A and option B.",
            rejection_text="The note covers only option A and never mentions option B.",
        )
        assert gap is None

    def test_build_mode_is_unaffected(self) -> None:
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="build", policy=self._policy("none"),
            criterion_text=self._R5795501A_C8_TEXT, rejection_text=self._R5795501A_REJECTION_2,
        )
        assert gap is None

    # 2026-09-13T08:24:36Z, verbatim from the live Studio loop's requests.json note for this same criterion,
    # its third judged-not-met verdict that day: the two earlier categories above (network, no-evidence-in-
    # proposal-mode) both fired the same day on this criterion's *other* rejections, but neither regex matches
    # this one, and it kept re-dispatching on the strength of that miss. cli-lead cleared its attempt counter
    # believing it was "one attribute name from passing" (the `p._electron` bug, fixed by then) - the agent's
    # own README, quoted in the judge's rejection, says the real reason nothing can ever satisfy this criterion
    # from here: no display, browser or Electron runtime exists in the dispatch sandbox to produce a real run.
    _R5795501A_REJECTION_3_NO_SANDBOX_DISPLAY = (
        "The proposal provides a well-designed, syntactically-correct script and the exact command to run it, "
        "but lacks the actual evidence the criterion requires. The README explicitly states \"no events.jsonl, "
        "video, or before/after screenshots from a real Electron run are included here, and none are claimed.\" "
        "It then details why: Playwright and Electron are not available in the sandbox, and no real engine is "
        "running. The only checks performed were script compilation, --help parsing, and a fallback error-path "
        "test—none of which exercise the actual criterion of real clicks driving a desktop update and "
        "producing recorded evidence (events.jsonl entries, video file, before/after screenshots showing state "
        "change). The README itself acknowledges this is a proposal explaining what would satisfy the "
        "criterion, not an actual run satisfying it."
    )

    def test_no_execution_environment_category_is_not_caught_by_the_other_two(self) -> None:
        """Documents the gap this test class exists to close: the real 2026-09-13 verdict above does not match
        either existing regex, which is exactly how `r-5795501a` criterion 8 kept re-consuming beats."""
        from pravrudhi.application.heartbeat import (
            _EXECUTED_EVIDENCE_BAR_RE,
            _PROPOSAL_NOT_EVIDENCE_RE,
        )

        assert not _PROPOSAL_NOT_EVIDENCE_RE.search(self._R5795501A_REJECTION_3_NO_SANDBOX_DISPLAY)
        assert not _EXECUTED_EVIDENCE_BAR_RE.search(self._R5795501A_C8_TEXT)

    def test_no_execution_environment_category_fires_on_the_live_case_this_must_catch(self) -> None:
        """If your detector does not catch this one, it has not done its job - it is the actual verdict text
        that let this criterion keep re-dispatching for a bar no sandbox in this loop can ever reach."""
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("none"),
            criterion_text=self._R5795501A_C8_TEXT,
            rejection_text=self._R5795501A_REJECTION_3_NO_SANDBOX_DISPLAY,
        )
        assert gap is not None and gap.category == "no_execution_environment"

    def test_no_execution_environment_category_does_not_fire_on_an_ordinary_rejection(self) -> None:
        """A rejection that just says the work is incomplete, with no mention of a missing tool or display,
        must not be swept into this category - it would hide a real, fixable gap in the delivered work."""
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="proposal", policy=self._policy("none"),
            criterion_text="Write a short design note explaining the tradeoffs between option A and option B.",
            rejection_text="The note covers only option A and never mentions option B.",
        )
        assert gap is None

    def test_no_execution_environment_fires_in_build_mode_too(self) -> None:
        """Unlike `no_evidence_in_proposal_mode`, a missing display/browser/runtime is missing regardless of
        dispatch mode - the remedy is a capable environment, not a mode change, so this category must not be
        scoped to `mode == "proposal"`."""
        from pravrudhi.application.heartbeat import structural_incapability

        gap = structural_incapability(
            mode="build", policy=self._policy("none"),
            criterion_text=self._R5795501A_C8_TEXT,
            rejection_text=self._R5795501A_REJECTION_3_NO_SANDBOX_DISPLAY,
        )
        assert gap is not None and gap.category == "no_execution_environment"


class TestStructuralIncapabilityDeclinesRatherThanReattempts:
    @staticmethod
    def _setup_criterion(tmp_path: Path, request_id: str = "r-test") -> str:
        req = requests.capture(tmp_path, "do the work", request_id=request_id)
        requests.add_criteria(tmp_path, req.id, [requests.Criterion(
            text=(
                "Produce, under this criterion's proposal directory, a runnable script that drives the "
                "Electron shell through an update and records it, plus the exact command to run it."
            ),
            source="operator",
        )])
        return req.id

    def test_a_dispatch_matching_the_live_r5795501a_case_is_declined_not_reattempted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import heartbeat, swarm

        req_id = self._setup_criterion(tmp_path)

        def accepting_wave(build_agent: object, wave: object, **kw: object) -> list[object]:
            return [swarm.Verdict(
                task_id="req:test:0", agent="test", accepted=True, reasons=[],
                files=["proposals/requests/r-test/0/script.py"],
            )]

        def judge(prompt: str) -> str:
            return (
                "VERDICT: not met\n"
                'The proposal provides well-designed scripts and exact commands, but the README explicitly '
                'frames this as "not that evidence" — it is "a runnable script plus a definition of what output '
                'from running it would count as evidence." The criterion forbids proposals that explain what '
                "would meet it."
            )

        monkeypatch.setattr(swarm, "run_wave", accepting_wave)

        def dispatch_fn(name: str, model: str | None) -> object:
            return object()

        _chose, reason, result = heartbeat._beat_obligations(tmp_path, dispatch_fn, judge=judge)

        assert result is not None and result.get("structural_incapability") == "no_evidence_in_proposal_mode"
        criterion = requests.get(tmp_path, req_id).criteria[0]
        assert criterion.declined is True
        assert not criterion.met
        assert "declined" in reason


class TestCriterionToolchainReachesTheDispatchPrompt:
    """A `toolchain` a criterion carries and nothing ever renders is the same defect as no field at all - it is
    stored, believed, and inert. These prove the field reaches the actual text a dispatched agent reads, in
    both dispatch modes, not merely that `Criterion` can hold the value (see test_requests.py for that)."""

    def test_obligation_prompt_states_it_when_given(self) -> None:
        prompt = heartbeat._obligation_prompt(
            "the ask", "the criterion", "scratch/dir", "true", toolchain="Node.js/@playwright/test",
        )
        assert "Required toolchain: Node.js/@playwright/test" in prompt

    def test_obligation_prompt_omits_the_line_when_unset(self) -> None:
        prompt = heartbeat._obligation_prompt("the ask", "the criterion", "scratch/dir", "true")
        assert "Required toolchain" not in prompt

    def test_build_prompt_states_it_when_given(self) -> None:
        prompt = heartbeat._build_prompt(
            "the ask", "the criterion", ("src/**",), "true", toolchain="Node.js/@playwright/test",
        )
        assert "Required toolchain: Node.js/@playwright/test" in prompt

    def test_build_prompt_omits_the_line_when_unset(self) -> None:
        prompt = heartbeat._build_prompt("the ask", "the criterion", ("src/**",), "true")
        assert "Required toolchain" not in prompt

    def test_task_for_criterion_carries_a_proposal_mode_criterion_s_toolchain_into_its_own_prompt(
        self, tmp_path: Path,
    ) -> None:
        """The real call site (`_beat_obligations`'s own dispatch-building), not just the prompt builder in
        isolation: a criterion read out of `requests.json` with `toolchain` set must produce a `TaskSpec` whose
        `prompt` states it, exactly as a hand-built call to `_obligation_prompt` would."""
        req = requests.capture(tmp_path, "do the work")
        criterion = requests.Criterion(
            text="drive the Electron shell through an update and record it",
            source="engine", toolchain="Node.js/@playwright/test",
        )
        requests.add_criteria(tmp_path, req.id, [criterion])

        _task_id, task = heartbeat._task_for_criterion(tmp_path, req, criterion, 0, "proposal")

        assert "Required toolchain: Node.js/@playwright/test" in task.spec.prompt

    def test_task_for_criterion_omits_the_line_for_an_ordinary_criterion(self, tmp_path: Path) -> None:
        req = requests.capture(tmp_path, "do the work")
        criterion = requests.Criterion(text="write a short design note", source="operator")
        requests.add_criteria(tmp_path, req.id, [criterion])

        _task_id, task = heartbeat._task_for_criterion(tmp_path, req, criterion, 0, "proposal")

        assert "Required toolchain" not in task.spec.prompt


class TestBuildPromptForbidsTheXfailEscape:
    """2026-09-13/14, cli-lead: r-1977143a criterion 2 (wiring `pravrudhi routes`/`agents` and an endpoint to
    real seat state) was judged not-met 10 times in 24h, every time because the dispatched agent wrote a test
    file where every case was `@pytest.mark.xfail(strict=True)` - a specification, not an implementation. The
    prompt's own "a failing test first, then the change" line reads as license to stop after the first half:
    `BUILD_VALIDATE`'s plain `pytest -q tests` treats a strict-xfail suite as passing, so nothing mechanical
    caught it and every attempt burned a full judged round-trip before being told the same thing again."""

    def test_the_prompt_forbids_stopping_at_an_xfail_marked_test(self) -> None:
        prompt = heartbeat._build_prompt("the ask", "the criterion", ("src/**",), "true")
        assert "xfail" in prompt.lower()
        assert "specification" in prompt.lower()
