"""#920: the Supabase tenancy backend, with an in-memory store standing in for the database (the real SQL is covered by the
env-gated integration test test_tenancy_pg_it.py). Both arms of every rule: file backend unchanged, supabase backend working,
store down = 503 (never an unknown key, never accepted, never a silent fall back to the volatile files)."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from pravrudhi.application import audit, tenancy
from pravrudhi.application.tenancy_store import TenancyStoreDown

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


class FakeStore:
    """The PgTenancyStore interface in memory; `down` makes every call raise TenancyStoreDown."""

    def __init__(self) -> None:
        self.down = False
        self.orgs: dict[str, dict[str, Any]] = {}
        self.members: dict[tuple[str, str], str] = {}
        self.keys: dict[str, dict[str, Any]] = {}
        self.usage: dict[str, list[int]] = {}
        self.daily: dict[tuple[str, str], list[int]] = {}
        self.audit_rows: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def _c(self, name: str) -> None:
        self.calls.append(name)
        if self.down:
            raise TenancyStoreDown("down")

    def create_org(self, org_id: str, name: str, created: str) -> dict[str, Any]:
        self._c("create_org")
        if org_id in self.orgs:
            return {"ok": False, "reason": "exists"}
        self.orgs[org_id] = {"id": org_id, "name": name, "created": created}
        return {"ok": True, "org": self.orgs[org_id]}

    def get_org(self, org_id: str) -> dict[str, Any] | None:
        self._c("get_org")
        return self.orgs.get(org_id)

    def list_orgs(self) -> list[dict[str, Any]]:
        self._c("list_orgs")
        return sorted(self.orgs.values(), key=lambda r: r["id"])

    def add_member(self, org_id: str, user_id: str, role: str) -> dict[str, Any]:
        self._c("add_member")
        if org_id not in self.orgs:
            return {"ok": False, "reason": "no_org"}
        self.members[(org_id, user_id)] = role
        return {"ok": True}

    def memberships_for_org(self, org_id: str) -> list[dict[str, Any]]:
        self._c("memberships_for_org")
        return [{"org_id": o, "user_id": u, "role": r} for (o, u), r in sorted(self.members.items()) if o == org_id]

    def create_key(self, row: Any) -> dict[str, Any]:
        self._c("create_key")
        self.keys[row["key_id"]] = dict(row)
        return {"ok": True}

    def get_key(self, key_id: str) -> dict[str, Any] | None:
        self._c("get_key")
        return dict(self.keys[key_id]) if key_id in self.keys else None

    def revoke_key(self, key_id: str, at: str) -> dict[str, Any]:
        self._c("revoke_key")
        if key_id not in self.keys:
            return {"ok": False, "reason": "no_key"}
        self.keys[key_id].update(revoked=True, revoked_at=at)
        return {"ok": True, "key": dict(self.keys[key_id])}

    def keys_for_org(self, org_id: str) -> list[dict[str, Any]]:
        self._c("keys_for_org")
        return [dict(k) for k in self.keys.values() if k["org_id"] == org_id]

    def record_usage(self, key_id: str, day: str, failed: bool) -> None:
        self._c("record_usage")
        t = self.usage.setdefault(key_id, [0, 0])
        d = self.daily.setdefault((key_id, day), [0, 0])
        t[1 if failed else 0] += 1
        d[1 if failed else 0] += 1

    def usage_counts(self, key_id: str) -> tuple[int, int]:
        self._c("usage_counts")
        c, f = self.usage.get(key_id, [0, 0])
        return c, f

    def usage_summary(self, org_id: str, first_day: str) -> list[dict[str, Any]]:
        self._c("usage_summary")
        return [
            {
                "key_id": k["key_id"],
                "label": k["label"],
                "revoked": k["revoked"],
                "days": [
                    {"day": d, "calls": v[0], "failed": v[1]}
                    for (kid, d), v in sorted(self.daily.items())
                    if kid == k["key_id"] and d >= first_day
                ],
            }
            for k in self.keys.values()
            if k["org_id"] == org_id
        ]

    def audit_record(self, row: Any, retention_s: int) -> None:
        self._c("audit_record")
        self.audit_rows.append(dict(row))

    def audit_page(self, key_id: str, offset: int, limit: int, retention_s: int) -> tuple[list[dict[str, Any]], int]:
        self._c("audit_page")
        mine = [r for r in reversed(self.audit_rows) if r["key_id"] == key_id]
        return mine[offset : offset + limit], len(mine)


@pytest.fixture
def sb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[FakeStore, Path]:
    store = FakeStore()
    monkeypatch.setenv(tenancy.BACKEND_ENV, "supabase")
    monkeypatch.setenv(tenancy.KEY_CACHE_ENV, "15")
    monkeypatch.setattr(tenancy, "_store_from_env", lambda env: store)
    tenancy.reset_backend_for_tests()
    yield store, tmp_path
    tenancy.reset_backend_for_tests()


def _files(root: Path) -> list[Path]:
    d = tenancy.tenancy_dir(root)
    return list(d.glob("*.json")) if d.exists() else []


def test_a_key_made_in_the_supabase_backend_verifies_and_the_secret_never_reaches_the_store(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    created = tenancy.create_key(root, "acme", label="t")
    rec = tenancy.verify_key(root, created.secret)
    assert rec.key_id == created.record.key_id and rec.org_id == "acme"
    secret_part = created.secret.split("_", 2)[2]
    assert secret_part not in repr(store.keys) and created.secret not in repr(store.keys)  # only the argon2id hash is stored
    assert store.keys[rec.key_id]["hash"].startswith("$argon2id$")
    assert _files(root) == []  # nothing written to the volatile files


def test_an_unknown_wrong_secret_and_revoked_key_are_each_invalid_with_the_store_up(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    prefix = f"{tenancy.KEY_PREFIX}_{k.record.key_id}_"
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, "pnk_unknownkeyid0000_xxxxxxxx")
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, prefix + "wrong")
    tenancy.revoke_key(root, k.record.key_id)
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, k.secret)


def test_revoking_in_this_process_stops_the_key_at_once_despite_the_cache(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.verify_key(root, k.secret)  # warms the cache
    tenancy.revoke_key(root, k.record.key_id)
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, k.secret)


def test_the_cache_serves_within_the_ttl_and_asks_the_store_again_after_it(sb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.verify_key(root, k.secret)
    tenancy.verify_key(root, k.secret)
    assert store.calls.count("get_key") == 1  # second call from the cache
    real = time.monotonic
    monkeypatch.setattr(tenancy.time, "monotonic", lambda: real() + 16)
    tenancy.verify_key(root, k.secret)
    assert store.calls.count("get_key") == 2


def test_a_cache_ttl_of_zero_asks_the_store_every_time(sb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    store, root = sb
    monkeypatch.setenv(tenancy.KEY_CACHE_ENV, "0")
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.verify_key(root, k.secret)
    tenancy.verify_key(root, k.secret)
    assert store.calls.count("get_key") == 2


def test_store_down_with_a_cold_cache_is_503_not_an_unknown_key(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.reset_backend_for_tests()  # cold cache
    store.down = True
    with pytest.raises(tenancy.TenancyUnavailable) as e:
        tenancy.verify_key(root, k.secret)
    assert e.value.status_code == 503 and e.value.detail == "key service unavailable"
    with pytest.raises(
        tenancy.TenancyUnavailable
    ):  # an unknown key is NOT accepted (nor called unknown) because the store is down
        tenancy.verify_key(root, "pnk_unknownkeyid0000_xxxxxxxx")


def test_store_down_inside_the_ttl_still_serves_a_cached_valid_key_but_not_after_it(
    sb: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.verify_key(root, k.secret)
    store.down = True
    assert tenancy.verify_key(root, k.secret).org_id == "acme"  # inside the TTL
    real = time.monotonic
    monkeypatch.setattr(tenancy.time, "monotonic", lambda: real() + 16)
    with pytest.raises(tenancy.TenancyUnavailable):  # no stale-while-error beyond the TTL
        tenancy.verify_key(root, k.secret)


def test_admin_operations_are_503_when_the_store_is_down(sb: Any) -> None:
    store, root = sb
    store.down = True
    for call in (
        lambda: tenancy.create_org(root, "acme", "Acme"),
        lambda: tenancy.get_org(root, "acme"),
        lambda: tenancy.list_orgs(root),
        lambda: tenancy.keys_for_org(root, "acme"),
        lambda: tenancy.revoke_key(root, "abc"),
        lambda: tenancy.usage_counts(root, "abc"),
        lambda: tenancy.usage_summary(root, "acme", now=NOW, days=7),
    ):
        with pytest.raises(tenancy.TenancyUnavailable):
            call()
    assert _files(root) == []


def test_the_hosted_backend_without_a_store_refuses_and_never_falls_back_to_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tenancy.BACKEND_ENV, "supabase")
    monkeypatch.delenv("SUPABASE_TENANCY_DB_URL", raising=False)
    tenancy.reset_backend_for_tests()
    with pytest.raises(tenancy.TenancyUnavailable):
        tenancy.create_org(tmp_path, "acme", "Acme")
    with pytest.raises(tenancy.TenancyUnavailable):
        tenancy.verify_key(tmp_path, "pnk_abcdefghijklmnop_secret")
    assert _files(tmp_path) == []


def test_the_file_backend_is_unchanged_when_the_backend_is_not_supabase(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(tenancy.BACKEND_ENV, raising=False)
    tenancy.reset_backend_for_tests()
    tenancy.create_org(tmp_path, "acme", "Acme")
    k = tenancy.create_key(tmp_path, "acme")
    assert tenancy.verify_key(tmp_path, k.secret).org_id == "acme"
    assert _files(tmp_path)  # the files are where they always were


def test_usage_is_written_off_the_request_path_and_counted(sb: Any) -> None:
    store, root = sb
    tenancy.record_usage(root, "k1", now=NOW)
    tenancy.record_usage(root, "k1", failed=True, now=NOW)
    assert tenancy.flush_usage()
    assert store.usage["k1"] == [1, 1] and store.daily[("k1", "2026-10-14")] == [1, 1]
    assert tenancy.usage_counts(root, "k1") == (1, 1)


def test_a_failing_store_drops_usage_rows_and_counts_them_without_raising(sb: Any) -> None:
    store, root = sb
    store.down = True
    before = tenancy.usage_queue_stats()["dropped"]
    tenancy.record_usage(root, "k1", now=NOW)
    assert tenancy.flush_usage()
    assert tenancy.usage_queue_stats()["dropped"] == before + 1


def test_the_usage_queue_is_bounded_and_overflow_is_counted_not_raised(sb: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    store, root = sb
    gate = threading.Event()
    started = threading.Event()
    real = store.record_usage

    def blocking(key_id: str, day: str, failed: bool) -> None:
        started.set()
        gate.wait(5)
        real(key_id, day, failed)

    store.record_usage = blocking  # type: ignore[method-assign]
    monkeypatch.setattr(tenancy, "_USAGE_MAX", 3)
    before = tenancy.usage_queue_stats()["dropped"]
    tenancy.record_usage(root, "k0", now=NOW)
    assert started.wait(5)  # the worker holds one item; the queue itself is now empty
    for i in range(5):
        tenancy.record_usage(root, f"k{i + 1}", now=NOW)  # 3 fit, 2 overflow
    assert tenancy.usage_queue_stats()["dropped"] == before + 2
    gate.set()
    assert tenancy.flush_usage()
    assert len(store.usage) == 4  # k0 + the three that fit


def test_a_key_unknown_a_moment_ago_is_not_remembered_as_unknown(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.reset_backend_for_tests()
    row = store.keys.pop(k.record.key_id)  # as if another replica has not written it yet
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, k.secret)
    store.keys[k.record.key_id] = row
    assert tenancy.verify_key(root, k.secret).org_id == "acme"  # only an existing row is cached, never an unknown one


def test_audit_rows_go_to_the_store_and_page_back_newest_first(sb: Any) -> None:
    store, root = sb
    for i in range(3):
        audit.record(
            root,
            key_id="k1",
            mode="sync",
            status_code=200,
            contract_ids=["bns69"],
            run_id=f"r{i}",
            retention_s=1000.0,
            now=NOW + timedelta(seconds=i),
        )
    assert tenancy.flush_usage()
    rows, nxt = audit.page(root, "k1", offset=0, limit=2, retention_s=1000.0, now=lambda: NOW + timedelta(seconds=10))
    assert [r["run_id"] for r in rows] == ["r2", "r1"] and nxt == 2
    rows2, nxt2 = audit.page(root, "k1", offset=2, limit=2, retention_s=1000.0, now=lambda: NOW + timedelta(seconds=10))
    assert [r["run_id"] for r in rows2] == ["r0"] and nxt2 is None
    assert not (root / ".pravrudhi" / "audit").exists()


def test_audit_reads_are_503_when_the_store_is_down_and_a_write_failure_never_raises(sb: Any) -> None:
    store, root = sb
    store.down = True
    audit.record(
        root, key_id="k1", mode="sync", status_code=200, contract_ids=[], retention_s=1000.0, now=NOW
    )  # off-path: no raise
    assert tenancy.flush_usage()
    with pytest.raises(tenancy.TenancyUnavailable):
        audit.page(root, "k1", offset=0, limit=10, retention_s=1000.0, now=lambda: NOW)


# ---- the health label -------------------------------------------------------------------------------------------------------
def test_health_says_file_for_the_local_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tenancy.BACKEND_ENV, raising=False)
    tenancy.reset_backend_for_tests()
    assert tenancy.store_status() == "file"


def _settle() -> str:
    for _ in range(100):
        v = tenancy.store_status()
        if v != "checking":
            return v
        time.sleep(0.02)
    return "checking"


def test_health_reports_ok_when_the_store_answers_and_unavailable_when_it_does_not(sb: Any) -> None:
    store, _root = sb
    store.canary = lambda: {"ok": True}  # type: ignore[attr-defined]
    assert tenancy.store_status() == "checking"  # first call never blocks on the store
    assert _settle() == "ok"
    tenancy.reset_backend_for_tests()
    store.canary = lambda: (_ for _ in ()).throw(TenancyStoreDown("down"))  # type: ignore[attr-defined]
    assert _settle() == "unavailable"


def test_health_is_unavailable_when_the_hosted_backend_has_no_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tenancy.BACKEND_ENV, "supabase")
    monkeypatch.delenv("SUPABASE_TENANCY_DB_URL", raising=False)
    tenancy.reset_backend_for_tests()
    assert _settle() == "unavailable"


def test_the_health_endpoint_carries_only_the_label(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from pravrudhi.api.server import create_app

    monkeypatch.delenv(tenancy.BACKEND_ENV, raising=False)
    tenancy.reset_backend_for_tests()
    body = TestClient(create_app(tmp_path), headers={"host": "127.0.0.1:8008"}).get("/api/health").json()
    assert body["tenancy_store"] == "file" and "SUPABASE" not in str(body) and "postgres" not in str(body)


# ---- pre-check fixes ---------------------------------------------------------------------------------------------------------
class _Conn:
    """A DB-API connection double: `script` is a list of per-execute outcomes (a value, or an exception to raise)."""

    def __init__(self, owner: _Driver) -> None:
        self.owner = owner

    def cursor(self) -> Any:
        return self

    def execute(self, sql: str, params: Any = None) -> None:
        self.owner.executes.append(sql)
        out = self.owner.script[min(len(self.owner.executes) - 1, len(self.owner.script) - 1)]
        if isinstance(out, BaseException):
            raise out
        self._row = out

    def fetchall(self) -> Any:
        return [(self._row,)]

    def close(self) -> None:
        self.owner.closed += 1

    autocommit = True


class _Driver:
    def __init__(self, script: list[Any]) -> None:
        self.script, self.executes, self.connects, self.closed = script, [], 0, 0

    def __call__(self, **kw: Any) -> _Conn:
        self.connects += 1
        return _Conn(self)


DSN = "postgresql://tenancy_rpc:pw@127.0.0.1:5432/postgres"


def _pgstore(script: list[Any], **kw: Any) -> tuple[Any, _Driver]:
    from pravrudhi.application.tenancy_store import PgTenancyStore

    d = _Driver(script)
    return PgTenancyStore(DSN, connect=d, **kw), d


def test_only_a_json_null_is_no_such_key_anything_else_is_a_broken_store() -> None:
    s, _ = _pgstore([None])
    assert s.get_key("abcdefgh12345678") is None and s.get_org("acme") is None
    for junk in ("oops", [], True, 7):
        s2, _ = _pgstore([junk])
        with pytest.raises(TenancyStoreDown):
            s2.get_key("abcdefgh12345678")
        with pytest.raises(TenancyStoreDown):
            s2.get_org("acme")


def test_a_read_is_retried_once_but_a_write_is_not_run_twice() -> None:
    boom = OSError("connection reset")
    s, d = _pgstore([boom, {"ok": True, "org": {"id": "a", "name": "A", "created": "t"}}])
    assert s.create_org  # noqa: B018
    with pytest.raises(TenancyStoreDown):
        s.create_org("a", "A", "t")  # not idempotent: one attempt, then the failure is reported
    assert len(d.executes) == 1
    r, d2 = _pgstore([boom, None])
    assert r.get_key("abcdefgh12345678") is None and len(d2.executes) == 2  # an idempotent read is retried on a fresh connection
    w, d3 = _pgstore([boom, {"ok": True}])
    with pytest.raises(TenancyStoreDown):
        w.record_usage("k", "2026-10-14", False)
    assert len(d3.executes) == 1


def test_a_missing_driver_or_config_error_is_a_store_failure_not_a_raw_exception() -> None:
    from pravrudhi.application.a0_assist.store import StoreNotConfigured

    s, _ = _pgstore([None])
    s._connect_fn = lambda **kw: (_ for _ in ()).throw(StoreNotConfigured("no driver"))  # type: ignore[assignment]
    with pytest.raises(TenancyStoreDown):
        s.get_key("abcdefgh12345678")


def test_an_idle_connection_is_reopened_before_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from pravrudhi.application import tenancy_store as TS

    s, d = _pgstore([None])
    s.get_key("abcdefgh12345678")
    real = time.monotonic
    monkeypatch.setattr(TS.time, "monotonic", lambda: real() + TS.IDLE_REOPEN_S + 1)
    s.get_key("abcdefgh12345678")
    assert d.connects == 2 and d.closed >= 1


def test_a_busy_connection_is_a_store_failure_not_an_unbounded_wait() -> None:
    import threading

    s, _ = _pgstore([None], timeout_s=0.05)
    s._lock.acquire()
    result: list[Any] = []
    t = threading.Thread(target=lambda: result.append(_try(s)))
    t.start()
    t.join(3)
    s._lock.release()
    assert result == ["down"]


def _try(s: Any) -> str:
    try:
        s.get_key("abcdefgh12345678")
        return "ok"
    except TenancyStoreDown:
        return "down"


def test_a_missing_pinned_ca_file_is_not_configured_not_a_silent_downgrade(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from pravrudhi.application import tenancy_store as TS

    env = {TS.DSN_ENV: DSN, "PRAVRUDHI_TENANCY_CA_FILE": str(tmp_path / "missing.crt")}
    assert TS.from_env(env) is None
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    assert TS.from_env({**env, "PRAVRUDHI_TENANCY_CA_FILE": str(ca)}) is not None


def test_a_mistyped_backend_value_fails_closed_and_never_selects_the_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for typo in ("supabse", "postgres", "files", "SUPABASE_"):
        monkeypatch.setenv(tenancy.BACKEND_ENV, typo)
        with pytest.raises(tenancy.TenancyUnavailable):
            tenancy.create_org(tmp_path, "acme", "Acme")
        with pytest.raises(tenancy.TenancyUnavailable):
            tenancy.verify_key(tmp_path, "pnk_abcdefghijklmnop_secret")
    assert _files(tmp_path) == []
    monkeypatch.setenv(tenancy.BACKEND_ENV, " File ")  # case and spaces are tolerated for the two real values
    tenancy.create_org(tmp_path, "acme", "Acme")


def test_a_revoke_that_loses_its_reply_still_drops_the_cached_key(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.verify_key(root, k.secret)  # cached
    real = store.revoke_key

    def lost(key_id: str, at: str) -> dict[str, Any]:
        real(key_id, at)  # the commit happened
        raise TenancyStoreDown("reply lost")

    store.revoke_key = lost  # type: ignore[method-assign]
    with pytest.raises(tenancy.TenancyUnavailable):
        tenancy.revoke_key(root, k.record.key_id)
    store.revoke_key = real  # type: ignore[method-assign]
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, k.secret)  # re-read from the store: revoked


def test_a_row_read_before_a_concurrent_revoke_is_not_cached_after_it(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.reset_backend_for_tests()
    real = store.get_key

    def racy(key_id: str) -> Any:
        row = real(key_id)
        tenancy._forget_key(key_id)  # a revoke lands between the read and the cache write
        return row

    store.get_key = racy  # type: ignore[method-assign]
    tenancy.verify_key(root, k.secret)
    store.get_key = real  # type: ignore[method-assign]
    store.calls.clear()
    tenancy.verify_key(root, k.secret)
    assert store.calls.count("get_key") == 1  # nothing was cached by the racy read


def test_the_cached_row_cannot_be_corrupted_through_a_returned_copy(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.verify_key(root, k.secret)
    row = tenancy._key_row(root, k.record.key_id)
    assert row is not None
    row.pop("hash")
    assert "hash" in (tenancy._key_row(root, k.record.key_id) or {})


def test_listing_and_revoke_never_carry_a_hash_but_verify_still_works(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    assert all(r.hash == "" for r in tenancy.keys_for_org(root, "acme"))
    assert tenancy.revoke_key(root, k.record.key_id).hash == ""


def test_a_purge_forgets_the_key_at_once(sb: Any) -> None:
    store, root = sb
    store.purge_key = lambda key_id: store.keys.pop(key_id, None) and {"ok": True, "removed": True}  # type: ignore[attr-defined]
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.verify_key(root, k.secret)
    assert tenancy.purge_key(root, k.record.key_id) is True
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, k.secret)


def test_an_audit_write_never_raises_even_when_the_backend_is_misconfigured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tenancy.BACKEND_ENV, "supabase")
    monkeypatch.delenv("SUPABASE_TENANCY_DB_URL", raising=False)
    tenancy.reset_backend_for_tests()
    audit.record(
        tmp_path, key_id="k1", mode="sync", status_code=200, contract_ids=[], retention_s=1000.0, now=NOW
    )  # no exception
    with pytest.raises(tenancy.TenancyUnavailable):
        audit.page(tmp_path, "k1", offset=0, limit=10, retention_s=1000.0, now=lambda: NOW)


def test_audit_paging_clamps_a_negative_offset_and_the_page_size(sb: Any) -> None:
    store, root = sb
    for i in range(3):
        audit.record(
            root, key_id="k1", mode="sync", status_code=200, contract_ids=[], run_id=f"r{i}", retention_s=1000.0, now=NOW
        )
    assert tenancy.flush_usage()
    rows, nxt = audit.page(root, "k1", offset=-5, limit=1000, retention_s=1000.0, now=lambda: NOW)
    assert len(rows) == 3 and nxt is None


def test_a_missing_store_is_not_remembered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = FakeStore()
    answers: list[Any] = [None, store]
    monkeypatch.setenv(tenancy.BACKEND_ENV, "supabase")
    monkeypatch.setattr(tenancy, "_store_from_env", lambda env: answers.pop(0) if len(answers) > 1 else answers[0])
    tenancy.reset_backend_for_tests()
    with pytest.raises(tenancy.TenancyUnavailable):
        tenancy.create_org(tmp_path, "acme", "Acme")
    tenancy.create_org(tmp_path, "acme", "Acme")  # the DSN or CA file appeared: picked up at once, no restart
    assert "acme" in store.orgs


def test_a_row_the_database_refuses_counts_as_dropped_not_written(sb: Any) -> None:
    store, root = sb
    store.audit_record = lambda row, retention_s: {"ok": False, "reason": "bad_args"}  # type: ignore[method-assign]
    before = tenancy.usage_queue_stats()
    audit.record(root, key_id="k1", mode="sync", status_code=200, contract_ids=[], retention_s=1000.0, now=NOW)
    assert tenancy.flush_usage()
    after = tenancy.usage_queue_stats()
    assert after["dropped"] == before["dropped"] + 1 and after["written"] == before["written"]


def test_record_usage_never_raises_into_the_caller_even_with_a_mistyped_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(tenancy.BACKEND_ENV, "supabse")
    tenancy.record_usage(tmp_path, "k1", now=NOW)  # no exception


def test_the_writer_connection_is_released_when_the_queue_is_empty(sb: Any) -> None:
    store, root = sb
    closed: list[int] = []
    store.close = lambda: closed.append(1)  # type: ignore[attr-defined]
    tenancy.record_usage(root, "k1", now=NOW)
    assert tenancy.flush_usage()
    time.sleep(0.2)
    assert closed


def test_a_revoke_answer_without_the_key_row_is_a_tenancy_error_not_a_crash(sb: Any) -> None:
    store, root = sb
    store.revoke_key = lambda key_id, at: {"ok": True, "key": None}  # type: ignore[method-assign]
    with pytest.raises(tenancy.TenancyError):
        tenancy.revoke_key(root, "abcdefgh12345678")


def test_a_malformed_count_from_the_store_is_a_store_failure() -> None:
    s, _ = _pgstore([{"calls": "many", "failed": 0}])
    with pytest.raises(TenancyStoreDown):
        s.usage_counts("k1")
    s2, _ = _pgstore([{"rows": [], "total": "x"}])
    with pytest.raises(TenancyStoreDown):
        s2.audit_page("k1", 0, 10, 3600)
