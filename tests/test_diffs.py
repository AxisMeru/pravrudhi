"""worktree_diff and recent() against a real git repository and worktree -- no mocked git."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pravrudhi.application import diffs

_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.com"}


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=_ENV, check=True)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run(["init", "-q"], root)
    (root / "a.py").write_text("line1\nline2\nline3\n")
    (root / "gone.txt").write_text("bye1\nbye2\n")
    _run(["add", "."], root)
    _run(["commit", "-q", "-m", "initial"], root)


def _add_worktree(root: Path, task_id: str) -> Path:
    wt = root / ".worktrees" / f"agent-{task_id}"
    wt.parent.mkdir(parents=True, exist_ok=True)
    _run(["worktree", "add", "-q", "-b", f"agent/{task_id}", str(wt), "HEAD"], root)
    return wt


def test_a_modified_tracked_file_is_parsed_into_hunks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    wt = _add_worktree(root, "t1")
    (wt / "a.py").write_text("line1\nCHANGED\nline3\n")

    d = diffs.worktree_diff(root, "t1")

    assert d.reason == ""
    assert len(d.base) == 40 and len(d.head) == 40
    assert [f.path for f in d.files] == ["a.py"]
    f = d.files[0]
    assert f.added == 1 and f.removed == 1 and not f.binary and not f.too_large
    assert len(f.hunks) == 1
    hunk = f.hunks[0]
    assert hunk.header.startswith("@@")
    kinds = [line.kind for line in hunk.lines]
    assert "add" in kinds and "del" in kinds and "context" in kinds
    added_text = [line.text for line in hunk.lines if line.kind == "add"]
    assert added_text == ["CHANGED"]


def test_an_untracked_file_is_shown_as_an_addition(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    wt = _add_worktree(root, "t2")
    (wt / "new.py").write_text("brand new\n")

    d = diffs.worktree_diff(root, "t2")

    f = next(f for f in d.files if f.path == "new.py")
    assert f.added == 1 and f.removed == 0
    assert f.hunks[0].lines[0].kind == "add"


def test_a_deleted_file_shows_only_removed_lines(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    wt = _add_worktree(root, "t3")
    (wt / "gone.txt").unlink()

    d = diffs.worktree_diff(root, "t3")

    f = next(f for f in d.files if f.path == "gone.txt")
    assert f.added == 0 and f.removed == 2
    assert all(line.kind == "del" for h in f.hunks for line in h.lines)


def test_a_binary_file_is_flagged_without_hunks(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    wt = _add_worktree(root, "t4")
    (wt / "img.bin").write_bytes(bytes([0, 1, 2, 3, 0, 255]))

    d = diffs.worktree_diff(root, "t4")

    f = next(f for f in d.files if f.path == "img.bin")
    assert f.binary is True
    assert f.hunks == []


def test_a_single_file_is_capped_at_2000_lines(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    wt = _add_worktree(root, "t5")
    (wt / "huge.py").write_text("\n".join(f"line {i}" for i in range(2500)) + "\n")

    d = diffs.worktree_diff(root, "t5")

    f = next(f for f in d.files if f.path == "huge.py")
    assert f.too_large is True
    assert f.added == 2500, "the true count is reported even though the shown hunks are capped"
    assert sum(len(h.lines) for h in f.hunks) <= diffs.MAX_FILE_LINES


def test_the_whole_diff_is_capped_at_400kb_without_dropping_the_file_list(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    wt = _add_worktree(root, "t6")
    (wt / "big.txt").write_text("\n".join("x" * 500 for _ in range(1000)) + "\n")
    (wt / "small.txt").write_text("tiny\n")

    d = diffs.worktree_diff(root, "t6")

    assert d.truncated is True
    big = next(f for f in d.files if f.path == "big.txt")
    small = next(f for f in d.files if f.path == "small.txt")
    assert big.hunks != []
    assert small.too_large is True and small.hunks == []


def test_a_missing_worktree_returns_an_empty_diff_with_a_reason(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)

    d = diffs.worktree_diff(root, "no-such-task")

    assert d.files == [] and d.base == "" and d.head == ""
    assert d.reason != ""


def test_worktree_diff_refuses_a_path_that_would_escape_the_worktrees_directory(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)

    d = diffs.worktree_diff(root, "../../etc")

    assert d.files == []
    # ref_safe collapses every "/" to a hyphen before a path is ever built, so the escape attempt itself never
    # produces a path outside .worktrees/ -- this asserts that guarantee holds, not a particular message.
    assert d.reason != ""


def test_recent_lists_only_dispatched_tasks_with_a_readable_worktree(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)
    _add_worktree(root, "t1")
    (root / ".worktrees" / "agent-t1" / "a.py").write_text("line1\nCHANGED\nline3\n")
    _add_worktree(root, "t2")
    (root / ".worktrees" / "agent-t2" / "new.py").write_text("brand new\n")

    rows = diffs.recent(root, n=10)

    ids = {r.task_id for r in rows}
    assert ids == {"t1", "t2"}
    t1 = next(r for r in rows if r.task_id == "t1")
    assert t1.files == 1 and t1.added == 1 and t1.removed == 1


def test_recent_on_a_workspace_with_no_worktrees_is_empty(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)

    assert diffs.recent(root) == []


def test_stale_agent_worktrees_reports_each_one_and_whether_its_branch_is_merged(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)

    unmerged_wt = _add_worktree(root, "unmerged")
    (unmerged_wt / "new.py").write_text("still open\n")
    _run(["add", "."], unmerged_wt)
    _run(["commit", "-q", "-m", "wip"], unmerged_wt)  # committed on its own branch, never merged to main

    merged_wt = _add_worktree(root, "merged")
    (merged_wt / "shipped.py").write_text("landed\n")
    _run(["add", "."], merged_wt)
    _run(["commit", "-q", "-m", "ship"], merged_wt)
    _run(["merge", "--ff-only", "agent/merged"], root)  # main fast-forwards onto it: an ancestor of HEAD now

    rows = {w.task_id: w for w in diffs.stale_agent_worktrees(root)}

    assert set(rows) == {"unmerged", "merged"}
    assert rows["unmerged"].merged is False and rows["unmerged"].files == 1
    assert rows["merged"].merged is True and rows["merged"].branch == "agent/merged"
    assert all(w.age_days >= 0 for w in rows.values())


def test_stale_agent_worktrees_on_a_workspace_with_none_is_empty(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _init_repo(root)

    assert diffs.stale_agent_worktrees(root) == []
