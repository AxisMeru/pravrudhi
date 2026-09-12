"""cli-web edited the pravrudhi-app main checkout by mistake twice one evening and caught it by hand both
times -- a control problem, not an attention problem. `.githooks/pre-commit` refuses a commit made directly in
the primary checkout while HEAD is `main`, unless PRAVRUDHI_LEAD_MERGE=1 is set (the lead's own merge flow, the
one legitimate case). A commit in a linked worktree is untouched -- that is where all agent work happens."""

import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "pre-commit"


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30, check=check)


def _make_primary_with_a_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A primary checkout on `main` with one real commit, and a linked worktree of it on its own branch."""
    primary = tmp_path / "primary"
    _git("init", "-b", "main", str(primary), cwd=tmp_path)
    (primary / "f.txt").write_text("v0\n")
    _git("add", ".", cwd=primary)
    _git("-c", "user.name=t", "-c", "user.email=t@t.example", "commit", "-m", "seed", cwd=primary)
    worktree = tmp_path / "worktree"
    _git("worktree", "add", "-b", "assistant/topic", str(worktree), cwd=primary)
    return primary, worktree


def _run_hook(cwd: Path, *, lead_merge: bool = False) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "PRAVRUDHI_LEAD_MERGE"}
    if lead_merge:
        env["PRAVRUDHI_LEAD_MERGE"] = "1"
    return subprocess.run(["bash", str(HOOK)], cwd=cwd, capture_output=True, text=True, env=env)


def test_a_commit_in_a_linked_worktree_succeeds(tmp_path: Path) -> None:
    _primary, worktree = _make_primary_with_a_worktree(tmp_path)
    result = _run_hook(worktree)
    assert result.returncode == 0


def test_a_commit_in_the_primary_checkout_on_main_fails(tmp_path: Path) -> None:
    primary, _worktree = _make_primary_with_a_worktree(tmp_path)
    result = _run_hook(primary)
    assert result.returncode == 1
    assert "PRAVRUDHI_LEAD_MERGE=1" in result.stderr
    assert "worktree" in result.stderr.lower()


def test_the_same_commit_succeeds_with_the_lead_merge_env_var(tmp_path: Path) -> None:
    primary, _worktree = _make_primary_with_a_worktree(tmp_path)
    result = _run_hook(primary, lead_merge=True)
    assert result.returncode == 0


def test_the_primary_checkout_on_a_non_main_branch_is_untouched(tmp_path: Path) -> None:
    """The guard is specifically about `main` in the primary checkout, not every commit there -- a maintenance
    branch checked out directly in the primary tree (no worktree involved) is not the merge-collision risk this
    exists to catch."""
    primary, _worktree = _make_primary_with_a_worktree(tmp_path)
    _git("checkout", "-b", "release/v1", cwd=primary)
    result = _run_hook(primary)
    assert result.returncode == 0
