"""The Supabase-backed store for partner orgs, memberships, API key hashes, per-key usage and the partner audit log (#920).

Reached ONLY through the SECURITY DEFINER functions of the `tenancy` schema as the narrow login role `tenancy_rpc`
(migration: tenancy_migration_v1). The plaintext key secret never reaches this module's callers' database: only the argon2id hash
of its secret half is stored, and the argon2 verify stays in the engine. Every failure of the store raises `TenancyStoreDown`,
which the tenancy module turns into a 503: a store that cannot answer is never read as "unknown key" and never as "valid key".

One persistent connection (a lock, a reconnect after any error) instead of the assist store's connection per call: a keyed request
should not pay a TLS handshake to the pooler. The TLS rules are the assist store's: always verifying, session pooler or direct.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Callable, Mapping
from typing import Any

from pravrudhi.application.a0_assist.store import StoreNotConfigured, parse_dsn, tls_context

DSN_ENV = "SUPABASE_TENANCY_DB_URL"
DEFAULT_CA_FILE = "/opt/pravrudhi/certs/supabase-prod-ca.crt"


class TenancyStoreDown(Exception):
    """The tenancy store did not answer (or answered something malformed). The message names only a class, never a value."""


class PgTenancyStore:
    def __init__(
        self,
        dsn: str,
        *,
        timeout_s: float = 3.0,
        connect: Callable[..., Any] | None = None,
        ca_file: str | None = None,
    ) -> None:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < float(timeout_s) <= 30:
            raise StoreNotConfigured("timeout_s must be in (0, 30]")
        self._p = parse_dsn(dsn)
        if ca_file is not None:
            self._p["sslrootcert"] = ca_file
        self._timeout = float(timeout_s)
        self._connect_fn = connect
        self._lock = threading.Lock()
        self._conn: Any = None

    def __repr__(self) -> str:
        return "PgTenancyStore(<redacted>)"

    def _open(self) -> Any:
        connect = self._connect_fn
        if connect is None:
            try:
                import pg8000.dbapi as driver
            except ImportError:
                raise StoreNotConfigured("the pg8000 driver is not installed (pip install pravrudhi[assist])") from None
            connect = driver.connect
        p = self._p
        conn = connect(
            user=p["user"], password=p["password"], host=p["host"], port=p["port"], database=p["database"],
            ssl_context=tls_context(p["sslrootcert"]), timeout=self._timeout, application_name="pravrudhi-tenancy",
        )
        conn.autocommit = True
        return conn

    def _call(self, fn: str, *args: Any) -> Any:
        """`select tenancy.<fn>(...)` -> the parsed JSON value. One reconnect per call; any failure is TenancyStoreDown."""
        marks = ", ".join(["%s"] * len(args))
        sql = f"select tenancy.{fn}({marks})"  # fn is a fixed name from this module, never caller text
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        self._conn = self._open()
                    cur = self._conn.cursor()
                    try:
                        cur.execute(sql, args)
                        rows = cur.fetchall()
                    finally:
                        with contextlib.suppress(Exception):
                            cur.close()
                    v = rows[0][0]
                    return json.loads(v) if isinstance(v, (str, bytes)) else v
                except StoreNotConfigured:
                    raise
                except Exception as exc:  # noqa: BLE001 - driver, ssl and socket layers raise unrelated types; all fail closed
                    with contextlib.suppress(Exception):
                        if self._conn is not None:
                            self._conn.close()
                    self._conn = None
                    if attempt == 2:
                        raise TenancyStoreDown(f"the tenancy store call failed ({type(exc).__name__})") from None
        raise TenancyStoreDown("unreachable")  # pragma: no cover

    # ---- orgs and memberships -------------------------------------------------------------------------------------------
    def create_org(self, org_id: str, name: str, created: str) -> dict[str, Any]:
        return _ok_dict(self._call("create_org", org_id, name, created))

    def get_org(self, org_id: str) -> dict[str, Any] | None:
        v = self._call("get_org", org_id)
        return dict(v) if isinstance(v, Mapping) else None

    def list_orgs(self) -> list[dict[str, Any]]:
        return _list(self._call("list_orgs"))

    def add_member(self, org_id: str, user_id: str, role: str) -> dict[str, Any]:
        return _ok_dict(self._call("add_member", org_id, user_id, role))

    def memberships_for_org(self, org_id: str) -> list[dict[str, Any]]:
        return _list(self._call("memberships_for_org", org_id))

    # ---- keys -----------------------------------------------------------------------------------------------------------
    def create_key(self, row: Mapping[str, Any]) -> dict[str, Any]:
        args = (row["key_id"], row["org_id"], row["label"], row["hash"], row["created"], row["rate_limit_per_minute"])
        return _ok_dict(self._call("create_key", *args))

    def get_key(self, key_id: str) -> dict[str, Any] | None:
        v = self._call("get_key", key_id)
        return dict(v) if isinstance(v, Mapping) else None


    def revoke_key(self, key_id: str, at: str) -> dict[str, Any]:
        return _ok_dict(self._call("revoke_key", key_id, at))

    def keys_for_org(self, org_id: str) -> list[dict[str, Any]]:
        return _list(self._call("keys_for_org", org_id))

    def purge_key(self, key_id: str) -> dict[str, Any]:
        return _ok_dict(self._call("purge_key", key_id))

    # ---- usage and audit ------------------------------------------------------------------------------------------------
    def record_usage(self, key_id: str, day: str, failed: bool) -> None:
        _ok_dict(self._call("record_usage", key_id, day, failed))

    def usage_counts(self, key_id: str) -> tuple[int, int]:
        v = self._call("usage_counts", key_id)
        if not isinstance(v, Mapping):
            raise TenancyStoreDown("the tenancy store answered with an unexpected shape")
        return int(v.get("calls", 0)), int(v.get("failed", 0))

    def usage_summary(self, org_id: str, first_day: str) -> list[dict[str, Any]]:
        return _list(self._call("usage_summary", org_id, first_day))

    def audit_record(self, row: Mapping[str, Any], cutoff: str) -> None:
        args = (row["ts"], row["key_id"], row["mode"], row["status_code"], row["run_id"], list(row["contract_ids"]))
        _ok_dict(self._call("audit_record", *args, json.dumps(row["outcomes"]), cutoff))

    def audit_page(self, key_id: str, offset: int, limit: int, cutoff: str) -> tuple[list[dict[str, Any]], int]:
        v = self._call("audit_page", key_id, offset, limit, cutoff)
        if not isinstance(v, Mapping) or not isinstance(v.get("rows"), list):
            raise TenancyStoreDown("the tenancy store answered with an unexpected shape")
        return [dict(r) for r in v["rows"]], int(v.get("total", 0))

    def canary(self) -> dict[str, Any]:
        v = self._call("canary")
        if not isinstance(v, Mapping) or "ok" not in v:
            raise TenancyStoreDown("the tenancy store answered with an unexpected shape")
        return dict(v)

    def close(self) -> None:
        with self._lock, contextlib.suppress(Exception):
            if self._conn is not None:
                self._conn.close()
            self._conn = None


def _ok_dict(v: Any) -> dict[str, Any]:
    if not isinstance(v, Mapping) or "ok" not in v:
        raise TenancyStoreDown("the tenancy store answered with an unexpected shape")
    return dict(v)


def _list(v: Any) -> list[dict[str, Any]]:
    if not isinstance(v, list):
        raise TenancyStoreDown("the tenancy store answered with an unexpected shape")
    return [dict(x) for x in v]


def from_env(env: Mapping[str, str], *, connect: Callable[..., Any] | None = None) -> PgTenancyStore | None:
    """The store, or None when the DSN is unset or refused (the caller decides: in the hosted shape None is a 503)."""
    dsn = env.get(DSN_ENV, "").strip()
    if not dsn:
        return None
    from pathlib import Path

    ca = env.get("PRAVRUDHI_TENANCY_CA_FILE", DEFAULT_CA_FILE)
    try:
        return PgTenancyStore(dsn, connect=connect, ca_file=ca if Path(ca).is_file() else None)
    except StoreNotConfigured:
        return None
