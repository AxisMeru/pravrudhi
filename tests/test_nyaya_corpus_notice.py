"""#289: /api/nyaya/corpus returns statute text only with the notice and a recorded source link (licence hold #506)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api.server import create_app
from pravrudhi.application import nyaya
from pravrudhi.application.init import init_project

NOTICE = "Unofficial text; the official version on India Code prevails."
SRC = [
    {"act": "Bharatiya Nyaya Sanhita, 2023", "act_page": "https://indiacode.gov.in/handle/123456789/545524"},
    {"work": "Constitution of India (2020)", "pages": [{"url": "https://en.wikisource.org/x"}]},
    {"work": "Some Act (1999)", "pages": [{"title": "x"}, {"url": "https://www.indiacode.nic.in/show-data?actid=1"}]},
]


def test_the_notice_is_the_apps_exact_wording() -> None:
    assert nyaya.STATUTE_NOTICE == NOTICE


@pytest.mark.parametrize(
    ("act", "want"),
    [
        ("Bharatiya Nyaya Sanhita, 2023", "https://indiacode.gov.in/handle/123456789/545524"),
        ("bharatiya nyaya sanhita, 2023", "https://indiacode.gov.in/handle/123456789/545524"),
        ("Some Act", "https://www.indiacode.nic.in/show-data?actid=1"),  # matches the `work` form, first India Code page
        ("Constitution of India", None),  # recorded page is not India Code: not the official text, so no link
        ("Indian Penal Code, 1860", None),  # no record at all: null, never a guessed deep link
        ("Bharatiya Nyaya", None),  # a prefix of the act name is not a match
        ("", None),
    ],
)
def test_recorded_source_url(act: str, want: str | None) -> None:
    assert nyaya.recorded_source_url(act, SRC) == want


@pytest.mark.parametrize("url", ["http://indiacode.gov.in/x", "https://indiacode.gov.in.evil.example/x",
                                 "https://evil.example/indiacode.gov.in", "javascript:alert(1)", None, 5])
def test_only_an_https_india_code_host_is_ever_returned(url: object) -> None:
    assert nyaya.recorded_source_url("A", [{"act": "A", "act_page": url}]) is None


def test_every_hit_carries_the_notice_and_a_source_url_key_and_no_text_comes_without_the_notice(tmp_path: Path) -> None:
    init_project(tmp_path)
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    body = c.get("/api/nyaya/corpus?q=murder punishment").json()
    assert body["notice"] == NOTICE and body["hits"]
    for h in body["hits"]:
        assert h["text"] and h["notice"] == NOTICE and "source_url" in h
        assert h["source_url"] is None or h["source_url"].startswith("https://")
    by_act = {h["act"]: h["source_url"] for h in body["hits"]}
    assert any(a.startswith("Bharatiya Nyaya Sanhita") and u and "indiacode.gov.in/handle/" in u for a, u in by_act.items())
    assert all(u is None for a, u in by_act.items() if a.startswith("Indian Penal Code"))


def test_an_empty_query_returns_no_text_and_still_the_notice(tmp_path: Path) -> None:
    init_project(tmp_path)
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    body = c.get("/api/nyaya/corpus?q=").json()
    assert body["hits"] == [] and body["notice"] == NOTICE
