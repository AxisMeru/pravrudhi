"""Declared, repeatable multi-step work: refusals, wave planning, when/needs semantics, and run recording. All
CPU-only; dispatch is a fake callable, never a real agent."""

from __future__ import annotations

import json

import pytest

from pravrudhi.application.delegate import Verdict
from pravrudhi.application.swarm import SwarmTask
from pravrudhi.application.workflows import (
    Workflow,
    WorkflowError,
    WorkflowInput,
    WorkflowStep,
    get,
    load,
    plan,
    run,
    runs_dir,
    validate,
)


def _wf(steps, inputs=()) -> Workflow:
    return Workflow(id="w", title="w", description="", inputs=tuple(inputs), steps=tuple(steps))


def _step(step_id, **kw) -> WorkflowStep:
    kw.setdefault("title", step_id)
    kw.setdefault("brief", "do it")
    kw.setdefault("validate", "true")
    return WorkflowStep(id=step_id, **kw)


# --- validate() refusals ----------------------------------------------------------------------------------


def test_a_cycle_in_needs_is_refused_naming_the_steps():
    wf = _wf([
        _step("a", needs=("b",), allowed_paths=("a.py",)),
        _step("b", needs=("a",), allowed_paths=("b.py",)),
    ])
    with pytest.raises(WorkflowError) as e:
        validate(wf)
    assert "a" in str(e.value) and "b" in str(e.value)
    assert "cycle" in str(e.value)


def test_an_escaping_allowed_path_is_refused():
    wf = _wf([_step("a", allowed_paths=("../etc/passwd",))])
    with pytest.raises(WorkflowError) as e:
        validate(wf)
    assert "a" in str(e.value)
    assert "../etc/passwd" in str(e.value)


def test_an_absolute_allowed_path_is_also_refused():
    wf = _wf([_step("a", allowed_paths=("/etc/passwd",))])
    with pytest.raises(WorkflowError) as e:
        validate(wf)
    assert "/etc/passwd" in str(e.value)


def test_an_undeclared_input_is_refused_naming_the_step_and_input():
    wf = _wf([_step("a", brief="build {module}", allowed_paths=("x.py",))])  # "module" is never declared
    with pytest.raises(WorkflowError) as e:
        validate(wf)
    assert "a" in str(e.value)
    assert "module" in str(e.value)


def test_an_undeclared_input_in_a_when_expression_is_also_refused():
    wf = _wf([_step("a", allowed_paths=("x.py",), when="ship_it")])
    with pytest.raises(WorkflowError) as e:
        validate(wf)
    assert "ship_it" in str(e.value)


def test_a_step_with_no_validate_command_is_refused():
    wf = _wf([WorkflowStep(id="a", title="a", brief="do it", allowed_paths=("x.py",), validate="")])
    with pytest.raises(WorkflowError) as e:
        validate(wf)
    assert "a" in str(e.value)
    assert "validate" in str(e.value)


def test_a_need_naming_an_unknown_step_is_refused():
    wf = _wf([_step("a", needs=("ghost",), allowed_paths=("a.py",))])
    with pytest.raises(WorkflowError) as e:
        validate(wf)
    assert "ghost" in str(e.value)


def test_a_when_expression_may_use_a_dangerous_looking_construct_only_if_it_stays_boolean():
    # __import__('os') is not a Name/Attribute/Compare/BoolOp the evaluator understands, so it is refused
    # outright at validate() time rather than ever reaching the (nonexistent) eval() this module deliberately
    # does not use.
    wf = _wf([_step("a", allowed_paths=("x.py",), when="__import__('os').system('true')")], inputs=())
    with pytest.raises(WorkflowError):
        validate(wf)


def test_a_valid_workflow_passes_validate_without_raising():
    wf = _wf(
        [
            _step("a", allowed_paths=("a.py",)),
            _step("b", needs=("a",), allowed_paths=("b.py",), when="flag"),
        ],
        inputs=(WorkflowInput(name="flag", required=False, default=True, has_default=True),),
    )
    validate(wf)  # must not raise


# --- plan(): waves from needs and from swarm.plan's conflict detection -----------------------------------


def test_plan_orders_a_dependent_step_after_its_predecessor():
    wf = _wf([
        _step("a", allowed_paths=("a.py",)),
        _step("b", needs=("a",), allowed_paths=("b.py",)),
    ])
    waves = plan(wf, {})
    flat_ids = [[s.id for s in wave] for wave in waves]
    assert flat_ids == [["a"], ["b"]]


def test_plan_defers_two_steps_touching_the_same_path_to_separate_waves():
    wf = _wf([
        _step("a", allowed_paths=("shared.py",)),
        _step("b", allowed_paths=("shared.py",)),
    ])
    waves = plan(wf, {})
    assert len(waves) == 2
    assert {s.id for wave in waves for s in wave} == {"a", "b"}
    # never scheduled together, regardless of order
    assert not any({"a", "b"}.issubset({s.id for s in wave}) for wave in waves)


def test_plan_renders_input_placeholders_into_allowed_paths_and_brief():
    wf = _wf(
        [_step("a", brief="build {mod}", allowed_paths=("src/{mod}.py",))],
        inputs=(WorkflowInput(name="mod", required=True),),
    )
    waves = plan(wf, {"mod": "widgets"})
    step = waves[0][0]
    assert step.allowed_paths == ("src/widgets.py",)
    assert step.brief == "build widgets"


def test_plan_refuses_a_rendered_path_that_escapes_once_the_input_is_applied():
    wf = _wf(
        [_step("a", allowed_paths=("{p}",))],
        inputs=(WorkflowInput(name="p", required=True),),
    )
    with pytest.raises(WorkflowError):
        plan(wf, {"p": "../escape.py"})


