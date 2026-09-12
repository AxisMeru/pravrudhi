"""Amending a criterion's text as a sublation: a named fragment translated, with the reason and the original kept.

Why this exists. On 2026-09-12 the product loop closed nothing for a full day, and the last remaining cause was
that `r-55c7083e`'s six criteria name `app/frontend/src/...` -- the Studio repository's layout -- while the files
they refer to live in the product repository at `frontend/src/...`. The request is correctly filed; only its path
strings are wrong. `build_paths_for` therefore finds no reachable path, every dispatch auto-detects as proposal
mode, the agent can only describe what it would write, and the judge correctly refuses a proposal as evidence,
hourly, forever.

There was no sanctioned way to fix it. `pravrudhi requests` offers show / set-mode / mark-met / decline, and S14
built that surface precisely so criteria are never hand-edited in `.pravrudhi/requests.json`. So the fix was
blocked not by judgement but by a missing tool, and hand-editing to get around the safeguard that exists to
prevent hand-editing would have been the wrong trade.

The design constraint that makes this safe: it replaces a NAMED fragment, it does not rewrite the text. A caller
must say exactly which words change, the fragment must occur exactly once, and the original text is kept on the
record. An operator's ask can therefore be translated between layouts but never quietly replaced by a different
ask -- which is the whole reason S14 refused hand-edits in the first place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application import requests


def _seeded(tmp_path: Path, text: str) -> str:
    req = requests.capture(tmp_path, "do the work", request_id="r-amend")
    requests.add_criteria(tmp_path, req.id, [requests.Criterion(text=text, source="operator")])
    return req.id


class TestAmendTranslatesANamedFragment:
    def test_the_named_fragment_is_replaced_and_the_rest_is_untouched(self, tmp_path: Path) -> None:
        rid = _seeded(tmp_path, "`app/frontend/src/lib/candidates.ts` reaches one of exactly two end states")
        requests.amend_criterion(
            tmp_path, rid, 0, old="app/frontend/src/", new="frontend/src/",
            why="path translated from Studio layout; request cites pravrudhi-app@7e30ef7",
        )
        after = requests.get(tmp_path, rid)
        assert after is not None
        assert after.criteria[0].text == "`frontend/src/lib/candidates.ts` reaches one of exactly two end states"

    def test_the_original_text_is_kept_so_the_sublation_is_auditable(self, tmp_path: Path) -> None:
        """CHARTER §6: an update is a sublation with reasons. A sublation preserves what it supersedes."""
        original = "`app/frontend/src/lib/home.ts` keeps its null-vs-empty contract"
        rid = _seeded(tmp_path, original)
        requests.amend_criterion(
            tmp_path, rid, 0, old="app/frontend/src/", new="frontend/src/", why="layout translation",
        )
        after = requests.get(tmp_path, rid)
        assert after is not None
        assert after.criteria[0].amended_from == original

    def test_the_reason_and_actor_are_kept_on_the_request(self, tmp_path: Path) -> None:
        rid = _seeded(tmp_path, "touch `app/frontend/src/x.ts`")
        requests.amend_criterion(
            tmp_path, rid, 0, old="app/frontend/", new="frontend/", why="layout translation", actor="cli-lead",
        )
        after = requests.get(tmp_path, rid)
        assert after is not None
        note = after.notes[-1]["note"]
        assert "cli-lead" in note and "layout translation" in note and "criterion[0]" in note


class TestAmendRefusesWhatWouldNotBeATranslation:
    def test_a_fragment_that_is_not_present_is_refused(self, tmp_path: Path) -> None:
        rid = _seeded(tmp_path, "touch `frontend/src/x.ts`")
        with pytest.raises(requests.RequestError, match="does not appear"):
            requests.amend_criterion(tmp_path, rid, 0, old="app/frontend/", new="frontend/", why="w")

    def test_every_occurrence_can_be_translated_when_the_caller_says_so(self, tmp_path: Path) -> None:
        """`all_=True` is not a loosening of the uniqueness guard, it is the other well-defined operation.

        "Replace one of three, you pick" is a guess about the operator's meaning and stays refused. "Replace
        every occurrence of this exact fragment" is unambiguous. The real criteria this command was written for
        each name their path two or three times, so without this the safe path is unusable on the actual case.
        """
        rid = _seeded(tmp_path, "`app/frontend/src/a.ts` and `app/frontend/src/b.ts` and `app/frontend/src/c.ts`")
        requests.amend_criterion(
            tmp_path, rid, 0, old="app/frontend/", new="frontend/", why="layout translation", all_=True,
        )
        after = requests.get(tmp_path, rid)
        assert after is not None
        assert "app/frontend/" not in after.criteria[0].text
        assert after.criteria[0].text.count("frontend/src/") == 3

    def test_an_ambiguous_fragment_is_refused_rather_than_guessed(self, tmp_path: Path) -> None:
        """Two occurrences means the caller has not said which one they meant. Replacing both, or the first,
        would be a guess about the operator's words -- so it refuses and makes them name a unique fragment."""
        rid = _seeded(tmp_path, "move `app/a.ts` and `app/b.ts`")
        with pytest.raises(requests.RequestError, match="occurs 2 times"):
            requests.amend_criterion(tmp_path, rid, 0, old="app/", new="frontend/", why="w")

    def test_a_reason_is_required(self, tmp_path: Path) -> None:
        rid = _seeded(tmp_path, "touch `app/x.ts`")
        with pytest.raises(requests.RequestError, match="reason"):
            requests.amend_criterion(tmp_path, rid, 0, old="app/", new="frontend/", why="   ")

    def test_a_met_criterion_is_refused_because_its_evidence_was_judged_against_these_words(
        self, tmp_path: Path
    ) -> None:
        """Same guard `decline_criterion` has, for a stronger reason: evidence was accepted against the text as
        it read, so changing the text afterwards would silently re-point real evidence at a different ask."""
        rid = _seeded(tmp_path, "touch `app/x.ts`")
        requests.meet(tmp_path, rid, 0, [requests.Evidence(kind="commit", ref="deadbee")])
        with pytest.raises(requests.RequestError, match="already met"):
            requests.amend_criterion(tmp_path, rid, 0, old="app/", new="frontend/", why="w")

    def test_an_unknown_criterion_is_refused(self, tmp_path: Path) -> None:
        rid = _seeded(tmp_path, "touch `app/x.ts`")
        with pytest.raises(requests.RequestError, match="no criterion"):
            requests.amend_criterion(tmp_path, rid, 9, old="app/", new="frontend/", why="w")


