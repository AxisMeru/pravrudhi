"""Executable spec for `application.openrouter_byok` (#304). Fake keys, fake transport: no network, no spend.

The leak check is itself proven on a known-positive first: `leaks()` must flag a deliberately leaky artifact,
otherwise a passing "no leak" assertion below would mean nothing."""

from __future__ import annotations

import base64
import json
import os
import urllib.parse
from pathlib import Path

import pytest

from pravrudhi.application import credentials as creds
from pravrudhi.application import openrouter_byok as ob
from pravrudhi.application.credentials import FileCredentialStore, Secret, redact
from pravrudhi.application.nyaya_judges import JudgeRequest

KEY_A = "sk-or-v1-" + "a1b2c3d4" * 8
KEY_B = "sk-or-v1-" + "9f8e7d6c" * 8
OPERATOR_KEY = "sk-or-v1-" + "0p3r4t0r" * 8
SLUG = "anthropic/claude-sonnet-5"
REPLY = '{"status":"established","fact_id":"F1","quote":"he signed"}'


def leaks(key: str, *artifacts: object) -> bool:
    """True if `key`, or an encoding or a tail fragment of it, appears in any artifact's text form."""
    needles = {key, key[-16:], urllib.parse.quote(key, safe=""), base64.b64encode(key.encode()).decode()}
    return any(n in str(a) or n in repr(a) for a in artifacts for n in needles)


def store_with(tmp_path: Path, key: str | None) -> FileCredentialStore:
    s = FileCredentialStore(tmp_path)
    if key:
        s.put("openrouter", key)
    return s


class FakeTransport:
    def __init__(self, status: int = 200, body: dict | None = None, raises: Exception | None = None) -> None:
        self.status, self.raises, self.calls = status, raises, []
        self.body = body if body is not None else {
            "model": SLUG, "choices": [{"message": {"content": REPLY}}], "usage": {"completion_tokens": 9},
        }

    def __call__(self, url, headers, body):
        self.calls.append((url, dict(headers), dict(body)))
        if self.raises:
            raise self.raises
        return self.status, self.body


@pytest.fixture(autouse=True)
def _operator_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", OPERATOR_KEY)


def test_leak_checker_flags_a_known_positive():
    assert leaks(KEY_A, f"error talking to openrouter with {KEY_A}")
    assert leaks(KEY_A, "tail " + KEY_A[-16:])
    assert leaks(KEY_A, urllib.parse.quote(KEY_A, safe=""))
    assert not leaks(KEY_A, "nothing to see", Secret("openrouter", KEY_A))


def test_openrouter_is_a_registered_provider_and_redact_catches_its_keys():
    p = creds.PROVIDERS["openrouter"]
    assert (p.base_url, p.key_prefix, p.openai_compatible) == (ob.BASE_URL, "sk-or-", True)
    assert redact(f"boom {KEY_A} boom") == "boom [REDACTED] boom"


def test_key_comes_from_the_customer_store_never_the_environment(tmp_path):
    t = FakeTransport()
    ob.ask(SLUG, "p", store=store_with(tmp_path, KEY_A), transport=t)
    assert t.calls[0][1]["Authorization"] == f"Bearer {KEY_A}"
    assert OPERATOR_KEY not in json.dumps(t.calls)


def test_missing_customer_key_refuses_and_never_falls_back_to_operator_env(tmp_path):
    t = FakeTransport()
    with pytest.raises(ob.ByokKeyMissing):
        ob.ask(SLUG, "p", store=store_with(tmp_path, None), transport=t)
    assert t.calls == []


