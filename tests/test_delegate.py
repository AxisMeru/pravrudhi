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


def test_a_nonzero_exit_falls_back_to_run_text_when_stderr_tail_is_empty(tmp_path):
    """ClaudeCodeAgent._attempt reports its own errors via a JSON envelope on stdout
    (`is_error: true`), landing the real explanation in `run.text`, not `run.stderr_tail`
    (the raw process stderr, legitimately empty for this failure mode -- claude's CLI
    doesn't write its own errors there). Before this fix, `dispatch` only ever read
    `stderr_tail`, so this exact failure mode always produced the useless literal
    "no detail" even though the real reason was sitting in `run.text` the whole time."""
    task = TaskSpec(task_id="t9", prompt="p", allowed_paths=("a.py",), validate="true")
    real_error = "Error: No messages returned from query"
    v = dispatch(FakeAgent(tmp_path, [], ok=False, text=real_error), task, log=lambda s: None)
    assert not v.accepted
    assert any(real_error in r for r in v.reasons)
    assert not any("no detail" in r for r in v.reasons)


def test_validation_really_runs_in_the_worktree(tmp_path):
    (tmp_path / "marker.txt").write_text("here")
    ok, out = validate_in(tmp_path, "test -f marker.txt && echo FOUND")
    assert ok and "FOUND" in out
    bad, _ = validate_in(tmp_path, "test -f absent.txt")
    assert not bad


def test_scratch_files_are_never_out_of_scope() -> None:
    """cli-web/cli-lead, 2026-09-14: the product loop's root (pravrudhi-app) has no `.pravrudhi-scratch/`
    .gitignore entry (unlike this engine's own repo, where it is), so a tool the agent ran there - `uv`
    writing a lock file into `.pravrudhi-scratch/uv-*.lock` - can end up committed and then show up in the
    diff, even though `dispatch`'s own brief told the agent this exact path was writable scratch. r-55c7083e:3
    was rejected whole this way 8 times in one morning on the product loop."""
    from pravrudhi.agents.base import SCRATCH_DIRNAME

    task = TaskSpec(task_id="t", prompt="p", allowed_paths=("a.py",))
    assert task.out_of_scope(Diff(files=["a.py", f"{SCRATCH_DIRNAME}/uv-1234.lock"])) == []
    assert task.out_of_scope(Diff(files=[f"{SCRATCH_DIRNAME}/nested/dir/file.txt"])) == []
    # A real out-of-scope file is still caught; the carve-out is scratch-only, not a general amnesty.
    assert task.out_of_scope(Diff(files=["a.py", "b.py", f"{SCRATCH_DIRNAME}/uv.lock"])) == ["b.py"]


def test_a_scratch_lockfile_committed_by_the_agent_no_longer_rejects_the_whole_dispatch(tmp_path) -> None:
    """The end-to-end path `test_scratch_files_are_never_out_of_scope` proves in isolation: a diff that
    includes a scratch file alongside real, in-scope work must still be accepted."""
    from pravrudhi.agents.base import SCRATCH_DIRNAME

    task = TaskSpec(task_id="t11", prompt="p", allowed_paths=("a.py",), validate="true")
    v = dispatch(FakeAgent(tmp_path, ["a.py", f"{SCRATCH_DIRNAME}/uv-1234.lock"]), task, log=lambda s: None)
    assert v.accepted, v.reasons


def test_escape_into_the_main_checkout_is_still_caught_after_the_scratch_carve_out(tmp_path) -> None:
    """The scratch carve-out lives in `out_of_scope()`, not `owns()` - `owns()` also drives `dispatch`'s
    separate main-checkout escape detector (line ~177), and this proves that detector is untouched: a file
    written straight into the main root under a declared path is still flagged as an escape, scratch or not."""
    import subprocess

    root = tmp_path
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, capture_output=True)
    (root / "README.md").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True)

    class EscapingAgent(FakeAgent):
        def run(self, prompt, workspace, timeout_s=60):
            (self.root / "a.py").write_text("x = 1\n")  # writes into root, not its own worktree
            return super().run(prompt, workspace, timeout_s)

        def collect_changes(self, workspace):
            return Diff(files=[])

    task = TaskSpec(task_id="t12", prompt="p", allowed_paths=("a.py",), validate="true")
    v = dispatch(EscapingAgent(root, []), task, log=lambda s: None)

    assert not v.accepted
    assert any("into the main checkout" in r for r in v.reasons)


def test_owns_treats_trailing_slash_as_directory() -> None:
    from pravrudhi.application.delegate import TaskSpec

    spec = TaskSpec("t", "p", ("app/frontend/src/app/progress/", "src/x.py"), "true", 10)
    assert spec.owns("app/frontend/src/app/progress/page.tsx")
    assert spec.owns("src/x.py")
    assert not spec.owns("app/frontend/src/app/progressive.tsx")


def test_dispatch_gives_the_agent_a_scratch_dir_and_cleans_it_up(tmp_path):
    """r-5795501a c8 (2026-09-13): a dispatched agent tried `mkdir -p /tmp/pdws` for a scratch workspace and was
    auto-rejected for touching an external directory, then scored as a dispatch failure ("no change produced")
    for a want the sandbox was right to refuse. The defect was that the agent had no legal place to put scratch
    state at all -- so `dispatch` must give it one inside its own worktree, tell it where, and clean it up after."""
    from pravrudhi.agents.base import SCRATCH_DIRNAME

    seen: dict = {}

    class ScratchCheckingAgent(FakeAgent):
        def run(self, prompt, workspace, timeout_s=60):
            seen["prompt"] = prompt
            seen["scratch_existed_during_run"] = (workspace / SCRATCH_DIRNAME).is_dir()
            return super().run(prompt, workspace, timeout_s)

    task = TaskSpec(task_id="t9", prompt="p", allowed_paths=("a.py",), validate="true")
    agent = ScratchCheckingAgent(tmp_path, ["a.py"])
    v = dispatch(agent, task, log=lambda s: None)

    assert v.accepted
    assert seen["scratch_existed_during_run"], "the agent's own worktree had no writable scratch dir during the run"
    assert SCRATCH_DIRNAME in seen["prompt"], "the brief never told the agent where its scratch dir is"
    ws = tmp_path / "t9"
    assert not (ws / SCRATCH_DIRNAME).exists(), "the scratch dir must be discarded after the dispatch, not left behind"

    # Named in the SAME sentence as the restriction, not a separate one elsewhere in the brief (a real dispatch
    # prompt read exactly this way as self-contradictory before this was fixed, 2026-09-13): the scratch entry
    # must sit inside the "ONLY these paths" list itself, no period between the restriction and the exception.
    only_line = next(ln for ln in seen["prompt"].splitlines() if "ONLY these paths" in ln)
    assert f"{SCRATCH_DIRNAME}/**" in only_line, "scratch must be IN the allow-list line, not a later sentence"

    # loop_agent.py/hosted_agent.py's ALLOWED_PATHS_LINE regex parses this exact sentence to learn what a
    # non-Claude-Code agent may write -- the scratch entry must survive as a clean, separate token, not prose
    # that corrupts the comma-split.
    from pravrudhi.agents.loop_agent import _allowed_patterns

    assert _allowed_patterns(seen["prompt"]) == ("a.py", f"{SCRATCH_DIRNAME}/**")