class TestAmendMakesTheCriterionReachable:
    def test_translating_the_path_makes_the_dispatcher_able_to_reach_it(self, tmp_path: Path) -> None:
        """The end the whole command exists for, tested against the function that actually decides dispatch mode.

        This reproduces `r-55c7083e` on the product root: a root declaring `frontend/`, and a criterion naming
        the same file under Studio's `app/frontend/` layout. Before the translation `build_paths_for` finds
        nothing, so the criterion can only ever be dispatched proposal-mode and the judge can only ever refuse
        a proposal; after it, the path is reachable and the work can actually be done.
        """
        from pravrudhi.application.build_config import config_path
        from pravrudhi.application.heartbeat import build_paths_for

        config_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        config_path(tmp_path).write_text("version: 1\nbuild:\n  allowed_prefixes:\n    - frontend/\n")

        text = "`app/frontend/src/lib/candidates.ts` reaches one of exactly two end states"
        rid = _seeded(tmp_path, text)
        assert build_paths_for(text, root=tmp_path) == (), "the Studio-layout path is unreachable in this root"

        requests.amend_criterion(
            tmp_path, rid, 0, old="app/frontend/", new="frontend/",
            why="path translated from Studio layout; request cites pravrudhi-app@7e30ef7",
        )
        after = requests.get(tmp_path, rid)
        assert after is not None
        assert build_paths_for(after.criteria[0].text, root=tmp_path) != (), \
            "after the translation the dispatcher must be able to reach the named path"
