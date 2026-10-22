# ruff: noqa: E501
"""INTEGRATION: supabase/wake_migration_v1.sql run for real against a throwaway Postgres container.

Skipped unless PRAVRUDHI_WAKE_PG_IT=1 and PRAVRUDHI_WAKE_PG_IMAGE names a Postgres image already present on this machine (for
example the pinned supabase/postgres image of test_tenancy_pg_it.py, or postgres:16). Needs docker. A throwaway container on
loopback, removed at the end; passwords are random and live only inside it. It proves what the structure test can only read:
the function semantics in SQL (the same cases as the Python port in tests/wake_store_fakes.py), the unique-key replay guard, ONE
wake started under concurrent begins, and that anon / authenticated / the engine's login role cannot touch a table directly."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PRAVRUDHI_WAKE_PG_IT") != "1" or not os.environ.get("PRAVRUDHI_WAKE_PG_IMAGE"),
    reason="integration test; set PRAVRUDHI_WAKE_PG_IT=1 and PRAVRUDHI_WAKE_PG_IMAGE=<a local postgres image>",
)

ROOT = Path(__file__).resolve().parent.parent / "supabase"
NAME = "wake-it"
PW = secrets.token_hex(8)


def sh(*a: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(a), capture_output=True, text=True, timeout=180)


def psql(sql: str, user: str = "postgres", db: str = "postgres") -> tuple[int, str]:
    r = sh("docker", "exec", "-e", f"PGPASSWORD={PW}", NAME, "psql", "-h", "127.0.0.1", "-U", user, "-d", db, "-X", "-A", "-t", "-q", "-c", sql)
    return r.returncode, (r.stdout + r.stderr).strip()


def call(sql: str) -> dict:
    code, out = psql(f"select {sql}", user="tenancy_rpc")
    assert code == 0, out
    return json.loads(out)


def nonce() -> str:
    return secrets.token_hex(16)


@pytest.fixture(scope="module", autouse=True)
def db() -> Iterator[None]:
    sh("docker", "rm", "-f", NAME)
    r = sh("docker", "run", "-d", "--name", NAME, "-e", f"POSTGRES_PASSWORD={PW}", os.environ["PRAVRUDHI_WAKE_PG_IMAGE"])
    assert r.returncode == 0, r.stderr
    try:
        for _ in range(120):
            time.sleep(1)
            if psql("select 1")[0] == 0:
                break
        for role in ("anon", "authenticated"):
            psql(f"do $$ begin if not exists (select 1 from pg_roles where rolname='{role}') then create role {role} nologin; end if; end $$")
        psql(f"create role tenancy_rpc login noinherit nosuperuser nocreatedb nocreaterole nobypassrls password '{PW}'")
        psql("create schema if not exists tenancy; grant usage on schema tenancy to tenancy_rpc, anon, authenticated")
        for f in ("wake_migration_v1.sql",):
            sh("docker", "cp", str(ROOT / f), f"{NAME}:/tmp/{f}")
            m = sh("docker", "exec", "-e", f"PGPASSWORD={PW}", NAME, "psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "postgres", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", f"/tmp/{f}")
            assert m.returncode == 0, m.stderr
        yield
    finally:
        sh("docker", "rm", "-f", "-v", NAME)


def test_ensure_is_one_live_request_per_judge_and_deliver_answers_once() -> None:
    n1 = nonce()
    assert call(f"tenancy.wake_ensure('first','{n1}',300,120,900)")["reused"] is False
    assert call(f"tenancy.wake_ensure('first','{nonce()}',300,120,900)")["reused"] is True
    rows = call("tenancy.wake_open(20,120)")
    assert [r["label"] for r in rows] == ["first"] and rows[0]["nonce"] == n1
    assert call(f"tenancy.wake_deliver('[{{\"nonce\":\"{n1}\",\"state\":\"granted\",\"lease_ref\":\"lease-1\"}}]',120)")["applied"] == 1
    assert call(f"tenancy.wake_deliver('[{{\"nonce\":\"{n1}\",\"state\":\"refused\"}}]',120)")["applied"] == 0
    st = call("tenancy.wake_state('first',120)")
    assert st["found"] is True and st["answer"] == "granted" and call("tenancy.wake_lease('first')")["lease"] == "lease-1"
    assert call("tenancy.wake_open(20,120)") == []


def test_the_nonce_guard_is_atomic() -> None:
    n = nonce()
    assert call(f"tenancy.wake_nonce('{n}',180)")["first"] is True
    assert call(f"tenancy.wake_nonce('{n}',180)")["first"] is False


def test_concurrent_begins_start_exactly_one_wake() -> None:
    out: list[str] = []
    ts = [threading.Thread(target=lambda: out.append(call("tenancy.wake_begin('it-user',6,120,450,86400)")["result"])) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out.count("new") == 1 and out.count("existing") == 7
    stamp = call("tenancy.wake_active('it-user',450)")["stamp"]
    assert call(f"tenancy.wake_end('it-user',false,{stamp + 5},120)")["applied"] is False  # a late end of another wake: a no-op
    assert call(f"tenancy.wake_end('it-user',false,{stamp},120)")["applied"] is True
    assert call("tenancy.wake_begin('it-user',6,120,450,86400)")["result"] == "cooldown"


def test_nobody_but_the_functions_can_touch_a_table() -> None:
    statements = (
        "select * from tenancy.wake_requests",
        "insert into tenancy.wake_nonces values ('" + "0" * 32 + "', 1)",
        "delete from tenancy.wake_limits",
    )
    for sql in statements:
        code, out = psql(sql, user="tenancy_rpc")  # the engine's own login role: functions only
        assert code != 0 and "permission denied" in out, (sql, out)
        for role in ("anon", "authenticated"):  # the REST roles, entered as the owner would
            code, out = psql(f"set role {role}; {sql}")
            assert code != 0 and "permission denied" in out, (role, sql, out)


def test_the_down_migration_removes_every_object() -> None:
    sh("docker", "cp", str(ROOT / "wake_migration_v1_down.sql"), f"{NAME}:/tmp/down.sql")
    m = sh("docker", "exec", "-e", f"PGPASSWORD={PW}", NAME, "psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "postgres", "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", "/tmp/down.sql")
    assert m.returncode == 0, m.stderr
    assert psql("select count(*) from information_schema.tables where table_schema='tenancy' and table_name like 'wake_%'")[1] == "0"
    assert psql("select count(*) from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='tenancy' and p.proname like '%wake%'")[1] == "0"
