"""Real git repos, not mocked subprocess: this tool exists to answer a question about real git history, so a
fake git backend would just be re-testing a mock of the thing under test."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from pravrudhi.application.sign_carryover import SignRecordError, check_carryover


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, content in files.items():
        (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(
        repo, "-c", "user.name=test", "-c", "user.email=test@example.com",
        "commit", "-q", "-m", message,
    )
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    return tmp_path


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class TestCarries:
    def test_identical_content_at_both_shas_carries(self, repo: Path) -> None:
        sign_sha = _commit(repo, {"a.py": "x = 1\n"}, "first")
        target_sha = _commit(repo, {"other.py": "unrelated\n"}, "second, a.py untouched")

        record = {"sha": sign_sha, "paths": ["a.py"], "file_sha256": {"a.py": _sha256("x = 1\n")}}
        result = check_carryover(record, target_sha, repo_root=repo)

        assert result.carries
        assert result.changed_paths == ()


class TestReSignNeeded:
    def test_content_changed_between_sign_and_target(self, repo: Path) -> None:
        sign_sha = _commit(repo, {"a.py": "x = 1\n"}, "first")
        target_sha = _commit(repo, {"a.py": "x = 2\n"}, "a.py changed")

        record = {"sha": sign_sha, "paths": ["a.py"], "file_sha256": {"a.py": _sha256("x = 1\n")}}
        result = check_carryover(record, target_sha, repo_root=repo)

        assert not result.carries
        assert result.changed_paths == ("a.py",)
        assert "changed between" in result.reasons["a.py"]

    def test_path_deleted_at_target(self, repo: Path) -> None:
        sign_sha = _commit(repo, {"a.py": "x = 1\n"}, "first")
        (repo / "a.py").unlink()
        _git(repo, "add", "-A")
        target_sha = _commit(repo, {}, "a.py deleted")

        record = {"sha": sign_sha, "paths": ["a.py"], "file_sha256": {"a.py": _sha256("x = 1\n")}}
        result = check_carryover(record, target_sha, repo_root=repo)

        assert not result.carries
        assert result.changed_paths == ("a.py",)
        assert "not found at the target sha" in result.reasons["a.py"]

    def test_inconsistent_sign_record_does_not_carry(self, repo: Path) -> None:
        """The sign record's own file_sha256 doesn't match what was really at its sha -- untrustworthy for
        that path either way, so this must never read as CARRIES."""
        sign_sha = _commit(repo, {"a.py": "x = 1\n"}, "first")
        target_sha = _commit(repo, {"other.py": "unrelated\n"}, "second, a.py untouched")

        record = {"sha": sign_sha, "paths": ["a.py"], "file_sha256": {"a.py": _sha256("wrong content\n")}}
        result = check_carryover(record, target_sha, repo_root=repo)

        assert not result.carries
        assert "inconsistent" in result.reasons["a.py"]

    def test_path_missing_at_the_sign_sha_itself(self, repo: Path) -> None:
        sign_sha = _commit(repo, {"a.py": "x = 1\n"}, "first")
        target_sha = _commit(repo, {"b.py": "y = 2\n"}, "second")

        record = {"sha": sign_sha, "paths": ["never-existed.py"], "file_sha256": {"never-existed.py": _sha256("x")}}
        result = check_carryover(record, target_sha, repo_root=repo)

        assert not result.carries
        assert "not found at the sign's own sha" in result.reasons["never-existed.py"]

    def test_multiple_paths_only_the_changed_one_is_reported(self, repo: Path) -> None:
        sign_sha = _commit(repo, {"a.py": "x = 1\n", "b.py": "y = 1\n"}, "first")
        target_sha = _commit(repo, {"a.py": "x = 2\n"}, "only a.py changed")

        record = {
            "sha": sign_sha,
            "paths": ["a.py", "b.py"],
            "file_sha256": {"a.py": _sha256("x = 1\n"), "b.py": _sha256("y = 1\n")},
        }
        result = check_carryover(record, target_sha, repo_root=repo)

        assert not result.carries
        assert result.changed_paths == ("a.py",)


class TestMalformedRecord:
    def test_missing_required_key_raises(self, repo: Path) -> None:
        sign_sha = _commit(repo, {"a.py": "x = 1\n"}, "first")
        with pytest.raises(SignRecordError, match="sha"):
            check_carryover({"paths": ["a.py"], "file_sha256": {}}, sign_sha, repo_root=repo)

    def test_path_with_no_file_sha256_entry_raises(self, repo: Path) -> None:
        sign_sha = _commit(repo, {"a.py": "x = 1\n"}, "first")
        with pytest.raises(SignRecordError, match="a.py"):
            check_carryover(
                {"sha": sign_sha, "paths": ["a.py"], "file_sha256": {}}, sign_sha, repo_root=repo
            )
