"""INTEGRATION: the tenancy backend against the pinned database image with the real migration.

Skipped unless PRAVRUDHI_TENANCY_PG_IT=1 and PRAVRUDHI_TENANCY_MIGRATION names the migration file (the applied one; not part of
this repository). Needs docker and the optional pg8000 driver (on PYTHONPATH). A throwaway container on loopback, removed at the
end; the role password is random and exists only inside it (the connection drops TLS: loopback, throwaway only). Proves the SQL
functions and the narrow role: what a keyed caller can do through `tenancy_rpc`, and nothing else, plus persistence across an
engine restart and the extra latency of the store on the request path (written to PRAVRUDHI_TENANCY_LATENCY_LOG when set)."""

from __future__ import annotations

import json
import os
import secrets
import statistics
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import audit, tenancy
from pravrudhi.application.tenancy_store import PgTenancyStore

pytestmark = pytest.mark.skipif(
    os.environ.get("PRAVRUDHI_TENANCY_PG_IT") != "1" or not os.environ.get("PRAVRUDHI_TENANCY_MIGRATION"),
    reason="integration test; set PRAVRUDHI_TENANCY_PG_IT=1 and PRAVRUDHI_TENANCY_MIGRATION=<migration file>",
)

IMAGE = "supabase/postgres@sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86"
NAME, PORT = "tenancy-it", 55438
PW = {"postgres": secrets.token_hex(8), "tenancy_rpc": secrets.token_hex(8)}
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def sh(*a: str, **k: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(a), capture_output=True, text=True, timeout=180, **k)


