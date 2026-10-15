"""#300 slice B: the citation verifier as a tool on the runner. Constructed toy index only."""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.citation_tool import register_verify_citation
from pravrudhi.application.tool_runner import ToolRunner

_CITER = "In Narandas Karsondas v. S.A. Kamtam and Anr. (1977) 3 SCC 247 the Court clarified the rule."
_BODY = "time is not ordinarily of the essence of the contract for sale of immovable property."


def _db(tmp_path: Path) -> Path:
    p = tmp_path / "i.sqlite3"
    c = open_index(p)
    insert_case(c, CaseRecord("c1", "Later v Other", "Supreme Court", 2005, "sc_pdf", "/x", _CITER))
    insert_case(c, CaseRecord("r1", "Narandas Karsondas vs S A Kamtam and Anr", "Supreme Court", 1977, "sc_pdf", "/y", _BODY))
    c.commit()
    c.close()
    return p


def _runner(path: Path | None) -> ToolRunner:
    r = ToolRunner(sleep=lambda _s: None)
    register_verify_citation(r, path)
    return r


def test_verified_through_the_runner(tmp_path: Path) -> None:
    res = _runner(_db(tmp_path)).call("verify_citation", {"citation": "(1977) 3 SCC 247", "quote": "time is not ordinarily"})
    assert (res.ok, res.value) == (True, "VERIFIED")


def test_not_in_index_is_a_value_not_a_fake_verdict(tmp_path: Path) -> None:
    res = _runner(_db(tmp_path)).call("verify_citation", {"citation": "AIR 1950 SC 27", "quote": "x"})
    assert (res.ok, res.value) == (True, "NOT_IN_INDEX")


def test_no_index_is_a_typed_error_never_a_guess(tmp_path: Path) -> None:
    res = _runner(None).call("verify_citation", {"citation": "(1977) 3 SCC 247", "quote": "x"})
    assert (res.ok, res.error) == (False, "tool_error:CitationIndexUnavailable")
    res = _runner(tmp_path / "absent.sqlite3").call("verify_citation", {"citation": "(1977) 3 SCC 247", "quote": "x"})
    assert (res.ok, res.error) == (False, "tool_error:CitationIndexUnavailable")


def test_a_locked_index_is_retried_as_transient(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    from pravrudhi.application import citation_tool

    real, n = citation_tool.verify, {"i": 0}

    def flaky(conn, c, q):
        n["i"] += 1
        if n["i"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real(conn, c, q)

    monkeypatch.setattr(citation_tool, "verify", flaky)
    res = _runner(_db(tmp_path)).call("verify_citation", {"citation": "(1977) 3 SCC 247", "quote": "time is not ordinarily"})
    assert (res.ok, res.value, res.attempts) == (True, "VERIFIED", 2)


def test_a_lookup_that_runs_out_of_budget_is_a_typed_error_never_a_verdict(tmp_path: Path, monkeypatch) -> None:
    """#379 follow-up: the tool has the API route's per-lookup deadline; a timeout is an error (not retried), never VERIFIED."""
    from pravrudhi.application import citation_tool

    calls = {"n": 0}

    def slow(conn, c, q):  # a statement that would run far past the budget
        calls["n"] += 1
        sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 1000000000) SELECT count(*) FROM c"
        conn.execute(sql).fetchone()
        return real_verdict

    real_verdict = type("V", (), {"value": "VERIFIED"})()
    monkeypatch.setattr(citation_tool, "verify", slow)
    monkeypatch.setattr(citation_tool, "VERIFY_TIMEOUT_S", 0.05)
    res = _runner(_db(tmp_path)).call("verify_citation", {"citation": "(1977) 3 SCC 247", "quote": "x"})
    assert (res.ok, res.error) == (False, "tool_error:CitationLookupTimeout")
    assert calls["n"] == 1  # not retried
