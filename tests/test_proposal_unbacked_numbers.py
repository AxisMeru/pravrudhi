"""S1b: a proposal that states a number as a result, while admitting the number is not a real measurement, must
not be judged met - even when the judge itself says met.

r-3981d7e0 criterion 5 was dispatched, accepted, and judged met on a README that stated its own numbers were
"fabricated to exercise the scripts". CHARTER §6 is unqualified: no number is stated that the ledger does not
contain. Admitting the number is invented in the same sentence is not an exception to that rule, and nothing in
`_obligation_prompt` or `_judge_prompt` told the dispatching agent or the judge otherwise - the obligation prompt's
only guard was "no number you state may be presented as a result", easily satisfied on paper by a number the file
frames as illustrative or as existing only "to exercise" a script, and the judge prompt said nothing about numbers
at all.

Because the judge is an LLM reading prose, and one already read straight past the existing guard, the fix is two
layered: stronger wording in both prompts (`_obligation_prompt`, `_judge_prompt`), and a deterministic backstop in
`_beat_obligations` (`_first_unevidenced_claim` / `_self_declared_fabrication`) that overrides a MET verdict
whenever a produced file admits, in its own words, that a stated number is not a real measurement - independent
of whatever the judge said.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import heartbeat, requests


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")
    (root / "README.md").write_text("hello\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    requests.capture(
        root, "measure whether the harness improves the score", request_id="r-1",
        criteria=[requests.Criterion(text="a proposal shows the harness measurably improves the score", source="operator")],
    )
    return root


def _worktree(root: Path, task_id: str) -> Path:
    from pravrudhi.agents.base import GitWorktreeMixin

    wt = root / ".worktrees" / f"agent-{GitWorktreeMixin.ref_safe(task_id)}"
    wt.parent.mkdir(exist_ok=True)
    _git(root, "worktree", "add", "-q", "-b", f"agent/{GitWorktreeMixin.ref_safe(task_id)}", str(wt), "HEAD")
    return wt


class TestFabricatedNumberIsNeverMet:
    def test_a_judge_that_says_met_is_overridden_when_the_readme_admits_the_number_is_fabricated(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This is the actual incident: the judge (whatever it was given) said met. The engine must not take its
        word for it when the evidence itself says the number was invented."""
        from pravrudhi.application.delegate import Verdict

        readme = (
            "Approach: run the harness twice and compare scores.\n"
            "Result: score improved from 0.41 to 0.63 (numbers fabricated to exercise the scripts).\n"
        )

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            task = wave[0]
            wt = _worktree(repo, task.spec.task_id)
            scratch = wt / "proposals" / "requests" / "r-1" / "0"
            scratch.mkdir(parents=True)
            (scratch / "README.md").write_text(readme)
            return [Verdict(
                task_id=task.spec.task_id, agent="fake", accepted=True,
                files=["proposals/requests/r-1/0/README.md"],
            )]

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)

        # The judge says met - reproducing the actual failure, not a hypothetical one: a real LLM judge already
        # accepted this exact shape of claim once in production.
        _chose, reason, result = heartbeat._beat_obligations(
            repo, lambda _n, _m=None: object(),
            judge=lambda **_kw: "VERDICT: met\nthe README shows the score improving.",
        )

        assert result is not None and result["judged"] == "not met", reason
        assert "fabricat" in reason.lower()
        criterion = requests.get(repo, "r-1").criteria[0]
        assert criterion.met is False, "a self-declared fabricated number must never satisfy a criterion"

    @pytest.mark.parametrize(
        "phrase", ["simulated", "a hypothetical result", "made up", "a placeholder value",
                   "dummy data", "for illustration", "to exercise the script"],
    )
    def test_every_admission_phrase_is_caught(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch, phrase: str
    ) -> None:
        readme = f"Result: 0.90 accuracy ({phrase}).\n"

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            from pravrudhi.application.delegate import Verdict

            task = wave[0]
            wt = _worktree(repo, task.spec.task_id)
            scratch = wt / "proposals" / "requests" / "r-1" / "0"
            scratch.mkdir(parents=True)
            (scratch / "README.md").write_text(readme)
            return [Verdict(
                task_id=task.spec.task_id, agent="fake", accepted=True,
                files=["proposals/requests/r-1/0/README.md"],
            )]

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)
        _chose, _reason, result = heartbeat._beat_obligations(
            repo, lambda _n, _m=None: object(), judge=lambda **_kw: "VERDICT: met\nfine",
        )
        assert result is not None and result["judged"] == "not met", phrase

    def test_a_real_measurement_with_no_admission_is_unaffected(
        self, repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The backstop must not punish an honest proposal that names a real number with no fabrication marker."""
        readme = "Approach: run pytest and read the summary line.\nEvidence: 42 passed, 0 failed.\n"

        def fake_run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Any]:
            from pravrudhi.application.delegate import Verdict

            task = wave[0]
            wt = _worktree(repo, task.spec.task_id)
            scratch = wt / "proposals" / "requests" / "r-1" / "0"
            scratch.mkdir(parents=True)
            (scratch / "README.md").write_text(readme)
            return [Verdict(
                task_id=task.spec.task_id, agent="fake", accepted=True,
                files=["proposals/requests/r-1/0/README.md"],
            )]

        monkeypatch.setattr(heartbeat.swarm, "run_wave", fake_run_wave)
        _chose, reason, result = heartbeat._beat_obligations(
            repo, lambda _n, _m=None: object(), judge=lambda **_kw: "VERDICT: met\nreal test output, evidenced",
        )
        assert result is not None and result["judged"] == "met", reason


class TestPromptsNameTheRule:
    def test_the_obligation_prompt_forbids_an_illustrative_or_fabricated_number(self) -> None:
        prompt = heartbeat._obligation_prompt("stand it up", "the thing stands up", "scratch", "true")
        low = prompt.lower()
        assert "charter" in low
        assert "fabricat" in low and "simulat" in low and "placeholder" in low

    def test_the_judge_prompt_tells_the_judge_to_refuse_a_self_declared_fabrication(self) -> None:
        prompt = heartbeat._judge_prompt("stand it up", "the thing stands up", ["a.md"])
        low = prompt.lower()
        assert "charter" in low
        assert "fabricat" in low or "simulat" in low
