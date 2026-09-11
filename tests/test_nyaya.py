"""prabhasa-nyaya in the product: retrieval is deterministic, citations are checked mechanically, abstention
is an outcome, and nothing reaches the ledger."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from pravrudhi.api.localguard import TOKEN_HEADER, app_token
from pravrudhi.api.server import create_app
from pravrudhi.application import nyaya, panel
from pravrudhi.application.init import init_project


def test_the_shipped_corpus_is_real_and_traceable() -> None:
    c = nyaya.load_corpus()
    assert len(c.documents) >= 550  # 100 IPC sections (IL-TUR) + 450 Constitution articles (Wikisource, 2020 text)
    ipc = next(s for s in c.sources if s.get("dataset") == "Exploration-Lab/IL-TUR")
    assert ipc["sha256"]
    coi = next(s for s in c.sources if s.get("work") == "Constitution of India (2020)")
    assert coi["site"] == "en.wikisource.org" and all(p["revid"] and p["sha256"] for p in coi["pages"])
    assert c.by_id["IPC/Section 302"].title.lower().startswith("punishment for murder")
    assert c.by_id["COI/Article 14"].text.startswith("The State shall not deny to any person equality before the law")
    assert c.by_id["COI/Article 21"].text.startswith("No person shall be deprived of his life or personal liberty")
    assert "COI/Article 232" not in c.by_id  # repealed; only a chapter banner followed its number


def test_retrieval_is_deterministic_and_a_named_section_comes_first() -> None:
    c = nyaya.load_corpus()
    q = "What is the punishment for murder under section 302?"
    a, b = c.retrieve(q, k=5), c.retrieve(q, k=5)
    assert a == b
    assert a[0][0].id == "IPC/Section 302"
    assert c.retrieve("", k=5) == []


def test_a_lay_question_reaches_the_homicide_sections_through_the_lexicon() -> None:
    """The first live ask retrieved hurt and robbery sections for a victim who died, and both CLIs rightly
    abstained. The gap was vocabulary, so the fix is config: lexicon.json maps lay words to the Code's."""
    c = nyaya.load_corpus()
    q = "A man strikes another on the head with a heavy stick intending grievous hurt; the victim dies two days later."
    ids = [d.id for d, _ in c.retrieve(q, k=8)]
    assert {"IPC/Section 304", "IPC/Section 300", "IPC/Section 299", "IPC/Section 302"} & set(ids)
    assert "IPC/Section 325" in ids or "IPC/Section 320" in ids
    assert nyaya.expand("nothing legal here", c.expansions) == "nothing legal here"
    assert [d.id for d, _ in c.retrieve("equality before law", k=1)] == ["COI/Article 14"]
    assert [d.id for d, _ in c.retrieve("right to life and personal liberty", k=1)] == ["COI/Article 21"]


def test_citations_are_checked_against_the_corpus_not_believed() -> None:
    c = nyaya.load_corpus()
    shown = [c.by_id["IPC/Section 302"], c.by_id["IPC/Section 300"]]
    text = (
        "ANSWER: Murder is punished with death or life imprisonment [IPC/Section 302]. See also [IPC/Section 34] "
        "and [IPC/Section 999].\nCITATIONS: IPC/Section 302\nCONFIDENCE: high"
    )
    cites, verdict, conf = nyaya.check_answer(text, shown, c)
    assert {x.id: x.status for x in cites} == {
        "IPC/Section 302": "licensed",
        "IPC/Section 34": "unshown",
        "IPC/Section 999": "invented",
    }
    assert verdict == "invented_citation" and conf == "high"
    ok, verdict, _ = nyaya.check_answer("ANSWER: [IPC/Section 302] applies.\nCONFIDENCE: medium", shown, c)
    assert verdict == "licensed"
    _, verdict, _ = nyaya.check_answer(
        "I do not know: the provided sources do not cover this. A family-law statute would be needed.", shown, c
    )
    assert verdict == "abstained"
    _, verdict, _ = nyaya.check_answer("The accused is guilty because everyone knows it.", shown, c)
    assert verdict == "unlicensed"
    # The first Constitution ask came back `unlicensed` with two correct citations, because the citation
    # pattern knew only "Section". An Article is a citation too.
    cites, verdict, _ = nyaya.check_answer("No [COI/Article 15]; see also [COI/Article 999].", [c.by_id["COI/Article 15"]], c)
    assert [(x.id, x.status) for x in cites] == [("COI/Article 15", "licensed"), ("COI/Article 999", "invented")]
    assert verdict == "invented_citation"
    _, verdict, _ = nyaya.check_answer("No [COI/Article 15].", [c.by_id["COI/Article 15"]], c)
    assert verdict == "licensed"


