"""spardha (rivalry) and voice(): a competitive drive that never mixes a reported rival score into a measured
one, and a first-person account that never states a number no drive carries.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from pravrudhi.application.chat import dispatch
from pravrudhi.application.kshudha import (
    ACTION_DESCRIPTIONS,
    Appetite,
    AppetiteConfig,
    Drive,
    RivalFigure,
    spardha_drive,
    voice,
)
from pravrudhi.application.memory_store import FileMemoryStore

CFG = AppetiteConfig(weights={"spardha": 1.0}, targets={"spardha": 1.0})

_DIGIT_RE = re.compile(r"\d")


def _rival(system: str, benchmark: str, score: float) -> RivalFigure:
    return RivalFigure(
        system=system, benchmark=benchmark, score=score,
        source_url=f"https://example.test/{system}", as_of="2026-09-01", note="",
    )


class TestSpardhaUnknown:
    def test_no_rivals_declared_is_unknown(self) -> None:
        d = spardha_drive({"gsm8k exact_match,strict-match": 0.6}, (), CFG)
        assert d.unknown is True
        assert d.deficit is None
        assert d.value is None
        assert "no rival" in d.blocked_reason

    def test_nothing_measured_yet_is_unknown(self) -> None:
        d = spardha_drive({}, (_rival("Acme", "gsm8k exact_match,strict-match", 0.9),), CFG)
        assert d.unknown is True
        assert "no external result" in d.blocked_reason

    def test_a_rival_naming_a_different_benchmark_is_unknown(self) -> None:
        measured = {"gsm8k exact_match,strict-match": 0.6}
        rivals = (_rival("Acme", "humaneval+ pass@1", 0.9),)
        d = spardha_drive(measured, rivals, CFG)
        assert d.unknown is True
        assert d.deficit is None

    def test_a_declared_rival_we_already_beat_is_unknown(self) -> None:
        measured = {"gsm8k exact_match,strict-match": 0.9}
        rivals = (_rival("Acme", "gsm8k exact_match,strict-match", 0.6),)
        d = spardha_drive(measured, rivals, CFG)
        assert d.unknown is True
        assert "no declared rival names a benchmark we measure and also beats" in d.blocked_reason


class TestSpardhaShortfall:
    def test_a_rival_that_beats_us_produces_the_right_shortfall(self) -> None:
        measured = {"gsm8k exact_match,strict-match": 0.6}
        rivals = (_rival("Acme", "gsm8k exact_match,strict-match", 0.8),)
        d = spardha_drive(measured, rivals, CFG)
        # gap = clip((0.8 - 0.6) / 0.8) = 0.25
        assert d.unknown is False
        assert round(d.deficit or 0.0, 4) == 0.25
        assert round(d.value or 0.0, 4) == 0.75
        assert d.eligible is True

    def test_the_nearest_beating_rival_is_used_not_the_furthest(self) -> None:
        measured = {"gsm8k exact_match,strict-match": 0.6}
        rivals = (
            _rival("FarAhead", "gsm8k exact_match,strict-match", 0.99),
            _rival("Nearest", "gsm8k exact_match,strict-match", 0.7),
        )
        d = spardha_drive(measured, rivals, CFG)
        # nearest beating rival is 0.7: gap = clip((0.7 - 0.6) / 0.7) = 0.142857...
        assert round(d.deficit or 0.0, 4) == round((0.7 - 0.6) / 0.7, 4)
        assert any("Nearest" in s for s in d.sources)
        assert not any("FarAhead" in s for s in d.sources)

    def test_averages_across_every_measured_benchmark_a_rival_beats(self) -> None:
        measured = {
            "gsm8k exact_match,strict-match": 0.6,
            "humaneval+ pass@1": 0.5,
        }
        rivals = (
            _rival("Acme", "gsm8k exact_match,strict-match", 0.8),  # gap 0.25
            _rival("Acme", "humaneval+ pass@1", 0.75),  # gap = (0.75-0.5)/0.75 = 1/3
        )
        d = spardha_drive(measured, rivals, CFG)
        expected = (0.25 + (0.75 - 0.5) / 0.75) / 2
        assert round(d.deficit or 0.0, 4) == round(expected, 4)


class TestRivalFiguresAreReportedNotMeasured:
    def test_a_rival_score_is_labelled_reported_and_never_merged_into_our_value(self) -> None:
        measured = {"gsm8k exact_match,strict-match": 0.6}
        rival = _rival("Acme", "gsm8k exact_match,strict-match", 0.8)
        d = spardha_drive(measured, (rival,), CFG)
        # our own measured score survives as `value` (1 - deficit); the rival's score never overwrites it.
        assert d.value != rival.score
        assert round(d.value or 0.0, 4) == 0.75
        source = next(s for s in d.sources if "Acme" in s)
        assert "agama" in source and "reported" in source
        assert rival.source_url in source
        assert str(rival.score) in source or f"{rival.score:g}" in source


class TestVoice:
    def _appetite(self, *, selected: str | None, unknown_reason: str = "no evidence-freshness source is wired "
                  "into the engine yet") -> Appetite:
        known = Drive(
            id="samarthya", wire_name="capability", value=0.1, target=0.8, deficit=0.9, weight=1.0,
            eligible=True, blocked_reason="", sources=(), unknown=False,
        )
        unk = Drive(
            id="pramana_navyata", wire_name="freshness", value=None, target=1.0, deficit=None, weight=1.0,
            eligible=False, blocked_reason=unknown_reason, sources=(), unknown=True,
        )
        action = {"drive": "samarthya", "kind": "action", "description": ACTION_DESCRIPTIONS["samarthya"]}
        return Appetite(
            as_of="2026-09-07T00:00:00Z", policy_version="1", drives=(known, unk),
            largest_unmet="samarthya", selected=selected, action=action if selected else None,
            next_wake="next heartbeat", resting_reason=None if selected else "no eligible drive has crossed "
            "the hungry threshold; samarthya is the largest unmet",
        )

    def test_names_an_unknown_drive_and_its_reason(self) -> None:
        text = voice(self._appetite(selected="samarthya"))
        assert "freshness" in text
        assert "cannot measure" in text
        assert "no evidence-freshness source is wired into the engine yet" in text

    def test_invents_no_number(self) -> None:
        text = voice(self._appetite(selected="samarthya"))
        assert not _DIGIT_RE.search(text)
        text_resting = voice(self._appetite(selected=None))
        assert not _DIGIT_RE.search(text_resting)

    def test_is_two_or_three_sentences(self) -> None:
        text = voice(self._appetite(selected="samarthya"))
        sentence_count = text.count(". ") + (1 if text.endswith(".") else 0)
        assert 2 <= sentence_count <= 3


class TestChatToolMatchesApi:
    def test_chat_tool_returns_the_same_figures_the_api_does(self, tmp_path: Path) -> None:
        from pravrudhi.api.server import create_app

        app = create_app(tmp_path)
        client = TestClient(app, base_url="http://localhost")
        api_response = client.get("/api/appetite")
        assert api_response.status_code == 200
        api_body = api_response.json()

        store = FileMemoryStore(tmp_path)
        invocation = dispatch(tmp_path, store, "appetite", {})

        assert invocation.result["drives"] == api_body["drives"]
        assert invocation.result["sentence"] == api_body["sentence"]
        assert invocation.result["appetite"]["selected"] == api_body["appetite"]["selected"]
        assert "voice" in invocation.result
