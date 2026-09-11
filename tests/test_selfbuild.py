"""Self-build plans: the kernel/evidence refusal, tier mapping, preview side-effects, and run recording."""

from __future__ import annotations

import json

import pytest
import yaml

from pravrudhi.application.delegate import TaskSpec
from pravrudhi.application.delegation import AGENT_IDENTITIES
from pravrudhi.application.selfbuild import (
    PACKAGED_EXAMPLE,
    BuildRun,
    SelfBuildError,
    close_gate,
    load_plan,
    preview,
    propose_card,
    record_run,
    run_plan,
    run_unattended_cycle,
    runs,
    runs_path,
)
from pravrudhi.application.swarm import SwarmTask


def _task(task_id, allowed_paths, *, why="", validate="true"):
    spec = TaskSpec(task_id=task_id, prompt="p", allowed_paths=tuple(allowed_paths), validate=validate)
    return SwarmTask(spec, "standard", why=why)


def _write_delegation(root, *, active=True):
    cfg_dir = root / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "delegation.yaml").write_text(
        yaml.safe_dump(
            {
                "active": active,
                "granted": "2026-09-11",
                "instruction": "test delegation, not the operator's real one",
                "signature_identity": "agent-for-operator",
                "scope": {"gate_signoff": True},
                "conditions": {},
            }
        )
    )


def _write_plan(tmp_path, tasks):
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump({"tasks": tasks}))
    return path


def test_the_packaged_example_loads_and_is_harmless():
    tasks = load_plan(PACKAGED_EXAMPLE)
    assert len(tasks) == 2
    for t in tasks:
        for pattern in t.spec.allowed_paths:
            assert not pattern.startswith(("pravrudhi_kernel/", "research/", "gates/", ".pravrudhi/"))


def test_a_plan_naming_the_kernel_is_refused_with_that_path_in_the_message(tmp_path):
    path = _write_plan(
        tmp_path,
        [{"id": "sneaky", "prompt": "p", "allowed_paths": ["pravrudhi_kernel/stats.py"]}],
    )
    with pytest.raises(SelfBuildError) as e:
        load_plan(path)
    assert "pravrudhi_kernel/stats.py" in str(e.value)


def test_a_plan_naming_research_gates_or_dotpravrudhi_is_also_refused(tmp_path):
    for protected in ("research/ledger.jsonl", "gates/L1.json", ".pravrudhi/routing.jsonl"):
        path = _write_plan(tmp_path, [{"id": "sneaky", "prompt": "p", "allowed_paths": [protected]}])
        with pytest.raises(SelfBuildError) as e:
            load_plan(path)
        assert protected in str(e.value), protected


def test_tiers_map_from_the_declared_field_with_standard_as_default(tmp_path):
    path = _write_plan(
        tmp_path,
        [
            {"id": "a", "prompt": "p", "allowed_paths": ["src/a.py"], "tier": "mechanical"},
            {"id": "b", "prompt": "p", "allowed_paths": ["src/b.py"], "tier": "critical"},
            {"id": "c", "prompt": "p", "allowed_paths": ["src/c.py"]},
        ],
    )
    tiers = {t.spec.task_id: t.tier for t in load_plan(path)}
    assert tiers == {"a": "mechanical", "b": "critical", "c": "standard"}


def test_preview_shows_what_would_dispatch_without_writing_anything(tmp_path):
    tasks = load_plan(PACKAGED_EXAMPLE)
    items = preview(tasks, tmp_path)
    assert len(items) == len(tasks)
    for item in items:
        assert item["tier"] in {"mechanical", "standard", "design", "critical"}
        assert item["allowed_paths"]
        assert item["agent"]

    assert not runs_path(tmp_path).exists()
    assert list(tmp_path.iterdir()) == []


class OkAgent:
    """A fake agent that always produces an accepted-shaped diff. See tests/test_swarm.py's OkAgent."""

    name = "fake"

    def __init__(self, files):
        self.files = files

    def create_workspace(self, task_id, base_ref="HEAD"):
        import tempfile
        from pathlib import Path

        return Path(tempfile.mkdtemp())

    def run(self, prompt, workspace, timeout_s=60):
        from pravrudhi.agents.base import AgentRun

        return AgentRun(agent=self.name, ok=True, exit_code=0, wall_s=0.1, text="", workspace=workspace)

    def collect_changes(self, workspace):
        from pravrudhi.agents.base import Diff

        return Diff(files=list(self.files))


def test_run_plan_records_one_run_per_task(tmp_path):
    tasks = load_plan(PACKAGED_EXAMPLE)
    results = run_plan(tmp_path, tasks, build_agent=lambda name, model: OkAgent(["x.py"]), log=lambda s: None)

    assert len(results) == len(tasks)
    assert all(isinstance(r, BuildRun) for r in results)
    assert {r.task_id for r in results} == {t.spec.task_id for t in tasks}

    on_disk = runs(tmp_path)
    assert len(on_disk) == len(tasks)
    assert {r.task_id for r in on_disk} == {r.task_id for r in results}


