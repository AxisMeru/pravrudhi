"""Delegation guarantees: disjoint ownership, declared scope, validation before merge."""

from pravrudhi.agents.base import Diff
from pravrudhi.application.delegate import TaskSpec, dispatch, overlapping, validate_in

T_DOC = TaskSpec(task_id="doc", prompt="p", allowed_paths=("src/pravrudhi/application/doctor.py", "tests/test_doctor.py"))
T_HOST = TaskSpec(task_id="host", prompt="p", allowed_paths=("src/pravrudhi/hosts/notes.md",))
T_CLASH = TaskSpec(task_id="clash", prompt="p", allowed_paths=("src/pravrudhi/application/*.py",))


def test_tasks_that_could_collide_are_refused_together():
    assert overlapping([T_DOC, T_HOST]) == []
    assert overlapping([T_DOC, T_CLASH]) == [("doc", "clash")], "a glob covering another task's file is a conflict"
    assert overlapping([T_DOC, T_HOST, T_CLASH]) == [("doc", "clash")]


def test_scope_is_judged_against_the_declaration():
    assert T_DOC.owns("tests/test_doctor.py") and not T_DOC.owns("src/pravrudhi/cli/app.py")
    assert T_DOC.out_of_scope(Diff(files=["tests/test_doctor.py"])) == []
    assert T_DOC.out_of_scope(Diff(files=["tests/test_doctor.py", "README.md"])) == ["README.md"]


class FakeAgent:
    """An agent that writes whatever files it was told to, so the judging can be tested without a model."""

    def __init__(self, root, files, ok=True, text=""):
        self.name, self.root, self.files, self._ok, self._text = "fake", root, files, ok, text

    def create_workspace(self, task_id, base_ref="HEAD"):
        ws = self.root / task_id
        ws.mkdir(parents=True, exist_ok=True)
        return ws

    def run(self, prompt, workspace, timeout_s=60):
        from pravrudhi.agents.base import AgentRun

        for f in self.files:
            p = workspace / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x = 1\n")
        return AgentRun(
            agent=self.name, ok=self._ok, exit_code=0 if self._ok else 1, wall_s=0.1, text=self._text,
            workspace=workspace,
        )

    def collect_changes(self, workspace):
        return Diff(files=list(self.files))


def test_an_in_scope_validated_change_is_accepted(tmp_path):
    task = TaskSpec(task_id="t1", prompt="p", allowed_paths=("a.py",), validate="true")
    v = dispatch(FakeAgent(tmp_path, ["a.py"]), task, log=lambda s: None)
    assert v.accepted and v.reasons == [] and v.files == ["a.py"]


def test_out_of_scope_writes_reject_the_whole_change(tmp_path):
    task = TaskSpec(task_id="t2", prompt="p", allowed_paths=("a.py",), validate="true")
    v = dispatch(FakeAgent(tmp_path, ["a.py", "b.py"]), task, log=lambda s: None)
    assert not v.accepted and any("outside its declared scope" in r for r in v.reasons)


def test_protected_paths_reject_even_when_declared(tmp_path):
    task = TaskSpec(task_id="t3", prompt="p", allowed_paths=("pravrudhi_kernel/*",), validate="true")
    v = dispatch(FakeAgent(tmp_path, ["pravrudhi_kernel/x.py"]), task, log=lambda s: None)
    assert not v.accepted and any("protected" in r for r in v.reasons)


def test_failing_validation_rejects_and_an_empty_diff_rejects(tmp_path):
    fail = TaskSpec(task_id="t4", prompt="p", allowed_paths=("a.py",), validate="false")
    v = dispatch(FakeAgent(tmp_path, ["a.py"]), fail, log=lambda s: None)
    assert not v.accepted and "validation failed" in v.reasons
    empty = TaskSpec(task_id="t5", prompt="p", allowed_paths=("a.py",), validate="true")
    v2 = dispatch(FakeAgent(tmp_path, []), empty, log=lambda s: None)
    assert not v2.accepted and "no change produced" in v2.reasons


def test_an_empty_diff_with_an_explanation_carries_the_agents_own_words(tmp_path):
    """r-799f8dfb c0 (2026-09-12): an agent correctly wrote nothing rather than fabricate the operator's
    verbatim answers to questions nobody had asked yet, said so in its final message, and the verdict recorded
    only the generic "no change produced" -- true, but useless for telling that refusal apart from an agent
    that simply did nothing. The agent's own explanation, when it gave one, belongs in the reason."""
    task = TaskSpec(task_id="t7", prompt="p", allowed_paths=("a.py",), validate="true")
    explanation = "I need your actual answers here before I write anything -- I won't fabricate them."
    v = dispatch(FakeAgent(tmp_path, [], text=explanation), task, log=lambda s: None)
    assert not v.accepted
    assert any(explanation in r for r in v.reasons)
    assert not any(r == "no change produced" for r in v.reasons), "the bare string, with the explanation dropped"


def test_an_empty_diff_with_no_explanation_still_gets_the_plain_reason(tmp_path):
    task = TaskSpec(task_id="t8", prompt="p", allowed_paths=("a.py",), validate="true")
    v = dispatch(FakeAgent(tmp_path, [], text="   "), task, log=lambda s: None)  # blank, not truly empty
    assert not v.accepted and "no change produced" in v.reasons


def test_validation_really_runs_in_the_worktree(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    ok, out = validate_in(tmp_path, "test -f marker.txt && echo FOUND")
    assert ok and "FOUND" in out
    bad, _ = validate_in(tmp_path, "test -f absent.txt")
    assert not bad


def test_owns_treats_trailing_slash_as_directory() -> None:
    from pravrudhi.application.delegate import TaskSpec

    spec = TaskSpec("t", "p", ("app/frontend/src/app/progress/", "src/x.py"), "true", 10)
    assert spec.owns("app/frontend/src/app/progress/page.tsx")
    assert spec.owns("src/x.py")
    assert not spec.owns("app/frontend/src/app/progressive.tsx")
