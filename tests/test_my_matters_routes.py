"""POST /api/v1/matter-spans/my-matters, registered only when wf_a2_list_matters is true (and A2 is on): the caller's OWN matters
(matter id and fully indexed paper count). Both arms of the flag, the same organisation from a fresh session (a second key), no
sight of another organisation's matters (an empty list, never a refusal), delete-matter from a fresh session, the audit row, the
key-only gate, nothing created for a tenant with no matters, and the object-backed store. Invented text."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pravrudhi.api import identity, partner, roles
from pravrudhi.api.server import create_app
from pravrudhi.application import audit, tenancy
from pravrudhi.application import matter_objectstore as O
from pravrudhi.application import matter_spans_config as msc
from pravrudhi.application.matter_spans import MatterNotFound, MatterStore
from pravrudhi.application.matter_spans_config import MatterSpansConfig
from tests.flag_peek_util import ALL_FLAG_KEYS, peek_all_true

REPO = Path(__file__).resolve().parent.parent
A2 = "/api/v1/matter-spans"
MINE = f"{A2}/my-matters"
PAPER = "1. The notice was sent on 4 May.\n\n2. The reply came on 9 June.\fPage two holds the NEEDLEWORD."


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(msc.DIR_ENV, str(tmp_path / "store"))
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, list_flag: bool = True) -> tuple[TestClient, Path]:
    monkeypatch.setattr(msc, "load_matter_spans_config", lambda _root: MatterSpansConfig())
    peek_all_true(monkeypatch)  # the A2 slice on (every reader-backed flag true; the patched loaders decide)
    flags: dict[str, Any] = dict.fromkeys(ALL_FLAG_KEYS, True)
    flags["wf_a2_list_matters"] = list_flag  # this flag has no config reader: the router reads it from the one peek
    monkeypatch.setattr(partner, "_peek_flags", lambda _r: flags)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "s" * 40)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    monkeypatch.setenv(tenancy.TENANCY_PROVISION_SECRET_ENV, "p" * 40)
    root = tmp_path / "root"
    if not root.exists():
        shutil.copytree(REPO / "configs", root / "configs")
        tenancy.create_org(root, "acme", "Acme")
        tenancy.create_org(root, "zorb", "Zorb")
    return TestClient(create_app(root), headers={"host": "127.0.0.1:8008"}, raise_server_exceptions=False), root


def _key(root: Path, org: str) -> dict[str, str]:
    return {tenancy.API_KEY_HEADER: tenancy.create_key(root, org, label="t").secret}


def _matter(c: TestClient, h: dict[str, str], paper: bool = False) -> str:
    m = c.post(f"{A2}/matters", json={}, headers=h).json()["matter_id"]
    if paper:
        r = c.post(f"{A2}/documents", json={"matter_id": m, "text": PAPER, "name": "p.pdf"}, headers=h)
        assert r.status_code == 200, r.text
    return m


def test_with_the_flag_off_the_route_is_absent_and_the_released_routes_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, root = _app(monkeypatch, tmp_path, list_flag=False)
    h = _key(root, "acme")
    assert MINE not in {p for _m, p in identity.PARTNER_KEY_ROUTES} and MINE not in roles.USER_FACING
    off = c.post(MINE, json={}, headers=h)
    assert not (
        off.status_code == 200 and off.headers.get("content-type", "").startswith("application/json") and "matters" in off.text
    )
    assert c.post(f"{A2}/matters", json={}, headers=h).status_code == 200  # A2 itself is untouched


def test_the_route_lists_own_matters_with_paper_counts_and_nothing_else(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, root = _app(monkeypatch, tmp_path)
    h = _key(root, "acme")
    empty, full = _matter(c, h), _matter(c, h, paper=True)
    r = c.post(MINE, json={}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"matters"} and {m["matter_id"]: m["papers"] for m in body["matters"]} == {empty: 0, full: 1}
    assert all(set(m) == {"matter_id", "papers"} for m in body["matters"])  # no names, no text, no tenant
    assert "NEEDLEWORD" not in r.text and "p.pdf" not in r.text and "acme" not in r.text


def test_a_second_key_of_the_same_organisation_sees_the_same_list_and_can_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, root = _app(monkeypatch, tmp_path)
    first, fresh = _key(root, "acme"), _key(root, "acme")  # a fresh session: a different key of the same organisation
    m1, m2 = _matter(c, first, paper=True), _matter(c, first)
    assert {x["matter_id"] for x in c.post(MINE, json={}, headers=fresh).json()["matters"]} == {m1, m2}
    d = c.post(f"{A2}/delete", json={"matter_id": m1}, headers=fresh)
    assert d.status_code == 200 and d.json()["deleted"] is True
    assert [x["matter_id"] for x in c.post(MINE, json={}, headers=first).json()["matters"]] == [m2]
    assert not any(p.is_file() and b"NEEDLEWORD" in p.read_bytes() for p in (tmp_path / "store").rglob("*"))


def test_another_organisation_sees_an_empty_list_not_a_refusal_and_cannot_delete_ours(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, root = _app(monkeypatch, tmp_path)
    ours, theirs = _key(root, "acme"), _key(root, "zorb")
    mine = _matter(c, ours, paper=True)
    r = c.post(MINE, json={}, headers=theirs)
    assert r.status_code == 200 and r.json() == {"matters": []} and mine not in r.text
    assert c.post(f"{A2}/delete", json={"matter_id": mine}, headers=theirs).status_code == 404
    assert [x["matter_id"] for x in c.post(MINE, json={}, headers=ours).json()["matters"]] == [mine]  # still there
    a = c.post(MINE, json={}, headers=theirs)
    b = c.post(MINE, json={}, headers=_key(root, "zorb"))
    assert a.json() == b.json() == {"matters": []}  # no difference between "never had any" and "someone else has some"


def test_listing_creates_nothing_for_a_tenant_with_no_matters(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, root = _app(monkeypatch, tmp_path)
    assert c.post(MINE, json={}, headers=_key(root, "zorb")).json() == {"matters": []}
    assert not (tmp_path / "store").exists() or not any((tmp_path / "store").iterdir())


def test_the_route_is_key_only_and_audited_like_the_other_a2_routes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    c, root = _app(monkeypatch, tmp_path)
    assert ("POST", MINE) in identity.PARTNER_KEY_ROUTES and MINE in roles.USER_FACING
    assert c.post(MINE, json={}).status_code == 401  # no credential
    h = _key(root, "acme")
    _matter(c, h, paper=True)
    assert c.post(MINE, json={}, headers=h).status_code == 200
    key_id = tenancy.keys_for_org(root, "acme")[-1].key_id
    rows, _n = audit.page(root, key_id, offset=0, limit=50, retention_s=3600, now=lambda: datetime.now(UTC))
    mine = [x for x in rows if str(x["run_id"]).startswith("matter-spans-")]
    assert mine and all(x["mode"] == "intake" for x in mine) and 200 in {x["status_code"] for x in mine}
    assert "NEEDLEWORD" not in str(rows) and "p.pdf" not in str(rows)


# ---- the stores -------------------------------------------------------------------------------------------------------------


def test_the_folder_store_lists_only_the_tenants_own_matters_newest_first(tmp_path: Path) -> None:
    s = MatterStore(tmp_path / "s", MatterSpansConfig())
    a, b = s.create_matter("org:acme"), s.create_matter("org:acme")
    s.add_document("org:acme", a, PAPER, "p.pdf")
    other = s.create_matter("org:zorb")
    got = s.list_matters("org:acme")["matters"]
    assert {m["matter_id"]: m["papers"] for m in got} == {a: 1, b: 0} and other not in str(got)
    assert s.list_matters("org:nobody") == {"matters": []} and not (tmp_path / "s" / "org-nobody").exists()
    s.delete_matter("org:acme", a)
    assert [m["matter_id"] for m in s.list_matters("org:acme")["matters"]] == [b]
    with pytest.raises(MatterNotFound):
        s.list_documents("org:acme", a)


def test_a_folder_that_is_not_a_matter_and_a_half_created_matter_are_left_out(tmp_path: Path) -> None:
    s = MatterStore(tmp_path / "s", MatterSpansConfig())
    good = s.create_matter("org:acme")
    tdir = next((tmp_path / "s").iterdir())
    (tdir / "not-a-matter").mkdir()
    (tdir / ("a" * 32)).mkdir()  # a matter-shaped folder with no database
    half = tdir / ("b" * 32)
    half.mkdir()
    (half / "matter.sqlite3").write_bytes(b"")  # a database file with no schema yet
    assert [m["matter_id"] for m in s.list_matters("org:acme")["matters"]] == [good]


def test_the_object_backed_store_lists_from_the_objects_on_a_cold_worker(tmp_path: Path) -> None:
    objects = O.MemoryObjects()

    def worker(name: str) -> O.ObjectBackedMatterStore:
        return O.ObjectBackedMatterStore(tmp_path / name, MatterSpansConfig(), objects, "pfx")

    w1 = worker("w1")
    a, b = w1.create_matter("org:acme"), w1.create_matter("org:acme")
    w1.add_document("org:acme", a, PAPER, "p.pdf")
    w1.create_matter("org:zorb")
    cold = worker("w2")  # a fresh worker: its local copy is empty
    got = {m["matter_id"]: m["papers"] for m in cold.list_matters("org:acme")["matters"]}
    assert got == {a: 1, b: 0} and cold.list_matters("org:nobody") == {"matters": []}
    cold.delete_matter("org:acme", a)
    assert [m["matter_id"] for m in w1.list_matters("org:acme")["matters"]] == [b]