def test_plan_refuses_a_missing_required_input():
    wf = _wf(
        [_step("a", brief="build {mod}", allowed_paths=("src/{mod}.py",))],
        inputs=(WorkflowInput(name="mod", required=True),),
    )
    with pytest.raises(WorkflowError):
        plan(wf, {})


# --- run(): when / needs / continue_on_failure, with a fake dispatcher ------------------------------------


class _FakeDispatch:
    """Returns a canned Verdict per task id; records every wave it was called with."""

    def __init__(self, verdicts: dict[str, Verdict]) -> None:
        self.verdicts = verdicts
        self.calls: list[list[str]] = []

    def __call__(self, tasks: list[SwarmTask]) -> list[Verdict]:
        self.calls.append([t.spec.task_id for t in tasks])
        return [self.verdicts[t.spec.task_id] for t in tasks if t.spec.task_id in self.verdicts]


def _accept(task_id: str) -> Verdict:
    return Verdict(task_id=task_id, agent="fake", accepted=True, files=["x.py"], wall_s=1.0)


def _reject(task_id: str, reason: str = "no good") -> Verdict:
    return Verdict(task_id=task_id, agent="fake", accepted=False, reasons=[reason])


def test_a_when_that_is_false_skips_with_a_reason_recorded(tmp_path):
    wf = _wf(
        [_step("a", allowed_paths=("a.py",), when="ship_it")],
        inputs=(WorkflowInput(name="ship_it", required=False, default=False, has_default=True),),
    )
    dispatch = _FakeDispatch({})
    record = run(tmp_path, wf, {}, dispatch)

    assert dispatch.calls == []  # never dispatched: the step was skipped before it reached the swarm
    (result,) = record.steps
    assert result.id == "a"
    assert result.state == "skipped"
    assert result.reason  # a reason was recorded, not silence


def test_a_when_that_is_true_still_dispatches(tmp_path):
    wf = _wf(
        [_step("a", allowed_paths=("a.py",), when="ship_it")],
        inputs=(WorkflowInput(name="ship_it", required=False, default=True, has_default=True),),
    )
    dispatch = _FakeDispatch({"a": _accept("a")})
    record = run(tmp_path, wf, {}, dispatch)

    assert dispatch.calls == [["a"]]
    (result,) = record.steps
    assert result.state == "accepted"


def test_a_failed_predecessor_stops_its_dependants_but_not_an_independent_branch(tmp_path):
    wf = _wf([
        _step("a", allowed_paths=("a.py",)),
        _step("b", needs=("a",), allowed_paths=("b.py",)),
        _step("c", allowed_paths=("c.py",)),  # independent of a/b entirely
    ])
    dispatch = _FakeDispatch({"a": _reject("a", "broke everything"), "c": _accept("c")})
    record = run(tmp_path, wf, {}, dispatch)

    by_id = {s.id: s for s in record.steps}
    assert by_id["a"].state == "rejected"
    assert by_id["b"].state == "blocked"
    assert "a" in by_id["b"].reason
    assert by_id["c"].state == "accepted"  # the independent branch was not touched

    # "b" was never handed to the dispatcher at all
    dispatched_ids = {tid for call in dispatch.calls for tid in call}
    assert "b" not in dispatched_ids


def test_continue_on_failure_runs_a_step_despite_a_failed_predecessor(tmp_path):
    wf = _wf([
        _step("a", allowed_paths=("a.py",)),
        _step("b", needs=("a",), allowed_paths=("b.py",), continue_on_failure=True),
    ])
    dispatch = _FakeDispatch({"a": _reject("a"), "b": _accept("b")})
    record = run(tmp_path, wf, {}, dispatch)

    by_id = {s.id: s for s in record.steps}
    assert by_id["a"].state == "rejected"
    assert by_id["b"].state == "accepted"


def test_run_is_recorded_to_disk_under_pravrudhi_workflows_runs(tmp_path):
    wf = _wf([_step("a", allowed_paths=("a.py",))])
    dispatch = _FakeDispatch({"a": _accept("a")})
    record = run(tmp_path, wf, {}, dispatch)

    path = runs_dir(tmp_path) / f"{record.id}.json"
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["workflow_id"] == "w"
    assert on_disk["steps"][0]["id"] == "a"
    assert on_disk["steps"][0]["state"] == "accepted"


# --- the packaged examples ----------------------------------------------------------------------------------


def test_the_packaged_workflows_both_load_and_validate(tmp_path):
    wfs = {wf.id: wf for wf in load(tmp_path)}
    assert "add-a-page" in wfs
    assert "close-a-request" in wfs
    for wf in wfs.values():
        validate(wf)  # must not raise


def test_add_a_page_plans_with_a_page_input(tmp_path):
    wf = get(tmp_path, "add-a-page")
    assert wf is not None
    waves = plan(wf, {"page": "widgets"})
    all_ids = [s.id for wave in waves for s in wave]
    assert all_ids.index("module") < all_ids.index("api-route") < all_ids.index("frontend-page") < all_ids.index("tests")
    module_step = next(s for wave in waves for s in wave if s.id == "module")
    assert module_step.allowed_paths == ("src/pravrudhi/application/widgets.py",)


def test_close_a_request_plans_with_its_inputs(tmp_path):
    wf = get(tmp_path, "close-a-request")
    assert wf is not None
    waves = plan(wf, {"request_id": "r-1", "target_path": "src/pravrudhi/application/foo.py"})
    all_ids = [s.id for wave in waves for s in wave]
    assert all_ids.index("read-criterion") < all_ids.index("do-the-work") < all_ids.index("run-suite") < \
        all_ids.index("attach-evidence")
