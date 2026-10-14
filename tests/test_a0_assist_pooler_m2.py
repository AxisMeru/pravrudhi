"""M2: one real login per role through the session pooler, with the pinned CA and full verification.

Skipped unless PRAVRUDHI_A0_M2=1. The two connection URLs are read from files named by A0_M2_ENGINE_URL_FILE and
A0_M2_SWEEPER_URL_FILE (or from the environment variables named in the config); the CA path comes from A0_M2_CA_FILE.
Nothing here prints a URL, a host, a user or a password: failures report which check failed, never a value.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pravrudhi.application.a0_assist import store as ST

pytestmark = pytest.mark.skipif(os.environ.get("PRAVRUDHI_A0_M2") != "1", reason="real pooler login test; set PRAVRUDHI_A0_M2=1")


def _url(file_env: str, var_env: str) -> str:
    path = os.environ.get(file_env, "")
    if path:
        return Path(path).read_text().strip()
    return os.environ.get(var_env, "").strip()


@pytest.fixture(scope="module")
def stores():
    ca = os.environ.get("A0_M2_CA_FILE", "")
    eng = _url("A0_M2_ENGINE_URL_FILE", "SUPABASE_ASSIST_DB_URL")
    swp = _url("A0_M2_SWEEPER_URL_FILE", "SUPABASE_ASSIST_DB_URL_SWEEPER")
    assert ca and Path(ca).is_file(), "A0_M2_CA_FILE must name the pinned CA file"
    assert eng and swp, "both connection URLs must be provided (files or environment)"
    return ST.PgSpendStore(eng, timeout_s=10, ca_file=ca), ST.PgSpendStore(swp, timeout_s=10, ca_file=ca)


def _show(store, name: str) -> str:
    return str(store._call(f"show {name}", ())[0][0])


@pytest.mark.parametrize("which", [0, 1], ids=["engine_role", "sweeper_role"])
def test_the_three_role_settings_are_in_force_over_the_real_pooler(stores, which):
    s = stores[which]
    seen = {n: _show(s, n) for n in ("search_path", "statement_timeout", "lock_timeout")}
    print({n: v for n, v in seen.items()})  # the three settings are the output of this test; they hold no secret
    assert seen["search_path"] == "assist"
    assert seen["statement_timeout"] == "3s"
    assert seen["lock_timeout"] == "2s"


@pytest.mark.parametrize("which", [0, 1], ids=["engine_role", "sweeper_role"])
def test_a_function_outside_assist_is_refused(stores, which):
    with pytest.raises(ST.StoreError):
        stores[which]._call("select pg_read_file('/etc/hostname')", ())
    with pytest.raises(ST.StoreError):
        stores[which]._call("select pg_ls_dir('/')", ())


def test_the_engine_role_reads_status_and_the_sweeper_role_lists_pending(stores):
    eng, swp = stores
    assert isinstance(eng.status(), dict)
    assert isinstance(swp.list_pending(10_000), list)


def test_the_canary_runs_through_the_sweeper_role_and_reports_every_category(stores):
    res = stores[1].canary()
    assert res.get("checks_version") == 2 and set(res["counts"]) >= {"role_drift", "owns_objects", "dangerous_functions"}
    print({"ok": res.get("ok"), "counts": res.get("counts")})  # counts only, never names of customer data
    assert res.get("ok") is True


def test_the_engine_role_cannot_call_the_sweepers_prune_but_can_call_the_canary(stores):
    eng, _ = stores
    with pytest.raises(ST.StoreError):
        eng.prune()
    assert eng.canary().get("checks_version") == 2
