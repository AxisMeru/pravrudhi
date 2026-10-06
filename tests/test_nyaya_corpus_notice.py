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


@pytest.mark.parametrize("url", [
    "http://indiacode.gov.in/x", "https://indiacode.gov.in.evil.example/x", "https://evil.example/indiacode.gov.in",
    "javascript:alert(1)", None, 5,
    "https://evil.example\\@indiacode.gov.in/x",  # a backslash: a browser goes to evil.example
    "https://evil.example\\.indiacode.gov.in/x", "https://indiacode.gov.in\\x", "https://indiacode.gov.in/a\\b",
    "https://user@indiacode.gov.in/x", "https://user:pw@indiacode.gov.in/x", "https://evil.example@indiacode.gov.in/x",
    "https://indiacode.gov.in@evil.example/x", "https://:@indiacode.gov.in/x",
    "https://indiacode.gov.in:8443/x", "https://indiacode.gov.in:443/x", "https://indiacode.gov.in:/x",
    "https://indiacode.gov.in /x", "https://indiacode.gov.in/x\n", "https://indiacode.gov.in/\tx",
    "https://indiacode.gov.in/x)](https://evil.com)", "https://indiacode.gov.in/x(y)", "https://indiacode.gov.in/[x]",
    "https://indiacode.gov.in/<x>", "https://indiacode.gov.in/?u=https://evil.example/", "https://evil.com\\@indiacode.gov.in/x",
    "https://indiacode\u00e9.gov.in/x", "https://\u0131ndiacode.gov.in/x", "//indiacode.gov.in/x", "https:indiacode.gov.in/x",
])
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


@pytest.mark.parametrize(
    ("url", "want"),
    [
        ("https://INDIACODE.gov.in/handle/1?x=1#f", "https://indiacode.gov.in/handle/1?x=1#f"),  # host lowercased, rest kept
        ("https://www.indiacode.nic.in/show-data?actid=1", "https://www.indiacode.nic.in/show-data?actid=1"),
        ("https://indiacode.gov.in", "https://indiacode.gov.in"),
        ("https://indiacode.gov.in/a b".replace(" ", "%20"), "https://indiacode.gov.in/a%20b"),  # an existing escape is kept
        ("https://indiacode.gov.in/a\"b", "https://indiacode.gov.in/a%22b"),  # an unsafe character is escaped
    ],
)
def test_a_returned_url_is_the_normalised_rebuilt_url(url: str, want: str) -> None:
    assert nyaya.recorded_source_url("A", [{"act": "A", "act_page": url}]) == want


def test_every_hit_has_a_fallback_link_even_without_a_recorded_page(tmp_path: Path) -> None:
    init_project(tmp_path)
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    hits = c.get("/api/nyaya/corpus?q=murder punishment").json()["hits"]
    assert hits and all(h["source_fallback_url"] == nyaya.INDIA_CODE_HOME for h in hits)
    assert nyaya.INDIA_CODE_HOME == "https://www.indiacode.nic.in/"
    ipc = [h for h in hits if h["act"].startswith("Indian Penal Code")]
    assert ipc and all(h["source_url"] is None and h["source_fallback_url"] for h in ipc)


# --- #506 ruling (6 Oct): full statute text is for AUTHENTICATED callers; anonymous callers get an excerpt -------------------


def _hosted_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, user: object | None = None) -> TestClient:
    from pravrudhi.api import identity
    from pravrudhi.api import nyaya as nyaya_api

    init_project(tmp_path)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")  # a hosted-style deployment: anonymous callers exist
    # Today an anonymous caller is refused (400 "name a workspace") by `_session` before the corpus is read, so the
    # gate below is defence in depth. Resolve every caller to the engine root here so the gate itself is what is tested.
    real_root_for = nyaya_api.root_for
    # a signed-in user resolves through the real workspace logic; only the anonymous caller is let through to the gate
    monkeypatch.setattr(
        nyaya_api, "root_for", lambda u, ws, **k: tmp_path if u is None else real_root_for(u, ws, **k)
    )
    app = create_app(tmp_path)
    if user is not None:
        app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app, base_url="http://127.0.0.1:8008")


def test_an_anonymous_caller_gets_no_statute_text_at_all_only_id_title_and_the_source_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    anon_resp = _hosted_client(tmp_path / "a", monkeypatch).get("/api/nyaya/corpus?q=murder punishment")
    anon = anon_resp.json()
    assert anon["notice"] == NOTICE and anon["hits"]
    for h in anon["hits"]:
        assert "text" not in h and "excerpt" not in h
        assert h["id"] and h["title"] and h["section"] and "source_url" in h and h["source_fallback_url"]
        assert h["notice"] == NOTICE
    from tests.test_partner_key_metering import ADMIN

    auth = _hosted_client(tmp_path / "b", monkeypatch, user=ADMIN).get("/api/nyaya/corpus?q=murder punishment&workspace=w1").json()
    # no provision's text (not even its first 40 characters) appears anywhere in the anonymous body
    assert auth["hits"] and all(h["text"][:40] not in anon_resp.text for h in auth["hits"])


def test_an_authenticated_caller_gets_the_full_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_partner_key_metering import ADMIN

    c = _hosted_client(tmp_path, monkeypatch, user=ADMIN)
    auth = c.get("/api/nyaya/corpus?q=murder punishment&workspace=w1").json()["hits"]
    assert auth and all(h["text"] and h["notice"] == NOTICE for h in auth)


def test_an_api_key_caller_is_authenticated_and_a_bad_key_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.api import nyaya as nyaya_api
    from pravrudhi.application import tenancy

    init_project(tmp_path)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
    monkeypatch.setattr(nyaya_api, "root_for", lambda *_a, **_k: tmp_path)  # let a key-only caller reach the gate
    tenancy.create_org(tmp_path, "acme", "Acme")
    secret = tenancy.create_key(tmp_path, "acme", label="t", rate_limit_per_minute=60).secret
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    keyed = c.get("/api/nyaya/corpus?q=murder punishment", headers={"X-Pravrudhi-Api-Key": secret}).json()["hits"]
    assert keyed and all(h["text"] for h in keyed)
    assert all("text" not in h for h in c.get("/api/nyaya/corpus?q=murder punishment").json()["hits"])
    assert c.get("/api/nyaya/corpus?q=murder", headers={"X-Pravrudhi-Api-Key": "bad"}).status_code == 401


def test_the_local_single_operator_engine_keeps_the_full_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAVRUDHI_AUTH", raising=False)  # auth disabled: the operator's own engine, not a public caller
    init_project(tmp_path)
    c = TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")
    assert all(h["text"] for h in c.get("/api/nyaya/corpus?q=murder punishment").json()["hits"])


def test_openapi_documents_the_text_as_optional() -> None:
    schema = create_app(Path(".")).openapi()["components"]["schemas"]["NyayaCorpusHit"]
    assert "text" in schema["properties"] and "text" not in schema.get("required", []) and "excerpt" not in schema["properties"]