def test_runs_round_trip(tmp_path):
    run = BuildRun(task_id="x", route="fake", accepted=True, wall_s=1.5, files=("a.py",), reasons=())
    record_run(tmp_path, run)

    got = runs(tmp_path)
    assert len(got) == 1
    assert got[0].task_id == "x"
    assert got[0].route == "fake"
    assert got[0].accepted is True
    assert got[0].files == ("a.py",)
    assert got[0].at  # filled in by record_run


def test_propose_card_refuses_a_task_naming_a_protected_path(tmp_path):
    task = _task("sneaky", ["pravrudhi_kernel/stats.py"])

    with pytest.raises(SelfBuildError) as e:
        propose_card(tmp_path, task)

    assert "pravrudhi_kernel/stats.py" in str(e.value)
    assert not (tmp_path / "contracts").exists()  # a refused task leaves nothing on disk


def test_propose_card_writes_the_next_card_shaped_like_l5_product_surface(tmp_path):
    task = _task(
        "build-loom", ["src/pravrudhi/application/loom.py"],
        why="wire the loom surface", validate="uv run pytest -q tests/test_loom.py",
    )

    path = propose_card(tmp_path, task)

    assert path == tmp_path / "contracts" / "L1_build-loom.md"
    text = path.read_text()
    assert text.startswith("# L1 — wire the loom surface\n")
    assert "uv run pytest -q tests/test_loom.py" in text  # the acceptance
    assert "gates/gate_L1.json" in text  # the gate file name

    second = propose_card(tmp_path, _task("second-task", ["src/a.py"]))
    assert second.name.startswith("L2_")  # the next free number, not a restart at L1


def test_run_unattended_cycle_takes_task_and_build_agent_as_keywords_only(tmp_path):
    # Matches the module's own documented shape, `run_unattended_cycle(root, *, build_agent)`: `task` sits
    # alongside `build_agent` after the `*`, not as a second positional argument a caller could get wrong.
    _write_delegation(tmp_path)
    task = _task("build-it", ["x.py"])

    with pytest.raises(TypeError):
        run_unattended_cycle(tmp_path, task, build_agent=lambda name, model: OkAgent(["x.py"]))


def test_run_unattended_cycle_proposes_runs_and_closes_a_passing_build(tmp_path):
    _write_delegation(tmp_path)
    task = _task("build-it", ["x.py"])

    run, gate_path = run_unattended_cycle(
        tmp_path, task=task, build_agent=lambda name, model: OkAgent(["x.py"]), log=lambda s: None,
    )

    assert isinstance(run, BuildRun)
    assert run.accepted is True
    assert gate_path.exists()

    report = json.loads(gate_path.read_text())
    assert report["status"] == "pass"
    assert report["signoff"]["by"] == "agent-for-operator"
    assert report["signoff"]["by"] in AGENT_IDENTITIES

    on_disk = runs(tmp_path)
    assert len(on_disk) == 1
    assert on_disk[0].task_id == "build-it"


def test_close_gate_refuses_when_the_delegation_is_inactive(tmp_path):
    _write_delegation(tmp_path, active=False)
    task = _task("build-it", ["x.py"])

    run, gate_path = run_unattended_cycle(
        tmp_path, task=task, build_agent=lambda name, model: OkAgent(["x.py"]), log=lambda s: None,
    )

    assert run.accepted is True  # the build itself is unaffected; only the close is refused
    report = json.loads(gate_path.read_text())
    assert report["signoff"]["by"] is None  # left unsigned, for a person to close by hand

    with pytest.raises(SelfBuildError, match="delegation"):
        close_gate(tmp_path, gate_path)


def test_close_gate_refuses_when_check_gate_is_not_clean(tmp_path):
    _write_delegation(tmp_path)
    task = _task("build-it", ["x.py"])
    _run, gate_path = run_unattended_cycle(
        tmp_path, task=task, build_agent=lambda name, model: OkAgent(["x.py"]), log=lambda s: None,
    )
    report = json.loads(gate_path.read_text())
    assert report["signoff"]["by"] == "agent-for-operator"  # closed once, cleanly, above

    # Delete the card the gate was emitted against: `check_gate` can no longer find it, so the gate is not
    # clean, and a second close attempt (as an unattended retry might make) must refuse rather than re-sign.
    for card in (tmp_path / "contracts").glob("L1_*.md"):
        card.unlink()

    with pytest.raises(SelfBuildError, match="not clean"):
        close_gate(tmp_path, gate_path)
