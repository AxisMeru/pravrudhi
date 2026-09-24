"""Track A P3 (`research/prereg/B1-tier1.md` / `B1-tier1-amendment-1.md`): the Python consumer for
prabhasa-nyaya's `A3E` wire tag (`lean/Score.lean`'s `scoreA3ELine`, merged main 78bf844). Nothing calls
that tag yet on the pravrudhi side before this module -- this is what wires it up.

Done-when: an answer that states every required element is `"grounded"`; an answer that omits a required
element comes back `"flagged"` naming that element in `omitted_elements`; empty `required_elements` is
refused rather than silently scored against nothing.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pravrudhi.application import nyaya_lean_elements as nle

# No host path is committed here.  Set PRABHASA_NYAYA_SCORE_BIN to the built score binary; tests skip
# with a clear reason when the variable is unset.
_LEAN_SCORE_BIN = Path(os.environ.get("PRABHASA_NYAYA_SCORE_BIN", "prabhasa-nyaya-score-not-configured"))
requires_lean_scorer = pytest.mark.skipif(
    not _LEAN_SCORE_BIN.exists(),
    reason="PRABHASA_NYAYA_SCORE_BIN is not set or does not point to a built score binary (set it to run these tests)",
)

# Section 107's real required-element names (research/nyaya-elements/packets/section-107's
# verified_element_list.json), kept literal here rather than reading the fixture file -- this module's
# input contract is "a list of element name strings", not "a path inside prabhasa-nyaya's worktree layout"
# (ADR-0001: pravrudhi has no path/import dependency on that repo's internal directory shape).
_SECTION_107_ELEMENTS = [
    "Instigation (First clause)",
    "Engaging in a conspiracy (Secondly clause)",
]


class TestLoadRequiredElements:
    def test_reads_element_names_off_a_verified_element_list_json(self, tmp_path: Path) -> None:
        p = tmp_path / "verified_element_list.json"
        p.write_text(
            '{"section_id": "Section 107", "elements": '
            '[{"name": "Instigation (First clause)", "text": "...", "grounded": true}], '
            '"exceptions": [], "cross_referenced_definitions": []}'
        )
        assert nle.load_required_elements(p) == ["Instigation (First clause)"]

    def test_refuses_an_element_list_with_no_elements(self, tmp_path: Path) -> None:
        p = tmp_path / "verified_element_list.json"
        p.write_text('{"section_id": "Section 1", "elements": [], "exceptions": [], "cross_referenced_definitions": []}')
        with pytest.raises(nle.NoRequiredElementsError):
            nle.load_required_elements(p)


@requires_lean_scorer
class TestCheckElementsOnTheA3EWireTag:
    def test_an_answer_stating_every_required_element_is_grounded(self) -> None:
        answer = (
            "The accused instigated the co-accused to commit the offence, satisfying Instigation "
            "(First clause). He also engaged in a conspiracy for the doing of that thing, satisfying "
            "Engaging in a conspiracy (Secondly clause)."
        )
        result = nle.check_elements(answer, _SECTION_107_ELEMENTS, score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "grounded"
        assert result["unlicensed_elements"] == []
        assert result["omitted_elements"] == []

    def test_an_answer_omitting_a_required_element_is_flagged_naming_it(self) -> None:
        answer = "The accused's act satisfies Instigation (First clause), nothing more is claimed."
        result = nle.check_elements(answer, _SECTION_107_ELEMENTS, score_bin=_LEAN_SCORE_BIN)
        assert result["verdict"] == "flagged"
        assert result["omitted_elements"] == ["Engaging in a conspiracy (Secondly clause)"]
        assert result["unlicensed_elements"] == []

    def test_refuses_to_score_against_an_empty_required_element_list(self) -> None:
        with pytest.raises(nle.NoRequiredElementsError):
            nle.check_elements("anything", [], score_bin=_LEAN_SCORE_BIN)