def test_request_pins_model_and_forbids_fallback_and_data_collection(tmp_path):
    t = FakeTransport()
    ob.ask(SLUG, "hello", store=store_with(tmp_path, KEY_A), transport=t)
    url, _, body = t.calls[0]
    assert url == ob.BASE_URL + "/chat/completions"
    assert body["model"] == SLUG and body["temperature"] == 0
    assert body["provider"] == {"allow_fallbacks": False, "data_collection": "deny"}
    assert body["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.parametrize("bad", ["", "gpt-4o", "../x/y", "a/b c", "openrouter/auto\n", "a/" + "b" * 300 + "!"])
def test_malformed_model_slug_is_refused_before_any_call(tmp_path, bad):
    t = FakeTransport()
    with pytest.raises(ob.OpenRouterByokError):
        ob.ask(bad, "p", store=store_with(tmp_path, KEY_A), transport=t)
    assert t.calls == []


def test_response_model_must_equal_the_pinned_slug(tmp_path):
    t = FakeTransport(body={"model": "meta-llama/llama-3-8b", "choices": [{"message": {"content": REPLY}}]})
    with pytest.raises(ob.ModelMismatch):
        ob.ask(SLUG, "p", store=store_with(tmp_path, KEY_A), transport=t)


def test_answer_records_the_resolved_model_and_interface(tmp_path):
    a = ob.ask(SLUG, "p", store=store_with(tmp_path, KEY_A), transport=FakeTransport())
    assert (a.model, a.interface, a.text, a.tokens, a.error) == (SLUG, "openrouter_byok", REPLY, 9, None)
    assert not leaks(KEY_A, a)


@pytest.mark.parametrize("status,exc", [(401, ob.ByokKeyRejected), (403, ob.ByokKeyRejected), (402, ob.ByokCreditsExhausted)])
def test_upstream_status_maps_to_typed_error_without_body_or_key(tmp_path, status, exc):
    t = FakeTransport(status=status, body={"error": {"message": f"bad key {KEY_A}"}})
    with pytest.raises(exc) as e:
        ob.ask(SLUG, "p", store=store_with(tmp_path, KEY_A), transport=t)
    assert not leaks(KEY_A, e.value, e.value.args)


def test_transport_exception_carrying_the_key_is_redacted(tmp_path):
    t = FakeTransport(raises=ConnectionError(f"proxy echoed Authorization: Bearer {KEY_A}"))
    with pytest.raises(ob.OpenRouterByokError) as e:
        ob.ask(SLUG, "p", store=store_with(tmp_path, KEY_A), transport=t)
    assert not leaks(KEY_A, e.value, e.value.args, e.value.__cause__, e.value.__context__)


def test_ledger_records_carry_a_fingerprint_not_the_key_or_prompt(tmp_path):
    rows = []
    ob.ask(SLUG, "SECRET FACTS", store=store_with(tmp_path, KEY_A), transport=FakeTransport(), ledger=rows.append)
    assert len(rows) == 1 and rows[0]["model"] == SLUG and len(rows[0]["key_fingerprint"]) == 8
    assert not leaks(KEY_A, json.dumps(rows)) and "SECRET FACTS" not in json.dumps(rows)
    assert ob.key_fingerprint(Secret("openrouter", KEY_A)) != ob.key_fingerprint(Secret("openrouter", KEY_B))


def test_judge_is_off_unless_the_org_opted_in(tmp_path):
    with pytest.raises(ob.ByokNotOptedIn):
        ob.OpenRouterJudge(SLUG, store=store_with(tmp_path, KEY_A), transport=FakeTransport())


def test_judge_returns_the_parsed_frontier_verdict(tmp_path):
    j = ob.OpenRouterJudge(SLUG, store=store_with(tmp_path, KEY_A), transport=FakeTransport(), opted_in=True)
    r = JudgeRequest("c", "el", False, "statute", "", (("F1", "he signed the deed"),))
    v = j.judge(r)
    assert (v.status, v.fact_id, v.quote) == ("established", "F1", "he signed")


def test_two_customers_never_share_a_key(tmp_path):
    ta, tb = FakeTransport(), FakeTransport()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ja = ob.OpenRouterJudge(SLUG, store=store_with(tmp_path / "a", KEY_A), transport=ta, opted_in=True)
    jb = ob.OpenRouterJudge(SLUG, store=store_with(tmp_path / "b", KEY_B), transport=tb, opted_in=True)
    r = JudgeRequest("c", "el", False, "s", "", (("F1", "x"),))
    ja.judge(r), jb.judge(r)
    assert ta.calls[0][1]["Authorization"].endswith(KEY_A) and tb.calls[0][1]["Authorization"].endswith(KEY_B)
    assert not leaks(KEY_B, ta.calls) and not leaks(KEY_A, tb.calls)
