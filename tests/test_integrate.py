"""Two agents changing one file must not end with one of them silently erased.

Merging a wave was a copy per worktree, which is last-writer wins. When two agents both touched a shared file the
second copy reverted the first, and the loss surfaced only when a test failed or a page stopped working. That
happened five times in a single day, and the agents were blameless each time: the fault was in how their work was
collected.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pravrudhi.application.integrate import changed_files, integrate, survey


def _run(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run(root, "git", "init", "-q")
    (root / "shared.py").write_text("def a():\n    return 1\n\n\ndef z():\n    return 26\n")
    (root / "other.py").write_text("x = 1\n")
    _run(root, "git", "add", "-A")
    _run(root, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    return root


def _worktree(root: Path, name: str) -> Path:
    wt = root.parent / name
    _run(root, "git", "worktree", "add", "-q", "-b", name, str(wt))
    return wt


def _commitless_change(wt: Path, path: str, text: str) -> None:
    (wt / path).write_text(text)


class TestNothingIsSilentlyOverwritten:
    def test_two_agents_editing_one_file_are_both_kept(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        one, two = _worktree(root, "one"), _worktree(root, "two")
        _commitless_change(one, "shared.py", "def a():\n    return 111\n\n\ndef z():\n    return 26\n")
        _commitless_change(two, "shared.py", "def a():\n    return 1\n\n\ndef z():\n    return 999\n")

        result = integrate(root, {"one": one, "two": two})

        assert result.ok, result.conflicts
        text = (root / "shared.py").read_text()
        assert "111" in text, "the first agent's change was lost"
        assert "999" in text, "the second agent's change was lost"

    def test_a_contested_file_is_named_before_anything_is_applied(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        one, two = _worktree(root, "one"), _worktree(root, "two")
        _commitless_change(one, "shared.py", "def a():\n    return 111\n\n\ndef z():\n    return 26\n")
        _commitless_change(two, "shared.py", "def a():\n    return 1\n\n\ndef z():\n    return 999\n")
        _commitless_change(two, "other.py", "x = 2\n")

        contested = [c for c in survey(root, {"one": one, "two": two}) if c.contested]
        assert [c.path for c in contested] == ["shared.py"]
        assert set(contested[0].tasks) == {"one", "two"}

    def test_an_irreconcilable_edit_is_reported_rather_than_resolved(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        one, two = _worktree(root, "one"), _worktree(root, "two")
        _commitless_change(one, "shared.py", "def a():\n    return 111\n\n\ndef z():\n    return 26\n")
        _commitless_change(two, "shared.py", "def a():\n    return 222\n\n\ndef z():\n    return 26\n")

        result = integrate(root, {"one": one, "two": two})

        assert not result.ok
        assert any("two" in c for c in result.conflicts)


class TestWhatIsCarriedBack:
    def test_build_products_are_never_integrated(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        one = _worktree(root, "one")
        (one / "node_modules").mkdir()
        (one / "node_modules" / "junk.js").write_text("x")
        (one / "app").mkdir()
        (one / "app" / "real.py").write_text("y = 1\n")

        assert changed_files(one) == ["app/real.py"]

    def test_a_worktree_with_no_change_is_skipped_not_failed(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        one = _worktree(root, "one")
        result = integrate(root, {"one": one})
        assert result.ok
        assert any("no change" in s for s in result.skipped)

    def test_a_missing_worktree_is_skipped_with_its_reason(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        result = integrate(root, {"gone": tmp_path / "nowhere"})
        assert result.ok
        assert any("no worktree" in s for s in result.skipped)

    def test_a_dry_run_changes_nothing(self, tmp_path: Path) -> None:
        root = _repo(tmp_path)
        one = _worktree(root, "one")
        _commitless_change(one, "other.py", "x = 42\n")
        before = (root / "other.py").read_text()

        result = integrate(root, {"one": one}, dry_run=True)

        assert result.applied == ["one"]
        assert (root / "other.py").read_text() == before