def psql(sql: str, user: str = "postgres") -> tuple[int, str]:
    r = sh(
        "docker",
        "exec",
        "-e",
        f"PGPASSWORD={PW[user]}",
        NAME,
        "psql",
        "-h",
        "127.0.0.1",
        "-U",
        user,
        "-d",
        "postgres",
        "-X",
        "-A",
        "-t",
        "-q",
        "-c",
        sql,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def _plain_connect(**kw: Any) -> Any:
    import pg8000.dbapi

    return pg8000.dbapi.connect(**{**kw, "ssl_context": None})


@pytest.fixture(scope="module")
def db() -> Iterator[PgTenancyStore]:
    sh("docker", "rm", "-f", NAME)
    r = sh(
        "docker", "run", "-d", "--name", NAME, "-p", f"127.0.0.1:{PORT}:5432", "-e", f"POSTGRES_PASSWORD={PW['postgres']}", IMAGE
    )
    assert r.returncode == 0, r.stderr
    try:
        for _ in range(120):
            time.sleep(1)
            if (
                psql("select count(*) from pg_roles where rolname in ('anon','authenticated','service_role','supabase_admin')")[1]
                == "4"
            ):
                break
        pw = PW["tenancy_rpc"]
        attrs = "login noinherit nosuperuser nocreatedb nocreaterole nobypassrls connection limit 10"
        psql(f"create role tenancy_rpc {attrs} password '{pw}'")
        sh("docker", "cp", os.environ["PRAVRUDHI_TENANCY_MIGRATION"], f"{NAME}:/tmp/m.sql")
        m = sh(
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={PW['postgres']}",
            NAME,
            "psql",
            "-h",
            "127.0.0.1",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "/tmp/m.sql",
        )
        assert m.returncode == 0, m.stderr
        dsn = f"postgresql://tenancy_rpc:{PW['tenancy_rpc']}@127.0.0.1:{PORT}/postgres"
        yield PgTenancyStore(dsn, timeout_s=3.0, connect=_plain_connect)
    finally:
        sh("docker", "rm", "-f", "-v", NAME)


@pytest.fixture
def backend(db: PgTenancyStore, monkeypatch: pytest.MonkeyPatch) -> PgTenancyStore:
    monkeypatch.setenv(tenancy.BACKEND_ENV, "supabase")
    monkeypatch.setattr(tenancy, "_store_from_env", lambda env: db)
    tenancy.reset_backend_for_tests()
    return db


def test_the_role_can_only_call_the_functions_and_see_no_table(db: PgTenancyStore) -> None:
    for q in (
        "select * from tenancy.org",
        "select * from tenancy.api_key",
        "select * from tenancy.audit",
        "insert into tenancy.org values ('x','x','x')",
        "delete from tenancy.api_key",
        "select * from public.users",
    ):
        rc, out = psql(q, "tenancy_rpc")
        assert rc != 0 and ("permission denied" in out or "does not exist" in out), (q, out[:80])
    assert db.canary()["ok"] is True
    rc, out = psql("select count(*) from information_schema.role_table_grants where grantee = 'tenancy_rpc'", "postgres")
    assert out == "0"
    rc, out = psql("select tenancy.canary()", "tenancy_rpc")
    assert rc == 0 and '"ok": true' in out


def test_the_sql_refuses_bad_arguments(db: PgTenancyStore) -> None:
    assert db.create_org("Bad_Upper", "x", "t")["reason"] == "bad_args"
    assert db.create_org("ok-org", "   ", "t")["reason"] == "bad_args"
    assert db.create_org("ok-org", "Ok", "t")["ok"] is True
    assert db.create_org("ok-org", "Ok", "t")["reason"] == "exists"
    row = {"key_id": "short", "org_id": "ok-org", "label": "", "hash": "h" * 30, "created": "t", "rate_limit_per_minute": 60}
    assert db.create_key(row)["reason"] == "bad_args"
    assert db.create_key({**row, "key_id": "abcdefgh12345678", "org_id": "nope"})["reason"] == "no_org"
    assert db.create_key({**row, "key_id": "abcdefgh12345678", "hash": "tooshort"})["reason"] == "bad_args"
    assert db.add_member("ok-org", "u1", "admin")["reason"] == "bad_args"
    assert db.add_member("nope", "u1", "member")["reason"] == "no_org"


def test_a_full_partner_flow_through_the_tenancy_module(backend: PgTenancyStore, tmp_path: Path) -> None:
    tenancy.create_org(tmp_path, "acme", "Acme Ltd")
    tenancy.add_member(tmp_path, "acme", "user-1", "owner")
    created = tenancy.create_key(tmp_path, "acme", label="prod", rate_limit_per_minute=30)
    assert tenancy.verify_key(tmp_path, created.secret).rate_limit_per_minute == 30
    assert [k.key_id for k in tenancy.keys_for_org(tmp_path, "acme")] == [created.record.key_id]
    assert tenancy.membership_role(tmp_path, "acme", "user-1") == "owner"
    assert tenancy.get_org(tmp_path, "acme").name == "Acme Ltd"  # type: ignore[union-attr]
    stored = backend._call("get_key", created.record.key_id)
    assert created.secret not in json.dumps(stored) and stored["hash"].startswith("$argon2id$")
    tenancy.record_usage(tmp_path, created.record.key_id, now=NOW)
    tenancy.record_usage(tmp_path, created.record.key_id, failed=True, now=NOW)
    assert tenancy.flush_usage()
    assert tenancy.usage_counts(tmp_path, created.record.key_id) == (1, 1)
    summary = tenancy.usage_summary(tmp_path, "acme", now=NOW, days=3)
    assert summary[0]["days"] == [{"day": "2026-10-14", "calls": 1, "failed": 1}]
    tenancy.revoke_key(tmp_path, created.record.key_id)
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(tmp_path, created.secret)
    assert not (tmp_path / ".pravrudhi").exists()


def test_keys_usage_and_audit_survive_an_engine_restart(backend: PgTenancyStore, tmp_path: Path) -> None:
    tenancy.create_org(tmp_path, "restart-org", "R")
    k = tenancy.create_key(tmp_path, "restart-org")
    audit.record(
        tmp_path,
        key_id=k.record.key_id,
        mode="sync",
        status_code=200,
        contract_ids=["bns69"],
        run_id="r1",
        outcomes={"bns69": "established"},
        retention_s=7776000.0,
        now=NOW,
    )
    tenancy.record_usage(tmp_path, k.record.key_id, now=NOW)
    assert tenancy.flush_usage()
    tenancy.reset_backend_for_tests()  # a new process: empty caches and queues, a NEW empty working directory
    fresh_root = tmp_path / "after-restart"
    assert tenancy.verify_key(fresh_root, k.secret).org_id == "restart-org"
    rows, nxt = audit.page(
        fresh_root, k.record.key_id, offset=0, limit=10, retention_s=7776000.0, now=lambda: NOW + timedelta(days=1)
    )
    assert [r["run_id"] for r in rows] == ["r1"] and nxt is None and rows[0]["outcomes"] == {"bns69": "established"}
    assert tenancy.usage_counts(fresh_root, k.record.key_id) == (1, 0)


def test_audit_retention_paging_and_the_90_day_cut(backend: PgTenancyStore, tmp_path: Path) -> None:
    kid = "auditkey00000001"
    for i in range(5):
        audit.record(
            tmp_path,
            key_id=kid,
            mode="assist",
            status_code=200,
            contract_ids=[],
            run_id=f"r{i}",
            retention_s=1000.0,
            now=NOW + timedelta(seconds=i),
        )
    assert tenancy.flush_usage()
    rows, nxt = audit.page(tmp_path, kid, offset=0, limit=2, retention_s=1000.0, now=lambda: NOW + timedelta(seconds=10))
    assert [r["run_id"] for r in rows] == ["r4", "r3"] and nxt == 2
    old, _ = audit.page(tmp_path, kid, offset=0, limit=10, retention_s=1000.0, now=lambda: NOW + timedelta(seconds=5000))
    assert old == []  # past retention is never served
    audit.record(
        tmp_path,
        key_id=kid,
        mode="assist",
        status_code=200,
        contract_ids=[],
        run_id="late",
        retention_s=1000.0,
        now=NOW + timedelta(seconds=5000),
    )
    assert tenancy.flush_usage()
    rows3, _ = audit.page(tmp_path, kid, offset=0, limit=10, retention_s=1000.0, now=lambda: NOW + timedelta(seconds=5001))
    assert [r["run_id"] for r in rows3] == ["late"]  # the write pruned the old rows of that key


def test_purge_removes_the_key_its_usage_and_its_audit(backend: PgTenancyStore, tmp_path: Path) -> None:
    tenancy.create_org(tmp_path, "purge-org", "P")
    k = tenancy.create_key(tmp_path, "purge-org")
    tenancy.record_usage(tmp_path, k.record.key_id, now=NOW)
    audit.record(tmp_path, key_id=k.record.key_id, mode="sync", status_code=200, contract_ids=[], retention_s=1e9, now=NOW)
    assert tenancy.flush_usage()
    assert backend.purge_key(k.record.key_id)["ok"] is True
    assert backend.get_key(k.record.key_id) is None and backend.usage_counts(k.record.key_id) == (0, 0)
    assert backend.audit_page(k.record.key_id, 0, 10, "1970-01-01T00:00:00+00:00")[1] == 0


def test_request_path_latency_of_the_store_on_loopback(backend: PgTenancyStore, tmp_path: Path) -> None:
    """Measured, not gated beyond a generous bound: the cost of one get_key and one usage write on a persistent connection to a
    LOCAL database. The hosted figure adds the network round trip to the pooler (measured on the dev stack against it)."""
    tenancy.create_org(tmp_path, "lat-org", "L")
    k = tenancy.create_key(tmp_path, "lat-org")
    backend.get_key(k.record.key_id)  # opens the connection
    get_ms = []
    for _ in range(200):
        t = time.perf_counter()
        backend.get_key(k.record.key_id)
        get_ms.append((time.perf_counter() - t) * 1000)
    use_ms = []
    for _ in range(200):
        t = time.perf_counter()
        backend.record_usage(k.record.key_id, "2026-10-14", False)
        use_ms.append((time.perf_counter() - t) * 1000)
    tenancy.reset_backend_for_tests()
    verify_ms = []
    for _ in range(40):
        t = time.perf_counter()
        tenancy.verify_key(tmp_path, k.secret)  # cold cache each time: store read + argon2 verify
        verify_ms.append((time.perf_counter() - t) * 1000)
        tenancy.reset_backend_for_tests()
    cached = []
    tenancy.verify_key(tmp_path, k.secret)
    for _ in range(40):
        t = time.perf_counter()
        tenancy.verify_key(tmp_path, k.secret)
        cached.append((time.perf_counter() - t) * 1000)
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(len(xs) * p))]  # noqa: E731
    out = {
        "get_key_ms": {"p50": q(get_ms, 0.5), "p95": q(get_ms, 0.95)},
        "record_usage_ms": {"p50": q(use_ms, 0.5), "p95": q(use_ms, 0.95)},
        "verify_key_cold_cache_ms": {"p50": q(verify_ms, 0.5), "p95": q(verify_ms, 0.95), "mean": statistics.mean(verify_ms)},
        "verify_key_cached_ms": {"p50": q(cached, 0.5), "p95": q(cached, 0.95)},
        "note": "loopback to a local container, no TLS: store round trip only; add the pooler round trip for the hosted figure",
    }
    if os.environ.get("PRAVRUDHI_TENANCY_LATENCY_LOG"):
        Path(os.environ["PRAVRUDHI_TENANCY_LATENCY_LOG"]).write_text(json.dumps(out, indent=1))
    assert out["get_key_ms"]["p95"] < 100
