"""The judge is handed a list of changed files; some criteria can only be judged against the whole repository.

Found while aiding the product loop on 2026-09-12. `r-55c7083e[0]` asks that every symbol `candidates.ts` still
exports has a caller reachable from a mounted surface — a repository-wide reachability question. The judge
refused it and gave as its reason that the importing files were not "imported by any component shown in the
worktree". Checked against the actual repository, that reason is wrong: `LinkedActivity.tsx` is imported and
rendered by `ObjectiveDetailView.tsx`, and `lib/objective.ts` is imported by four components including
`components/home/ResultBand.tsx`.

The verdict may still have been right on the criterion's real bar (it requires reachability from the *mounted
home surface*, which is narrower than "imported somewhere"). What was wrong is the reasoning basis: the prompt
lists the changed files and says "Read those files", so a judge answering a repo-wide question answers it from a
partial view. A judge reasoning from incomplete information errs in both directions, and a false refusal costs a
loop its convergence exactly as a false acceptance costs it its evidence.

The fix is one sentence telling the judge the listing is what CHANGED and that it may read anything else in its
working directory to check a claim that spans the repository. It deliberately does not touch any of the refusal
instructions, and the second test here exists to keep it that way: the fail-closed properties are what make the
judge worth having, and a prompt edit made to fix a false refusal is exactly when someone might soften them.
"""

from __future__ import annotations

from pravrudhi.application.heartbeat import _judge_prompt


def _prompt() -> str:
    return _judge_prompt(
        "operator asked for something",
        "every exported symbol has a caller reachable from a mounted route",
        ["frontend/src/lib/candidates.ts"],
        where="the agent's worktree",
    )


class TestTheJudgeKnowsItMayLookBeyondTheListing:
    def test_it_says_the_listing_is_what_changed_not_all_it_may_read(self) -> None:
        text = _prompt().lower()
        assert "changed" in text
        assert "working directory" in text, (
            "the judge must be told it can read the rest of the tree, or a repository-wide criterion is "
            "judged from the diff alone"
        )

    def test_the_listed_files_are_still_named(self) -> None:
        """Widening the scope must not lose the pointer to what actually changed."""
        assert "frontend/src/lib/candidates.ts" in _prompt()


class TestTheFailClosedInstructionsSurvive:
    """These are the properties that make the judge worth having. A prompt edit made to fix a false refusal is
    precisely the moment they would get softened by accident, so they are pinned."""

    def test_a_proposal_still_does_not_meet_a_criterion(self) -> None:
        assert "does not meet it" in _prompt()

    def test_the_fabricated_number_refusal_survives(self) -> None:
        text = _prompt()
        assert "no number is stated that the ledger does not contain" in text
        assert "honest about inventing a number" in text

    def test_the_verdict_format_is_unchanged(self) -> None:
        """`_judged` is fail-closed on anything that is not exactly this, so the prompt must keep asking for it."""
        text = _prompt()
        assert "VERDICT: met" in text and "VERDICT: not met" in text

    def test_it_still_says_what_is_missing_for_the_next_attempt(self) -> None:
        assert "the next attempt is given your reason" in _prompt()
