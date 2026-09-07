"""The completion gate must catch evidence that only looks true, not just evidence that is obviously missing.

A criterion can carry a commit hash nobody made, a file nobody wrote, or a command that used to pass and no longer
does, and `requests.meet` accepts all three because it only checks that *something* was attached. These tests
build fakes for each failure this project actually named the gate to catch — a fabricated commit, a command that
regressed, and an adversarial review that finds a real problem — and confirm a clean, fully-verified case still
passes, and that a failing end-to-end command still blocks it even when every criterion checks out.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pravrudhi.application.completion import (
    EvidenceCheck,
    adversarial_review,
    check_evidence,
    gate,
)
from pravrudhi.application.delegate import TaskSpec
from pravrudhi.application.requests import Criterion, Evidence, RequestError, add_criteria, capture, meet


def _git_repo(root: Path) -> None:
    for args in (["init"], ["config", "user.email", "test@example.com"], ["config", "user.name", "Test"]):
        subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)


def _commit(root: Path, relpath: str, content: str) -> str:
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    subprocess.run(["git", "add", relpath], cwd=root, capture_output=True, text=True, check=True)
    subprocess.run(["git", "commit", "-m", f"touch {relpath}"], cwd=root, capture_output=True, text=True, check=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    return sha


def _reasoned_satisfied(task: TaskSpec) -> str:  # noqa: ARG001 (fake dispatch matches DispatchFn shape)
    return (
        "I checked the commit and the file evidence myself; the referenced commit modifies exactly the widget "
        "file the criterion describes, so the criterion is satisfied because the change is real and on point."
    )


def _bare_satisfied(task: TaskSpec) -> str:  # noqa: ARG001
    return "satisfied"


def _real_finding(task: TaskSpec) -> str:  # noqa: ARG001
    return (
        "The evidence does not satisfy the criterion: the referenced commit only touches an unrelated README "
        "file and never touches the widget module the criterion describes."
    )


class TestCheckEvidence:
    def test_a_fabricated_commit_hash_is_caught(self, tmp_path: Path) -> None:
        _git_repo(tmp_path)
        _commit(tmp_path, "README.md", "hello")
        rid = capture(tmp_path, "fix the widget module").id
        add_criteria(tmp_path, rid, [Criterion(text="widget module works", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("commit", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")])

        check = check_evidence(tmp_path, rid, 0)
        assert isinstance(check, EvidenceCheck)
        assert not check.verified
        assert "no such commit" in check.unverified()[0].reason

    def test_a_real_commit_unrelated_to_the_criterion_is_caught(self, tmp_path: Path) -> None:
        _git_repo(tmp_path)
        sha = _commit(tmp_path, "README.md", "hello")
        rid = capture(tmp_path, "fix the widget module").id
        add_criteria(tmp_path, rid, [Criterion(text="widget module renders correctly", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("commit", sha)])

        check = check_evidence(tmp_path, rid, 0)
        assert not check.verified
        assert "none of which look related" in check.unverified()[0].reason

    def test_a_command_that_no_longer_passes_is_caught(self, tmp_path: Path) -> None:
        _git_repo(tmp_path)
        _commit(tmp_path, "README.md", "hello")
        rid = capture(tmp_path, "the tests pass").id
        add_criteria(tmp_path, rid, [Criterion(text="the suite is green", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("command", "uv run pytest -q", "12 passed at the time")])

        def regressed(argv: list[str], cwd: Path, timeout_s: int) -> tuple[bool, str]:
            # A vetted argv, never a command string: nothing here reaches a shell.
            assert argv == ["uv", "run", "pytest", "-q"]
            return False, "1 failed, 11 passed"

        check = check_evidence(tmp_path, rid, 0, run_command=regressed)
        assert not check.verified
        assert "1 failed" in check.unverified()[0].reason

    def test_a_command_off_the_allow_list_is_reported_unverified_not_run(self, tmp_path: Path) -> None:
        _git_repo(tmp_path)
        rid = capture(tmp_path, "something happened").id
        add_criteria(tmp_path, rid, [Criterion(text="it happened", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("command", "rm -rf /", "trust me")])

        def must_not_run(command: str, cwd: Path, timeout_s: int) -> tuple[bool, str]:
            raise AssertionError("a command off the allow-list must never be executed")

        check = check_evidence(tmp_path, rid, 0, run_command=must_not_run)
        assert not check.verified
        assert "allow-list" in check.unverified()[0].reason

    def test_a_missing_file_is_caught(self, tmp_path: Path) -> None:
        rid = capture(tmp_path, "wrote the doc").id
        add_criteria(tmp_path, rid, [Criterion(text="doc exists", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("file", "docs/nope.md")])

        check = check_evidence(tmp_path, rid, 0)
        assert not check.verified
        assert "no such file" in check.unverified()[0].reason

    def test_an_empty_file_is_caught(self, tmp_path: Path) -> None:
        (tmp_path / "empty.txt").write_text("")
        rid = capture(tmp_path, "wrote the doc").id
        add_criteria(tmp_path, rid, [Criterion(text="doc exists", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("file", "empty.txt")])

        check = check_evidence(tmp_path, rid, 0)
        assert not check.verified
        assert "empty" in check.unverified()[0].reason

    def test_a_ledger_seq_that_does_not_exist_is_caught(self, tmp_path: Path) -> None:
        ledger = tmp_path / "research" / "ledger.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"seq": 1}\n{"seq": 2}\n')
        rid = capture(tmp_path, "recorded in the ledger").id
        add_criteria(tmp_path, rid, [Criterion(text="ledger has it", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("ledger_seq", "99")])

        check = check_evidence(tmp_path, rid, 0)
        assert not check.verified
        assert "no ledger row" in check.unverified()[0].reason

    def test_a_ledger_seq_that_exists_verifies(self, tmp_path: Path) -> None:
        ledger = tmp_path / "research" / "ledger.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text('{"seq": 1}\n{"seq": 2}\n')
        rid = capture(tmp_path, "recorded in the ledger").id
        add_criteria(tmp_path, rid, [Criterion(text="ledger has it", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("ledger_seq", "2")])

        assert check_evidence(tmp_path, rid, 0).verified

    def test_a_reachable_url_verifies_via_the_injected_checker(self, tmp_path: Path) -> None:
        rid = capture(tmp_path, "the page is live").id
        add_criteria(tmp_path, rid, [Criterion(text="page renders", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("url", "https://example.invalid/tour")])

        assert check_evidence(tmp_path, rid, 0, url_check=lambda url, t: (True, f"{url} 200")).verified

    def test_an_unreachable_url_is_caught(self, tmp_path: Path) -> None:
        rid = capture(tmp_path, "the page is live").id
        add_criteria(tmp_path, rid, [Criterion(text="page renders", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("url", "https://example.invalid/tour")])

        check = check_evidence(tmp_path, rid, 0, url_check=lambda url, t: (False, "connection refused"))
        assert not check.verified


class TestAdversarialReview:
    def test_a_bare_satisfied_with_no_reasoning_is_a_failed_review(self, tmp_path: Path) -> None:
        rid = capture(tmp_path, "make the thing work").id
        add_criteria(tmp_path, rid, [Criterion(text="it works", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("file", "x.py")])

        review = adversarial_review(tmp_path, rid, _bare_satisfied)
        assert review.blocking
        assert "without reasoning" in review.reason

    def test_a_real_finding_blocks_the_review(self, tmp_path: Path) -> None:
        rid = capture(tmp_path, "make the thing work").id
        add_criteria(tmp_path, rid, [Criterion(text="it works", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("file", "x.py")])

        review = adversarial_review(tmp_path, rid, _real_finding)
        assert review.blocking

    def test_a_reasoned_satisfaction_does_not_block(self, tmp_path: Path) -> None:
        rid = capture(tmp_path, "make the thing work").id
        add_criteria(tmp_path, rid, [Criterion(text="it works", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("file", "x.py")])

        review = adversarial_review(tmp_path, rid, _reasoned_satisfied)
        assert not review.blocking


class TestGate:
    def _clean_request(self, tmp_path: Path) -> str:
        _git_repo(tmp_path)
        sha = _commit(tmp_path, "widget.py", "def widget(): return 1\n")
        rid = capture(tmp_path, "the widget module works").id
        add_criteria(tmp_path, rid, [Criterion(text="widget module works", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("commit", sha)])
        return rid

    def test_a_clean_case_passes(self, tmp_path: Path) -> None:
        rid = self._clean_request(tmp_path)
        result = gate(tmp_path, rid, dispatch=_reasoned_satisfied, e2e="true")
        assert result.passed, result.reason
        assert result.e2e_passed
        assert all(c.verified for c in result.evidence)

    def test_unverified_evidence_blocks_the_gate_before_any_review_runs(self, tmp_path: Path) -> None:
        _git_repo(tmp_path)
        _commit(tmp_path, "README.md", "hello")
        rid = capture(tmp_path, "fix the widget module").id
        add_criteria(tmp_path, rid, [Criterion(text="widget module works", source="operator")])
        meet(tmp_path, rid, 0, [Evidence("commit", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")])

        def must_not_dispatch(task: TaskSpec) -> str:
            raise AssertionError("the review must not run while evidence is still unverified")

        result = gate(tmp_path, rid, dispatch=must_not_dispatch, e2e="true")
        assert not result.passed
        assert "unverified evidence" in result.reason

    def test_a_review_finding_blocks_the_gate(self, tmp_path: Path) -> None:
        rid = self._clean_request(tmp_path)
        result = gate(tmp_path, rid, dispatch=_real_finding, e2e="true")
        assert not result.passed
        assert "adversarial review" in result.reason
        assert result.review is not None and result.review.blocking

    def test_the_gate_refuses_when_the_e2e_command_fails(self, tmp_path: Path) -> None:
        rid = self._clean_request(tmp_path)
        result = gate(tmp_path, rid, dispatch=_reasoned_satisfied, e2e="false")
        assert not result.passed
        assert "end-to-end check failed" in result.reason
        assert not result.e2e_passed

    def test_a_request_with_no_criteria_cannot_be_gated(self, tmp_path: Path) -> None:
        rid = capture(tmp_path, "something vague").id
        with pytest.raises(RequestError, match="nothing to verify"):
            gate(tmp_path, rid, dispatch=_reasoned_satisfied, e2e="true")

    def test_gating_an_unknown_request_raises(self, tmp_path: Path) -> None:
        with pytest.raises(RequestError, match="no request"):
            gate(tmp_path, "r-nope", dispatch=_reasoned_satisfied, e2e="true")


class TestEvidenceCannotBecomeAnEscalation:
    """Evidence is written by agents, so a reference must never be able to run or fetch what its writer chose."""

    def test_shell_syntax_smuggled_past_the_allow_list_is_refused(self, tmp_path: Path) -> None:
        from pravrudhi.application.completion import _allowed_argv

        patterns = ("uv run pravrudhi *", "uv run pytest*")
        for smuggled in (
            "uv run pravrudhi status; rm -rf ~",
            "uv run pravrudhi status && curl http://example.com",
            "uv run pytest -q | tee /etc/passwd",
            "uv run pravrudhi status `whoami`",
            "uv run pravrudhi status $(id)",
            "uv run pytest -q > /tmp/out",
        ):
            assert _allowed_argv(smuggled, patterns) is None, smuggled

    def test_a_plain_allowed_command_still_runs(self, tmp_path: Path) -> None:
        from pravrudhi.application.completion import _allowed_argv

        match = _allowed_argv("uv run pytest -q", ("uv run pytest*",))
        assert match == (["uv", "run", "pytest", "-q"], "")

    def test_a_subdirectory_check_is_allowed_but_cannot_escape(self, tmp_path: Path) -> None:
        from pravrudhi.application.completion import _allowed_argv

        patterns = ("cd app/desktop && node --test*",)
        assert _allowed_argv("cd app/desktop && node --test test/", patterns) == (
            ["node", "--test", "test/"], "app/desktop",
        )
        assert _allowed_argv("cd ../../etc && node --test", patterns) is None
        assert _allowed_argv("cd /etc && node --test", patterns) is None

    def test_a_url_on_this_machine_or_network_is_refused(self, tmp_path: Path) -> None:
        from pravrudhi.application.completion import _default_url_check

        for blocked in (
            "http://127.0.0.1:8200/api/health",
            "http://localhost/admin",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
            "http://[::1]/",
        ):
            ok, why = _default_url_check(blocked, 2)
            assert not ok, blocked
            assert why, blocked


class TestAReviewJudgesTheCodeAsItStands:
    """A reviewer reused its first worktree and judged that stale tree ever after.

    An agent workspace is reused when one already exists for its task id, which suits a builder resuming work.
    For a reviewer it is fatal: one reported, correctly for what it could see, that a page was missing — thirty
    five commits after it had been added. The task id now carries the commit under review.
    """

    def test_the_task_id_changes_with_the_commit(self, tmp_path: Path) -> None:
        import subprocess

        from pravrudhi.application import completion
        from pravrudhi.application.requests import Criterion, add_criteria, capture

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / "a.txt").write_text("one")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first"],
            cwd=tmp_path, check=True,
        )
        req = capture(tmp_path, "do the thing")
        add_criteria(tmp_path, req.id, [Criterion(text="a criterion", source="operator")])

        seen: list[str] = []

        def dispatch(task: object) -> str:
            seen.append(str(getattr(task, "task_id", "")))
            return "satisfied, and here is the reasoning at sufficient length to count as reasoned rather than a "
            "bare assertion of completion"

        completion.adversarial_review(tmp_path, req.id, dispatch)
        (tmp_path / "a.txt").write_text("two")
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "second"],
            cwd=tmp_path, check=True,
        )
        completion.adversarial_review(tmp_path, req.id, dispatch)

        assert len(seen) == 2
        assert seen[0] != seen[1], "a new commit must get a new review workspace, not the first one again"
