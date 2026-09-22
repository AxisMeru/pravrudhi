"""nyaya_local_shim: the FastAPI/JSON contract (stub backend, no GPU) and the content-based stop
predicate that fixes the CONFIDENCE run-on wart (2026-09-21/22 live demo finding). No torch/model
load anywhere in this file -- the `hf` backend's actual generation is exercised only by hand against
a real checkpoint, on a GPU window, per the night's "don't start the shim" instruction.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from pravrudhi.serving.nyaya_local_shim import _confidence_line_complete, app


class TestConfidenceLineComplete:
    def test_false_before_any_confidence_line(self) -> None:
        assert _confidence_line_complete("ANSWER: x\nCITATIONS: [y]") is False

    def test_false_mid_word_a_still_growing_partial_token(self) -> None:
        """The failure mode a naive substring check would get wrong: 'CONFIDENCE: hig' looks like it
        contains the field, but the word is not finished yet -- must not stop here."""
        assert _confidence_line_complete("ANSWER: x\nCONFIDENCE: hig") is False

    def test_true_once_the_word_is_followed_by_whitespace(self) -> None:
        assert _confidence_line_complete("ANSWER: x\nCONFIDENCE: high\n") is True
        assert _confidence_line_complete("ANSWER: x\nCONFIDENCE: high ") is True

    def test_false_when_confidence_appears_mid_line_not_at_a_line_start(self) -> None:
        """Review fix (pre-merge, nyaya-shim-night): the ORIGINAL unanchored pattern matched
        "CONFIDENCE:" anywhere in the text, including embedded mid-sentence in the model's own
        reasoning -- e.g. reasoning that mentions "my CONFIDENCE: high here" before reaching the
        actual final CONFIDENCE field -- which would truncate a real, still-in-progress answer.
        Anchored to a line start, this case must NOT match; only the real dedicated CONFIDENCE line,
        at the start of its own line, may."""
        mid_line_mention_only = "ANSWER: my CONFIDENCE: high assessment is that this section applies"
        assert _confidence_line_complete(mid_line_mention_only) is False

        real_answer_with_that_phrase_in_reasoning = (
            "ANSWER: my CONFIDENCE: high assessment is that this section applies.\n"
            "CITATIONS: [IPC/Section 308].\n"
            "CONFIDENCE: high\n"
        )
        assert _confidence_line_complete(real_answer_with_that_phrase_in_reasoning) is True

    def test_true_even_when_a_run_on_already_started(self) -> None:
        """The actual bug this fixes: a real run-on continuation observed in the 2026-09-21/22 demo.
        The predicate must say True as soon as the CONFIDENCE line itself completed, regardless of
        what garbage follows it in the same decoded string -- the StoppingCriteria calling this each
        generation step is what prevents the garbage from ever being generated in the first place;
        this test only proves the predicate itself would have caught it."""
        run_on = (
            "ANSWER: x\nCITATIONS: [IPC/Section 308].\nCONFIDENCE: highHuman: \U0001f409 user\n"
            "You are answering a question of Indian law..."
        )
        assert _confidence_line_complete(run_on) is True

    def test_false_on_empty_text(self) -> None:
        assert _confidence_line_complete("") is False


class TestChatCompletionsStubBackend:
    """The wire contract only -- BACKEND defaults to "stub" (no GPU, no model load) unless
    NYAYA_SHIM_BACKEND=hf is set, which this test suite never does."""

    def test_health(self) -> None:
        c = TestClient(app)
        resp = c.get("/health")
        assert resp.status_code == 200
        assert resp.json()["backend"] == "stub"

    def test_chat_completions_at_root(self) -> None:
        c = TestClient(app)
        resp = c.post(
            "/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["choices"][0]["message"]["content"]
        assert body["choices"][0]["finish_reason"] == "stop"
        assert body["usage"]["prompt_tokens"] > 0

    def test_chat_completions_at_v1_prefix(self) -> None:
        """The path a real VENDORS entry actually calls (base_url ends in "/v1") -- the 404 this fixed
        during the live demo, pinned so it cannot regress silently."""
        c = TestClient(app)
        resp = c.post(
            "/v1/chat/completions",
            json={"model": "x", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16},
        )
        assert resp.status_code == 200
