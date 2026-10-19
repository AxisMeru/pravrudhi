# ruff: noqa: E501
"""B1 old-section look-up (POST /api/v1/section-lookup, flag wf_b1_section_lookup): the loader (pin, closed kinds, one-to-one flag recomputed,
dangling ids fail closed), the answer rules (a one-to-one line only for a verified row; everything else "check both texts"; absent = not in
the table; no inferred match; a body that is not held says so) and the route (flag off = absent, signed-in or key only, fixed 422, no echo)."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api import identity, roles
from pravrudhi.api.partner import PartnerApiConfig, build_partner_router
from pravrudhi.application import section_lookup as L
from pravrudhi.application import section_lookup_config as slc
from tests.flag_peek_util import peek_all_true
from tests.test_api_partner import _agent, _proof_script

USER = identity.User("u1", "user@example.test", "user")
CFG = PartnerApiConfig(rate_limit_per_minute=1000, max_concurrent=1, trust_proxy_header=False)
PATH = "/api/v1/section-lookup"
MODULE = "pravrudhi.application.section_lookup"
BARRED = (
    "current",
    "in force",
    "latest",
    "updated",
    "verbatim",
    "accurate",
    "complete",
    "equivalent",
    "replaces",
    "same as",
    "official mapping",
)


def _raw() -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, dict[str, Any]]]]:
    mp = json.loads((L.ASSET_DIR / "corresponding_sections_v1.json").read_text(encoding="utf-8"))
    ip = json.loads((L.ASSET_DIR / "ipc_bodies_v1.json").read_text(encoding="utf-8"))
    new = {
        c: {d["id"]: d for d in json.loads((L.NYAYA_DIR / f).read_text(encoding="utf-8"))["documents"]}
        for c, f in L._NEW_BODY_FILE.items()
    }
    return mp, ip, new


def _ask(code: str, section: str) -> dict[str, Any]:
    return L.lookup(L.SectionLookupRequest(code=code, section=section))


# ---- loader ------------------------------------------------------------------------------------------------------------------------


def test_the_shipped_assets_load_and_match_their_pins() -> None:
    a = L.load_assets()
    assert a["rows"]["IPC"] and a["rows"]["CrPC"] and a["ipc"]


def test_a_changed_asset_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    L.load_assets.cache_clear()
    monkeypatch.setattr(L, "PIN_MAPPING", "0" * 64)
    with pytest.raises(L.AssetChanged):
        L.load_assets()
    L.load_assets.cache_clear()


def test_a_row_whose_one_to_one_flag_disagrees_with_its_evidence_is_rejected() -> None:
    mp, ip, new = _raw()
    row = next(r for r in mp["tables"]["IPC_to_BNS"]["rows"] if r["display_ok_as_one_to_one"])
    row["hand_check_required"] = True  # the flag still says true
    with pytest.raises(L.AssetChanged):
        L.build_assets(mp, ip, new)
    mp, ip, new = _raw()
    row = next(r for r in mp["tables"]["IPC_to_BNS"]["rows"] if r["kind"] == "merged")
    row["display_ok_as_one_to_one"] = True
    with pytest.raises(L.AssetChanged):
        L.build_assets(mp, ip, new)


def test_a_dangling_new_id_a_kind_outside_the_list_and_a_duplicate_row_are_rejected() -> None:
    mp, ip, new = _raw()
    mp["tables"]["IPC_to_BNS"]["rows"][0]["new_ids"] = ["BNS/Section 99999"]
    with pytest.raises(L.AssetChanged):
        L.build_assets(mp, ip, new)
    mp, ip, new = _raw()
    mp["tables"]["IPC_to_BNS"]["rows"][0]["kind"] = "equivalent"
    with pytest.raises(L.AssetChanged):
        L.build_assets(mp, ip, new)
    mp, ip, new = _raw()
    mp["tables"]["IPC_to_BNS"]["rows"].append(copy.deepcopy(mp["tables"]["IPC_to_BNS"]["rows"][0]))
    with pytest.raises(L.AssetChanged):
        L.build_assets(mp, ip, new)


def test_a_string_changed_without_its_hash_is_rejected() -> None:
    mp, ip, new = _raw()
    mp["strings_pending_R1"]["b1_check_both"]["text"] += " x"
    with pytest.raises(L.AssetChanged):
        L.build_assets(mp, ip, new)


def test_every_one_to_one_row_names_exactly_one_new_section_and_the_row_counts_are_not_in_any_string() -> None:
    mp, _ip, _new = _raw()
    for t in mp["tables"].values():
        for r in t["rows"]:
            if r["display_ok_as_one_to_one"]:
                assert len(r["new_ids"]) == 1 and r["kind"] == "one_to_one" and not r["hand_check_required"]
    for k, v in mp["strings_pending_R1"].items():
        if k != "b1_ipc_source":  # the one string that carries a date: the file's own ("January 2007")
            assert not any(ch.isdigit() for ch in v["text"].replace("{new}", "")), k  # no counts


# ---- answers ---------------------------------------------------------------------------------------------------------------------


def test_a_verified_one_to_one_row_names_its_section_and_shows_both_texts() -> None:
    r = _ask("IPC", "302")
    assert r["state"] == "one_to_one" and [n["id"] for n in r["new"]] == ["BNS/Section 103"]
    assert r["old"]["held"] and r["new"][0]["held"] and "Section 103" in r["line"]
    assert r["new"][0]["text"] and r["old"]["text"]  # full text, never a silent cut


def test_a_merged_or_split_row_says_check_both_and_names_no_one_to_one() -> None:
    for sec in ("420", "376"):
        r = _ask("IPC", sec)
        assert r["state"] == "check_both" and "matches this section to one section:" not in r["line"]
        assert r["new"], sec  # both official texts side by side


def test_a_hand_check_row_never_lists_a_mapping() -> None:
    for sec in ("63", "326A", "429", "472", "498A", "54", "55", "29A"):
        r = _ask("IPC", sec)
        assert r["state"] in ("check_both", "no_match") and r["new"] == [], sec


def test_a_deleted_section_says_the_table_shows_no_corresponding_section() -> None:
    assert _ask("IPC", "124A")["state"] == "no_match"
    assert _ask("CrPC", "355")["state"] == "no_match"


def test_a_section_not_in_the_table_is_not_matched_by_inference() -> None:
    r = _ask("IPC", "999")
    assert r["state"] == "not_in_table" and r["new"] == [] and not r["old"]["held"]


def test_crpc_bodies_are_not_held_and_the_mapping_line_still_shows_where_one_to_one() -> None:
    r = _ask("CrPC", "154")
    assert r["state"] == "one_to_one" and r["old"]["held"] is False and r["new"][0]["id"] == "BNSS/Section 173"
    assert r["old"]["lines"] == [L.load_assets()["strings"]["b1_body_not_held"]]


def test_an_ipc_body_that_is_not_held_says_so() -> None:
    r = _ask("IPC", "354D")  # added after the 2007 file
    assert r["old"]["held"] is False
    r2 = _ask("IPC", "213")  # flagged unreliable at parse time: not shipped
    assert r2["old"]["held"] is False


def test_a_held_ipc_body_carries_the_source_the_currency_and_the_marks_lines() -> None:
    S = L.load_assets()["strings"]
    assert _ask("IPC", "302")["old"]["lines"] == [S["b1_ipc_source"], S["b1_ipc_currency"], S["b1_ipc_marks"]]


def test_no_customer_string_uses_a_barred_word_or_the_secondary_table() -> None:
    low = " ".join(v.lower() for v in L.load_assets()["strings"].values())
    assert not [w for w in BARRED if w in low]
    assert "capt" not in low and "kerala" not in low


@pytest.mark.parametrize(
    "code,section",
    [("ipc", "302"), ("BNS", "1"), ("IPC", ""), ("IPC", "30 2"), ("IPC", "1234"), ("IPC", "../x"), ("CrPC", "154;")],
)
def test_a_bad_code_or_section_is_refused_without_echo(code: str, section: str) -> None:
    with pytest.raises(ValueError):
        _ask(code, section)


# ---- config + route ----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", [None, {}, {"wf_b1_section_lookup": "true"}, {"wf_b1_section_lookup": 1}, {"wf_b1_section_lookup": False}]
)
def test_only_the_boolean_true_turns_the_look_up_on(body: Any) -> None:
    assert slc.parse_section_lookup_config(body) is None
    assert slc.parse_section_lookup_config({"wf_b1_section_lookup": True}) == slc.SectionLookupConfig()
    assert slc.parse_section_lookup_config({"wf_b1_section_lookup": True, "section_lookup_max_concurrent": 0}) is None


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(identity, "PARTNER_KEY_ROUTES", identity.PARTNER_KEY_ROUTES)
    monkeypatch.setattr(identity, "PARTNER_KEY_PATHS", identity.PARTNER_KEY_PATHS)
    monkeypatch.setattr(roles, "USER_FACING", roles.USER_FACING)


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, on: bool, user: Any = USER) -> TestClient:
    peek_all_true(monkeypatch)
    monkeypatch.setattr(slc, "load_section_lookup_config", lambda _root: slc.SectionLookupConfig() if on else None)
    app = FastAPI()
    agent = _agent(tmp_path, _proof_script())
    app.include_router(build_partner_router(tmp_path, agent_factory=lambda _r: agent, config=CFG))
    app.dependency_overrides[identity.current_user] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def test_with_the_flag_off_the_route_is_absent_and_nothing_is_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    before = (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)
    c = _client(tmp_path, monkeypatch, on=False)
    assert c.post(PATH, json={"code": "IPC", "section": "302"}).status_code == 404
    assert MODULE not in sys.modules
    assert before == (identity.PARTNER_KEY_ROUTES, identity.PARTNER_KEY_PATHS, roles.USER_FACING)


def test_a_signed_in_user_gets_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(tmp_path, monkeypatch, on=True)
    r = c.post(PATH, json={"code": "IPC", "section": "302"})
    assert r.status_code == 200 and r.json()["state"] == "one_to_one" and r.json()["run_id"].startswith("lookup-")
    assert ("POST", PATH) in identity.PARTNER_KEY_ROUTES and PATH in roles.USER_FACING


def test_an_anonymous_caller_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(tmp_path, monkeypatch, on=True, user=None)
    assert c.post(PATH, json={"code": "IPC", "section": "302"}).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": "IPC"},
        {"code": "IPC", "section": "302", "x": 1},
        {"code": 1, "section": "302"},
        {"code": "BNS", "section": "1"},
        {"code": "IPC", "section": "zz-secret"},
    ],
)
def test_a_bad_request_gets_one_fixed_422_with_no_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any]
) -> None:
    c = _client(tmp_path, monkeypatch, on=True)
    r = c.post(PATH, json=body)
    assert r.status_code == 422 and r.json() == L.BAD_BODY and "zz-secret" not in r.text


def test_a_changed_asset_answers_the_fixed_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    c = _client(tmp_path, monkeypatch, on=True)
    L.load_assets.cache_clear()
    monkeypatch.setattr(L, "PIN_IPC_BODIES", "0" * 64)
    r = c.post(PATH, json={"code": "IPC", "section": "302"})
    L.load_assets.cache_clear()
    assert r.status_code == 503 and r.json() == {"detail": L.UNAVAILABLE, "error": "lookup_unavailable"}


def test_the_bns_source_line_is_the_one_r1_signed() -> None:
    v = L.load_assets()["strings"]["b1_bns_source"]
    assert v == "BNS and BNSS text is from India Code (Legislative Department, Ministry of Law and Justice)."
    import hashlib

    assert hashlib.sha256(v.encode()).hexdigest()[:16] == "b1abd5288f60d06e"


def test_a_trailing_newline_or_space_is_not_a_section_and_is_not_echoed() -> None:
    for bad in ("302\n", "302 ", " 302", "302\r\n", "\n302"):
        with pytest.raises(ValueError):
            _ask("IPC", bad)


def test_a_trailing_newline_over_the_route_gets_the_fixed_422_without_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _client(tmp_path, monkeypatch, on=True)
    r = c.post(PATH, json={"code": "IPC", "section": "302\n"})
    assert r.status_code == 422 and r.json() == L.BAD_BODY


def test_a_failed_asset_check_is_cached_so_a_bad_pin_does_not_rehash_every_request(monkeypatch: pytest.MonkeyPatch) -> None:
    L.load_assets.cache_clear()
    monkeypatch.setattr(L, "PIN_MAPPING", "0" * 64)
    calls: list[int] = []
    real = L._sha
    monkeypatch.setattr(L, "_sha", lambda p: (calls.append(1), real(p))[1])
    for _ in range(5):
        with pytest.raises(L.AssetChanged):
            L.load_assets()
    assert len(calls) <= 2  # the first request hashed (the mapping fails before the second file is read); the next four did not
    L.load_assets.cache_clear()
    monkeypatch.undo()
    assert L.load_assets()["rows"]  # a clear reloads


def test_the_asset_strings_are_exactly_the_ones_r1_signed() -> None:
    """tests/fixtures/b1_r1_signed_strings.txt holds the 12 keyed strings as R1's files give them (key | sha256[:16] | text)."""
    import hashlib

    signed: dict[str, tuple[str, str]] = {}
    for ln in (Path(__file__).parent / "fixtures" / "b1_r1_signed_strings.txt").read_text(encoding="utf-8").splitlines():
        if ln and not ln.startswith("#"):
            k, h, txt = (p.strip() for p in ln.split(" | ", 2))
            assert hashlib.sha256(txt.encode()).hexdigest()[:16] == h, k
            signed[k] = (h, txt)
    mp, _ip, _new = _raw()
    got = {k: (v["sha256_16"], v["text"]) for k, v in mp["strings_pending_R1"].items()}
    assert got == signed


def test_a_malformed_or_missing_body_asset_fails_closed_not_a_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "bns_sections.json").write_text("{not json", encoding="utf-8")  # a changed byte in a body file
    monkeypatch.setattr(L, "NYAYA_DIR", tmp_path)
    L.load_assets.cache_clear()
    with pytest.raises(L.AssetChanged):
        L.load_assets()
    monkeypatch.undo()
    L.load_assets.cache_clear()
    assert L.load_assets()["rows"]


def test_the_answer_body_has_no_per_caller_field_except_the_run_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    a = _client(tmp_path, monkeypatch, on=True, user=USER).post(PATH, json={"code": "IPC", "section": "302"}).json()
    b = (
        _client(tmp_path, monkeypatch, on=True, user=identity.User("u2", "two@example.test", "user"))
        .post(PATH, json={"code": "IPC", "section": "302"})
        .json()
    )
    assert a["run_id"] != b["run_id"]
    a.pop("run_id")
    b.pop("run_id")
    assert a == b
