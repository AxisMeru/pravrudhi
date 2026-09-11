"""Tests for build mode in heartbeat dispatch.

According to the spec (2026-09-11-loop-build-mode.md):
- build_paths_for(text) extracts repo paths from criterion text
- dispatch_mode(criterion) determines "proposal" or "build" mode
- build dispatch uses selfbuild policy with allowed_paths
- integration on MET merges worktree into main, validates, commits, records evidence
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import heartbeat, requests


class TestBuildPathsFor:
    """Test heartbeat.build_paths_for() path extraction."""

    def test_empty_text(self) -> None:
        """Empty or missing text returns ()."""
        assert heartbeat.build_paths_for("") == ()
        assert heartbeat.build_paths_for("  ") == ()

    def test_backticked_paths(self) -> None:
        """Backticked paths are extracted and widened to directory globs."""
        text = "fix the bug in `src/foo.py` and `src/bar.py`"
        paths = heartbeat.build_paths_for(text)
        assert "src/foo.py" in paths or "src/*" in paths
        assert "src/bar.py" in paths or "src/*" in paths

    def test_bare_filenames_under_allowed_prefixes(self) -> None:
        """Bare filenames under allowed prefixes are matched."""
        # src/something.py, tests/test_foo.py, etc.
        text = "update utils.py in src/"
        paths = heartbeat.build_paths_for(text)
        # Should find src-related paths
        assert len(paths) > 0

    def test_protected_prefix_returns_empty(self) -> None:
        """Paths under protected prefixes return ()."""
        text = "edit `pravrudhi_kernel/somefile.py`"
        paths = heartbeat.build_paths_for(text)
        assert paths == ()

        text = "modify `research/results.json`"
        paths = heartbeat.build_paths_for(text)
        assert paths == ()

        text = "change `.pravrudhi/config.yaml`"
        paths = heartbeat.build_paths_for(text)
        assert paths == ()

    def test_tests_glob_included(self) -> None:
        """tests/* is always included as an allowed path."""
        text = "write tests for the new feature"
        paths = heartbeat.build_paths_for(text)
        # tests/* should be in the allowed paths
        assert "tests/*" in paths

    def test_multiple_valid_paths(self) -> None:
        """Multiple valid paths are all returned."""
        text = "update `src/utils.py`, `tests/test_utils.py`, and `app/frontend/src/index.tsx`"
        paths = heartbeat.build_paths_for(text)
        # Should return multiple paths
        assert len(paths) >= 2


class TestDispatchMode:
    """Test heartbeat.dispatch_mode() mode determination."""

    def test_explicit_build_mode(self) -> None:
        """Criterion with mode='build' returns 'build'."""
        criterion = requests.Criterion(text="update `src/foo.py`", mode="build")
        assert heartbeat.dispatch_mode(criterion) == "build"

    def test_proposal_mode_default(self) -> None:
        """Criterion without mode, or with mode='proposal', returns 'proposal'."""
        criterion = requests.Criterion(text="write a README")
        # Default should be proposal
        assert heartbeat.dispatch_mode(criterion) == "proposal"

    def test_build_mode_when_paths_and_code_files(self) -> None:
        """build when paths are named and file is .py/.ts/.tsx/.sh/.yaml/.md."""
        criterion = requests.Criterion(text="update `src/handler.py` to fix the bug")
        # build_paths_for returns non-empty and text names a code file
        # This should auto-determine build mode
        assert heartbeat.dispatch_mode(criterion) == "build"

    def test_proposal_mode_when_no_paths(self) -> None:
        """proposal when build_paths_for returns ()."""
        criterion = requests.Criterion(text="update `research/data.json`")
        # build_paths_for returns () because it's a protected path
        # Should return proposal mode
        assert heartbeat.dispatch_mode(criterion) == "proposal"

    def test_proposal_mode_when_protected_paths(self) -> None:
        """proposal when all paths are protected."""
        criterion = requests.Criterion(text="modify `pravrudhi_kernel/base.py`")
        # Returns proposal because protected
        assert heartbeat.dispatch_mode(criterion) == "proposal"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real repository with one commit, a worktree branched from it, and one request with one criterion."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "src").mkdir()
    (root / "src" / "mod.py").write_text("VALUE = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    requests.capture(root, "make VALUE two", request_id="r-1",
                     criteria=[requests.Criterion(text="`src/mod.py` sets VALUE = 2", source="operator", mode="build")])
    return root


def _worktree(root: Path, task_id: str) -> Path:
    from pravrudhi.agents.base import GitWorktreeMixin

    wt = root / ".worktrees" / f"agent-{GitWorktreeMixin.ref_safe(task_id)}"
    wt.parent.mkdir(exist_ok=True)
    _git(root, "worktree", "add", "-q", "-b", f"agent/{GitWorktreeMixin.ref_safe(task_id)}", str(wt), "HEAD")
    return wt


class TestIntegrateBuildCriterion:
    def test_a_met_build_lands_in_the_main_tree_as_a_commit_that_is_the_evidence(self, repo: Path) -> None:
        from pravrudhi.application import integrate

        wt = _worktree(repo, "request:r-1:0")
        (wt / "src" / "mod.py").write_text("VALUE = 2\n")
        outcome = integrate.integrate_build_criterion(repo, {"request:r-1:0": wt}, "r-1", 0, validate="true")
        assert outcome.ok, outcome.why
        assert (repo / "src" / "mod.py").read_text() == "VALUE = 2\n"
        log = _git(repo, "log", "-1", "--format=%an <%ae>%n%s")
        assert log.splitlines()[0] == "SharathSPhD <qbz506@york.ac.uk>"
        assert log.splitlines()[1] == "`src/mod.py` sets VALUE = 2 (request r-1 criterion 0, built by the loop under ADR-0040)"
        crit = requests.get(repo, "r-1").criteria[0]
        assert crit.met and crit.evidence[0].kind == "commit" and crit.evidence[0].ref == outcome.commit
        assert _git(repo, "status", "--porcelain", "--untracked-files=no").strip() == "", "nothing left uncommitted"

    def test_a_failing_validate_restores_exactly_the_integrated_files_and_meets_nothing(self, repo: Path) -> None:
        from pravrudhi.application import integrate

        (repo / "notes.txt").write_text("a person's uncommitted work\n")
        wt = _worktree(repo, "request:r-1:0")
        (wt / "src" / "mod.py").write_text("VALUE = 2\n")
        (wt / "src" / "new.py").write_text("NEW = 1\n")
        outcome = integrate.integrate_build_criterion(repo, {"request:r-1:0": wt}, "r-1", 0, validate="false")
        assert not outcome.ok and "validate failed" in outcome.why
        assert (repo / "src" / "mod.py").read_text() == "VALUE = 1\n"
        assert not (repo / "src" / "new.py").exists()
        assert (repo / "notes.txt").exists(), "a person's unrelated work must survive the rollback"
        req = requests.get(repo, "r-1")
        assert not req.criteria[0].met and "validate failed" in req.notes[-1]["note"]

    def test_a_conflict_leaves_the_main_tree_untouched_and_is_noted(self, repo: Path) -> None:
        from pravrudhi.application import integrate

        wt = _worktree(repo, "request:r-1:0")
        (wt / "src" / "mod.py").write_text("VALUE = 2\n")
        (repo / "src" / "mod.py").write_text("VALUE = 3\n")  # main moved on the same line
        _git(repo, "commit", "-q", "-am", "main moved")
        outcome = integrate.integrate_build_criterion(repo, {"request:r-1:0": wt}, "r-1", 0, validate="true")
        assert not outcome.ok and "conflict" in outcome.why
        assert (repo / "src" / "mod.py").read_text() == "VALUE = 3\n"
        assert not requests.get(repo, "r-1").criteria[0].met

    def test_a_worktree_that_changed_nothing_is_not_met(self, repo: Path) -> None:
        from pravrudhi.application import integrate

        wt = _worktree(repo, "request:r-1:0")
        outcome = integrate.integrate_build_criterion(repo, {"request:r-1:0": wt}, "r-1", 0, validate="true")
        assert not outcome.ok and "changed nothing" in outcome.why


class TestBuildDispatch:
    def test_a_build_criterion_is_dispatched_with_selfbuild_paths_and_the_design_tier(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The task the beat hands the swarm for a build criterion writes under the named source paths, not
        under proposals/, and validates with the engine's own tests."""
        seen: dict[str, Any] = {}

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            from pravrudhi.application.delegate import Verdict

            task = wave[0]
            seen["allowed"] = task.spec.allowed_paths
            seen["tier"] = task.tier
            seen["validate"] = task.spec.validate
            seen["prompt"] = task.spec.prompt
            return [Verdict(task_id=task.spec.task_id, agent="fake", accepted=False, reasons=["validation failed"],
                            validation_output="x" * 5000 + "E   assert 1 == 2\n1 failed")]

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)
        _chose, _reason, result = heartbeat._beat_obligations(repo, lambda _n, _m=None: object())
        assert result is not None and result["validation_output"].endswith("E   assert 1 == 2\n1 failed")
        assert seen["tier"] == "design"
        assert any(p.startswith("src/") for p in seen["allowed"]) and "tests/*" in seen["allowed"]
        assert not any(p.startswith("proposals") for p in seen["allowed"])
        assert seen["validate"] == heartbeat.BUILD_VALIDATE
        assert "MAKING this change" in seen["prompt"] and "pravrudhi_kernel/" in seen["prompt"]

    def test_a_met_build_verdict_integrates_and_the_evidence_is_the_commit(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import integrate
        from pravrudhi.application.delegate import Verdict

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            task = wave[0]
            wt = _worktree(repo, task.spec.task_id)
            (wt / "src" / "mod.py").write_text("VALUE = 2\n")
            return [Verdict(task_id=task.spec.task_id, agent="fake", accepted=True, files=["src/mod.py"])]

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)
        monkeypatch.setattr(heartbeat, "BUILD_VALIDATE", "true")  # the temp repo has no test suite to run
        assert integrate.BUILD_VALIDATE.startswith("uv run"), "the real command is untouched"
        chose, reason, result = heartbeat._beat_obligations(
            repo, lambda _n, _m=None: object(), judge=lambda prompt: "verdict: met\nthe value is two",
        )
        assert result is not None and result["mode"] == "build" and result["integration"]["ok"], reason
        assert "integrated as" in reason
        assert (repo / "src" / "mod.py").read_text() == "VALUE = 2\n"
        crit = requests.get(repo, "r-1").criteria[0]
        assert crit.met and crit.evidence[0].kind == "commit"


class TestDispatchModeDirectories:
    def test_a_backticked_directory_under_an_allowed_prefix_is_build_mode(self) -> None:
        """r-35e8ce7b criterion 0 named `docs/blueprint/02-design/` and a `.pdf`; no code extension, so it went
        the proposal way twice and the judge refused it twice for writing under proposals/. A named directory the
        loop may write under is a build criterion."""
        criterion = requests.Criterion(
            text="`docs/recursive_self_improvement_sota.pdf` is answered by a committed design document under "
                 "`docs/blueprint/02-design/` that lists every mechanism"
        )
        assert heartbeat.dispatch_mode(criterion) == "build"

    def test_a_desktop_shell_js_file_is_build_mode(self) -> None:
        """r-70e8fe3c's criteria name `app/desktop/preload.js` and sat in proposal mode for four days: the
        extension list knew every language the repo is written in except the desktop shell's."""
        crit = requests.Criterion(text="`app/desktop/preload.js` exposes the `product` bridge")
        assert heartbeat.dispatch_mode(crit) == "build"

    def test_prose_with_no_path_stays_proposal(self) -> None:
        assert heartbeat.dispatch_mode(requests.Criterion(text="explain the plan to the operator")) == "proposal"


class TestUnbuildable:
    def test_a_kernel_path_is_unbuildable_with_the_adr_reason(self, repo: Path) -> None:
        crit = requests.Criterion(text="`pravrudhi_kernel/src/pravrudhi_kernel/efe/` carries more terms")
        why = heartbeat.unbuildable(repo, crit)
        assert why is not None and "ADR-0047" in why and "pravrudhi_kernel/" in why

    def test_paths_ignored_in_this_checkout_are_unbuildable(self, repo: Path) -> None:
        (repo / ".gitignore").write_text("docs/blueprint/\n")
        crit = requests.Criterion(text="a design document under `docs/blueprint/02-design/` lists every mechanism")
        why = heartbeat.unbuildable(repo, crit)
        assert why is not None and "docs/blueprint/02-design/" in why and "ignored" in why

    def test_engine_source_named_in_a_root_without_engine_source_is_unbuildable(self, tmp_path: Path) -> None:
        """The product install's 13:24 beat on 2026-09-11 drafted six criteria naming
        `pravrudhi/application/pool_admin.py`; a wheel install has no such tree to change."""
        root = tmp_path / "product"
        root.mkdir()
        _git(root, "init", "-q", "-b", "main")
        crit = requests.Criterion(
            text="`pravrudhi/application/pool_admin.py` exposes a sealer and `pravrudhi/cli/app.py` registers it"
        )
        why = heartbeat.unbuildable(root, crit)
        assert why is not None and "upstream" in why and "engine source" in why

    def test_an_ordinary_source_path_is_buildable(self, repo: Path) -> None:
        assert heartbeat.unbuildable(repo, requests.Criterion(text="`src/mod.py` sets VALUE = 2")) is None

    def test_the_beat_stalls_an_unbuildable_criterion_without_dispatching(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No model call for a criterion that no dispatch can meet; the budget is spent on paper, the reason
        is on the request, and the beat moves on next time."""
        root = repo.parent / "kernel-only"
        root.mkdir()
        _git(root, "init", "-q", "-b", "main")
        requests.capture(root, "grow the kernel", request_id="r-k",
                         criteria=[requests.Criterion(text="`pravrudhi_kernel/base.py` gains a term", source="operator")])

        def never(*a: Any, **k: Any) -> list[Any]:
            raise AssertionError("dispatched an unbuildable criterion")

        monkeypatch.setattr(heartbeat.swarm, "run_wave", never)
        chose, reason, result = heartbeat._beat_obligations(root, lambda _n, _m=None: object())
        assert result is not None and result["kind"] == "unbuildable", reason
        assert heartbeat.stalled(root, "r-k", 0)
        notes = [str(n.get("note", n)) for n in requests.get(root, "r-k").notes]
        assert any("ADR-0047" in n for n in notes)


class TestPaperStallsDoNotSpendTheBeat:
    def test_after_stalling_an_unbuildable_criterion_the_same_beat_dispatches_the_next(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 13:20 beat on 2026-09-11 stalled one kernel criterion on paper and went home: a 20-minute slot
        spent on no model call. Stalling is bookkeeping; the beat's work is the next buildable criterion."""
        from pravrudhi.application.delegate import Verdict

        requests.capture(repo, "grow the kernel", request_id="r-0", asked_at="2026-09-01T00:00:00Z",
                         criteria=[requests.Criterion(text="`pravrudhi_kernel/base.py` gains a term", source="operator")])
        seen: dict[str, Any] = {}

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            task = wave[0]
            seen["task"] = task.spec.task_id
            return [Verdict(task_id=task.spec.task_id, agent="fake", accepted=False, reasons=["not this test"])]

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)
        heartbeat._beat_obligations(repo, lambda _n, _m=None: object())
        assert seen["task"] == "request:r-1:0", "the buildable criterion was dispatched in the same beat"
        assert heartbeat.stalled(repo, "r-0", 0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
