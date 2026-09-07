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

        def regressed(command: str, cwd: Path, timeout_s: int) -> tuple[bool, str]:
            assert command == "uv run pytest -q"
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
