"""#478: the fuzzy title scan reads and normalises the index's titles once per process (cached by file identity), and gives
the same confirmed set as the plain per-title `_fuzzy_match` scan."""

import os
import random
import sqlite3
import threading
from pathlib import Path

from pravrudhi.application import verify
from pravrudhi.application.case_index import CaseRecord, insert_case, open_index
from pravrudhi.application.verify import _fuzzy_match, _fuzzy_scan, _title_rows, normalize_party_name, warm_title_cache

_WORDS = [
    "Aldric",
    "Vennoway",
    "Zorbland",
    "State",
    "of",
    "Kumari",
    "Kumar",
    "Ramachandran",
    "Ramchandran",
    "Union",
    "India",
    "&",
    "Ors.",
    "S.A.",
    "Co",
]


def _make(path: Path, titles: list[str]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cases (case_id TEXT PRIMARY KEY, title TEXT, text TEXT)")
    conn.executemany("INSERT INTO cases VALUES (?, ?, '')", [(f"c{i}", t) for i, t in enumerate(titles)])
    conn.commit()
    conn.close()
    return path


def _random_titles(seed: int, n: int) -> list[str]:
    rng = random.Random(seed)
    return [" ".join(rng.choice(_WORDS) for _ in range(rng.randint(1, 5))) for _ in range(n)]


def test_the_cached_scan_confirms_exactly_the_cases_the_per_title_scan_confirms(tmp_path):
    titles = _random_titles(478, 3000)
    path = _make(tmp_path / "t.sqlite3", titles)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = _title_rows(conn)
    assert rows is not None and len(rows) == 3000
    hits = 0
    for target in _random_titles(5, 60) + ["aldric vennoway state of zorbland", "ramachandran union of india"]:
        t = normalize_party_name(target)
        want = [f"c{i}" for i, title in enumerate(titles) if _fuzzy_match(t, title)]
        assert _fuzzy_scan(t, rows) == want, target
        hits += bool(want)
    assert hits > 10  # not vacuous


def test_the_cache_is_keyed_on_the_file_so_a_rewritten_index_is_never_served_from_the_old_copy(tmp_path):
    path = _make(tmp_path / "t.sqlite3", ["Aldric Vennoway v. State of Zorbland"])
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    first = _title_rows(conn)
    assert first is not None and len(first) == 1
    conn.close()
    os.unlink(path)
    _make(path, ["Aldric Vennoway v. State of Zorbland", "Kumari v. Union of India"])
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    second = _title_rows(conn)
    assert second is not None and len(second) == 2
    conn.close()


def test_a_database_with_no_file_is_scanned_as_before(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE cases (case_id TEXT, title TEXT, text TEXT)")
    assert _title_rows(conn) is None


def test_the_second_scan_does_not_read_the_titles_again(tmp_path, monkeypatch):
    path = _make(tmp_path / "t.sqlite3", _random_titles(1, 200))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    _title_rows(conn)
    calls = []
    monkeypatch.setattr(verify, "normalize_party_name", lambda s: calls.append(s) or s)
    _title_rows(conn)
    assert calls == []


def test_warm_title_cache_reports_the_title_count_and_survives_a_missing_file(tmp_path):
    path = _make(tmp_path / "t.sqlite3", _random_titles(2, 50))
    assert warm_title_cache(path) == 50
    assert warm_title_cache(tmp_path / "absent.sqlite3") == 0


# ---- the four points of the PR (A) review brief: staleness, warm-up fail-open, thread safety, memory ------------------------


def _answer(path: Path, citation: str, name: str | None = None) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return str(verify.exists_in_index(conn, citation, name))
    finally:
        conn.close()


def _alias_index(path: Path, titles: list[str], aliases: list[tuple[str, str, str]]) -> Path:
    """A real (FTS and alias tables) index: each title is a case; one citing case carries one mention per alias."""
    conn = open_index(path)
    for i, t in enumerate(titles):
        insert_case(conn, CaseRecord(f"c{i}", t, "Supreme Court", 2011, "sc_pdf", "/x", ""))
    cite = " ".join(f"This Court in {p1} v. {p2} {key} held that" for key, p1, p2 in aliases)
    insert_case(conn, CaseRecord("citer", "Citer v Other", "Supreme Court", 2005, "sc_pdf", "/y", cite))
    conn.commit()
    conn.close()
    return path


def test_staleness_a_swapped_file_under_a_running_process_is_never_answered_from_the_old_titles(tmp_path):
    """POINT 1a: the index file is REPLACED (a new file moved into place, as an index rebuild does) while the process runs."""
    live = tmp_path / "live.sqlite3"
    _make(live, ["Aldric Vennoway v. State of Zorbland"])
    conn = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    assert [r[1] for r in _title_rows(conn) or ()] == ["aldric vennoway v state of zorbland"]
    conn.close()
    new = _make(tmp_path / "new.sqlite3", ["Kumari v. Union of India", "Ramachandran v. State of Zorbland"])
    os.replace(new, live)  # the swap
    conn = sqlite3.connect(f"file:{live}?mode=ro", uri=True)
    rows = _title_rows(conn)
    conn.close()
    assert rows is not None and [r[1] for r in rows] == ["kumari v union of india", "ramachandran v state of zorbland"]


def test_staleness_an_in_place_rewrite_is_never_answered_from_the_old_titles(tmp_path):
    """POINT 1b: the SAME file is modified in place (a writer commits): the header change counter and mtime move the key."""
    path = _make(tmp_path / "t.sqlite3", ["Aldric Vennoway v. State of Zorbland"])
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert len(_title_rows(ro) or ()) == 1
    w = sqlite3.connect(path)
    w.execute("INSERT INTO cases VALUES ('c9', 'Kumari v. Union of India', '')")
    w.execute("UPDATE cases SET title = 'Vennoway Aldric v. State of Zorbland' WHERE case_id = 'c0'")
    w.commit()
    w.close()
    ro2 = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = _title_rows(ro2)
    assert rows is not None and sorted(r[1] for r in rows) == ["kumari v union of india", "vennoway aldric v state of zorbland"]
    ro.close()
    ro2.close()


def test_staleness_the_answer_follows_the_swapped_index(tmp_path):
    """POINT 1c, end to end: the fuzzy confirmation of a spelling variant reads the NEW index's titles after the swap."""
    alias = [("(2011) 9 SCC 257", "Aldric Vennoway", "State of Zorbland")]
    live = _alias_index(tmp_path / "live.sqlite3", ["Aldric Vennoway vs State of Zorbland"], alias)
    assert _answer(live, "(2011) 9 SCC 257") == "IN_INDEX"  # the case holding the citation is found
    other = _alias_index(tmp_path / "other.sqlite3", ["Kumari v. Union of India"], alias)
    os.replace(other, live)
    assert _answer(live, "(2011) 9 SCC 257") == "NOT_IN_INDEX"  # no title of the new index matches


def test_the_warm_up_failing_does_not_stop_the_router_or_the_first_lookup(tmp_path, monkeypatch, caplog):
    """POINT 2: a warm-up that raises is logged once and nothing waits for it; the first lookup loads what it needs itself."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pravrudhi.api import partner

    idx = _alias_index(
        tmp_path / "idx.sqlite3",
        ["Aldric Vennoway vs State of Zorbland"],
        [("(2011) 9 SCC 257", "Aldric Vennoway", "State of Zorbland")],
    )
    monkeypatch.setenv("PRAVRUDHI_CITATION_INDEX", str(idx))
    monkeypatch.delenv("PRAVRUDHI_CITATION_WARM", raising=False)
    started = threading.Event()

    def boom(_path):
        started.set()
        raise RuntimeError("warm-up boom")

    monkeypatch.setattr(partner, "warm_title_cache", boom)
    cfg = partner.PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    with caplog.at_level("WARNING"):
        app = FastAPI()
        app.include_router(partner.build_partner_router(tmp_path, config=cfg))  # the env-named index: the warm-up thread starts
        assert started.wait(5)
        client = TestClient(app)
        r = client.post("/api/v1/verify-citations", json={"citation": "(2011) 9 SCC 257"})
    assert r.status_code == 200 and r.json()["result"] == "IN_INDEX"
    assert sum("warm-up failed" in m for m in caplog.messages) == 1


def test_a_slow_warm_up_does_not_block_the_router_or_a_lookup_and_the_env_opt_out_starts_no_thread(tmp_path, monkeypatch):
    """POINT 2: nothing waits on the warm-up (the route answers while it runs); PRAVRUDHI_CITATION_WARM=0 starts none."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from pravrudhi.api import partner

    idx = _alias_index(
        tmp_path / "idx.sqlite3",
        ["Aldric Vennoway vs State of Zorbland"],
        [("(2011) 9 SCC 257", "Aldric Vennoway", "State of Zorbland")],
    )
    monkeypatch.setenv("PRAVRUDHI_CITATION_INDEX", str(idx))
    gate, running = threading.Event(), threading.Event()

    def slow(_path):
        running.set()
        gate.wait(10)

    monkeypatch.setattr(partner, "warm_title_cache", slow)
    cfg = partner.PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=100, trust_proxy_header=False)
    monkeypatch.delenv("PRAVRUDHI_CITATION_WARM", raising=False)
    app = FastAPI()
    app.include_router(partner.build_partner_router(tmp_path, config=cfg))
    assert running.wait(5)  # the warm-up is running and blocked
    r = TestClient(app).post("/api/v1/verify-citations", json={"citation": "(2011) 9 SCC 257"})
    assert r.status_code == 200 and r.json()["result"] == "IN_INDEX"  # the lookup did not wait for it
    gate.set()
    # opt-out
    running.clear()
    monkeypatch.setenv("PRAVRUDHI_CITATION_WARM", "0")
    app2 = FastAPI()
    app2.include_router(partner.build_partner_router(tmp_path, config=cfg))
    assert not running.wait(0.5)


def test_concurrent_first_lookups_build_the_cache_once_and_agree(tmp_path, monkeypatch):
    """POINT 3: sixteen threads whose lookups need the fuzzy scan (a misspelt party) hit a cold cache together, with a
    warm-up call among them: the cache is built exactly ONCE, every thread sees the complete cache, and every answer equals the
    single-threaded one."""
    aliases = [
        ("(2011) 9 SCC 257", "Aldrik Vennoway", "State of Zorbland")
    ]  # the title spells it "Aldric": exact FTS fails, fuzzy runs
    path = _alias_index(tmp_path / "idx.sqlite3", ["Aldric Vennoway vs State of Zorbland", *_random_titles(33, 400)], aliases)

    builds: list[object] = []

    class Spy(dict):
        def __setitem__(self, key, value):
            builds.append(key)
            super().__setitem__(key, value)

    monkeypatch.setattr(verify, "_TITLE_ROWS", Spy())
    expected = _answer(path, "(2011) 9 SCC 257")
    assert len(builds) == 1  # the lookup really used the cache, and built it once
    verify._TITLE_ROWS.clear()
    builds.clear()

    barrier = threading.Barrier(17)
    results: list[object] = []

    def lookup():
        barrier.wait()
        results.append(_answer(path, "(2011) 9 SCC 257"))

    def warm():
        barrier.wait()
        results.append(warm_title_cache(path))

    threads = [threading.Thread(target=lookup) for _ in range(16)] + [threading.Thread(target=warm)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(results) == 17
    assert [r for r in results if isinstance(r, str)] == [expected] * 16
    assert [r for r in results if isinstance(r, int)] == [402]  # the warm-up saw the whole cache: 401 titles + the citing case
    assert len(builds) == 1


def _TITLE_ROWS_CLEAR() -> None:  # noqa: N802
    verify._TITLE_ROWS.clear()


def _rss_kb() -> int:
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * (os.sysconf("SC_PAGE_SIZE") // 1024)


def test_memory_ten_thousand_lookups_do_not_grow_the_process(tmp_path):
    """POINT 4: once the cache is built, 10,000 fuzzy scans change the process size by well under 20 MB (no per-lookup growth)."""
    import gc

    titles = _random_titles(44, 1500)
    path = _make(tmp_path / "t.sqlite3", titles)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = _title_rows(conn)
    assert rows is not None
    targets = [normalize_party_name(t) for t in _random_titles(9, 50)]
    for t in targets:  # settle: regex and allocator caches
        _fuzzy_scan(t, rows)
    gc.collect()
    before = _rss_kb()
    for i in range(10_000):
        _fuzzy_scan(targets[i % len(targets)], rows)
    gc.collect()
    assert _rss_kb() - before < 20 * 1024
    conn.close()


def test_a_null_title_does_not_stop_the_cache_or_the_warm_up(tmp_path):
    """A case with no title (NULL) is cached as an empty title, never an error: the warm-up thread cannot die on it."""
    path = _make(tmp_path / "t.sqlite3", ["Aldric Vennoway vs State of Zorbland"])
    w = sqlite3.connect(path)
    w.execute("INSERT INTO cases VALUES ('cnull', NULL, '')")
    w.commit()
    w.close()
    assert warm_title_cache(path) == 2
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = _title_rows(conn)
    conn.close()
    assert rows is not None and rows[1][:3] == ("cnull", "", 0)


def test_a_wal_commit_moves_the_cache_key(tmp_path):
    """An index in WAL mode updated in place: the commit lands in the -wal file and leaves the main file untouched, but the
    cache still reloads (the -wal stat is part of the key)."""
    path = _make(tmp_path / "t.sqlite3", ["Aldric Vennoway vs State of Zorbland"])
    w = sqlite3.connect(path)
    w.execute("PRAGMA journal_mode=WAL")
    w.commit()
    ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert len(_title_rows(ro) or ()) == 1
    w.execute("INSERT INTO cases VALUES ('c9', 'Kumari vs Union of India', '')")
    w.commit()  # not checkpointed: the main file is unchanged
    ro2 = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = _title_rows(ro2)
    assert rows is not None and len(rows) == 2
    w.close()
    ro.close()
    ro2.close()