def _fake(text_by_vendor: dict[str, str]) -> panel.AskFn:
    def fn(v: panel.Vendor, prompt: str) -> panel.Answer:
        if v.id == "codex-cli":
            raise RuntimeError("usage limit reached")
        return panel.Answer(v.id, v.interface, v.model, "", text_by_vendor.get(v.id, text_by_vendor["*"]), 0.5, None, None)

    return fn


def test_ask_runs_every_vendor_records_failures_and_writes_no_ledger_row(tmp_path: Path) -> None:
    init_project(tmp_path)
    before = (tmp_path / "research" / "ledger.jsonl").read_text()
    rec = nyaya.ask(
        tmp_path,
        "What is the punishment for murder under section 302 IPC?",
        ("claude-cli", "codex-cli"),
        ask_fn=_fake({"*": "ANSWER: Death or imprisonment for life, and fine [IPC/Section 302].\nCONFIDENCE: high"}),
    )
    by = {a.vendor: a for a in rec.answers}
    assert by["claude-cli"].verdict == "licensed" and by["claude-cli"].citations[0].status == "licensed"
    assert by["codex-cli"].verdict == "error" and "usage limit" in (by["codex-cli"].error or "")
    assert rec.provenance == "agama" and rec.sources[0]["id"] == "IPC/Section 302"
    assert (tmp_path / "research" / "nyaya" / "asks" / f"{rec.id}.json").exists()
    assert (tmp_path / "research" / "ledger.jsonl").read_text() == before
    assert nyaya.recent_asks(tmp_path)[0]["id"] == rec.id


def test_the_checker_audits_each_answer_in_the_a1_1_shape(tmp_path: Path) -> None:
    init_project(tmp_path)

    def fn(v: panel.Vendor, prompt: str) -> panel.Answer:
        if "VERDICT: ERROR or CORRECT" in prompt:
            text = "VERDICT: ERROR\nSPAN: and fine\nCLASS: wrong_authority\nWHY: the section imposes a fine only in addition."
        else:
            text = "ANSWER: Death or life [IPC/Section 302] and fine.\nCITATIONS: IPC/Section 302\nCONFIDENCE: high"
        return panel.Answer(v.id, v.interface, v.model, "", text, 0.1, None, None)

    rec = nyaya.ask(tmp_path, "punishment for murder section 302", ("claude-cli",), checker="qwen-dashscope", ask_fn=fn)
    audit = rec.answers[0].audit
    assert audit and audit["verdict"] == "ERROR" and audit["class"] == "wrong_authority" and audit["span"] == "and fine"


def test_the_routes_serve_the_product_and_need_the_local_token_to_ask(tmp_path: Path) -> None:
    init_project(tmp_path)
    fake = _fake({"*": "ANSWER: [IPC/Section 302].\nCITATIONS: IPC/Section 302\nCONFIDENCE: low"})
    c = TestClient(create_app(tmp_path, nyaya_ask_fn=fake), base_url="http://127.0.0.1:8008")
    assert c.get("/api/nyaya/corpus?q=murder").json()["hits"][0]["id"].startswith("IPC/")
    vendors = c.get("/api/nyaya/vendors").json()["vendors"]
    assert {v["id"] for v in vendors} == set(nyaya.DEFAULT_VENDORS) and all("available" in v for v in vendors)
    assert c.post("/api/nyaya/ask", json={"question": "murder"}).status_code in (401, 403)  # no local token
    r = c.post(
        "/api/nyaya/ask", json={"question": "murder", "vendors": ["claude-cli"]}, headers={TOKEN_HEADER: app_token(tmp_path)}
    )
    assert r.status_code == 200 and r.json()["answers"][0]["verdict"] == "licensed"
    r = c.post("/api/nyaya/ask", json={"question": "murder", "vendors": ["nope"]}, headers={TOKEN_HEADER: app_token(tmp_path)})
    assert r.status_code == 422
    assert c.get("/api/nyaya/asks").json()["asks"][0]["question"] == "murder"
