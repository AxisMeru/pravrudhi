"""The spend and session store: Supabase Postgres, reached DIRECTLY as the narrow login role
`assist_spend_rpc` (no PostgREST, no service key).

* `pg8000` is an OPTIONAL extra (`pip install pravrudhi[assist]`) imported LAZILY inside `PgSpendStore._connect`; if it
  is missing, or the DSN environment variable is unset, `from_env` returns
  (None, "not_configured") and screening is untouched.
* The DSN must point at a direct connection or the session pooler (port 5432), NEVER the transaction pooler (6543: no
  session state, and prepared statements are not safe there); TLS is ALWAYS on and the server
  certificate is ALWAYS verified (the system CA bundle, or the file named by an optional `sslrootcert` query parameter);
  `sslmode` values that weaken that (disable, allow, prefer) are refused.
* Every call opens its own short-lived connection (bounded connect and statement time), runs ONE `select
  assist.<fn>(...)` with bound parameters (no string building), and closes it. No named prepared statement is created.
* Fail closed: any connection, timeout, driver or malformed-answer problem is a `StoreError` (the tier pauses, no
  session starts). The DSN, the password and the driver's own messages never appear in an error, a repr or a log.
* Only ids, integer cents and timestamps go in; the keys are engine-generated uuid4 strings (the database CHECKs them again)."""

from __future__ import annotations

import contextlib
import json
import ssl
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlsplit

TRANSACTION_POOLER_PORT = 6543
WEAK_SSLMODES = frozenset({"disable", "allow", "prefer"})


class StoreError(RuntimeError):
    """The store cannot be used right now: fail closed (no session starts)."""


class StoreNotConfigured(StoreError):
    """No DSN, no driver, or a DSN that is refused: the assisted route is not configured."""


class SpendStore(Protocol):
    def reserve(self, key: str, max_cents: int, ceiling_cents: int) -> dict[str, Any]: ...
    def finish(self, key: str, list_cost_cents: int | None) -> dict[str, Any]: ...
    def abandon_stale(self, minutes: int) -> dict[str, Any]: ...
    def sweep_ok(self) -> dict[str, Any]: ...
    def status(self) -> dict[str, Any]: ...
    def session_created(self, anthropic_session_id: str, key: str) -> dict[str, Any]: ...
    def session_deleted(self, anthropic_session_id: str, http_status: int) -> dict[str, Any]: ...
    def list_pending(self, older_than_minutes: int) -> list[dict[str, Any]]: ...
    def canary(self) -> dict[str, Any]: ...
    def mark_deleted(self, anthropic_session_id: str, http_status: int) -> dict[str, Any]: ...
    def prune(self) -> int: ...


def new_session_key() -> str:
    """The engine-generated reservation key (uuid4, lower case): never the client's session_id, never Anthropic's id."""
    return str(uuid.uuid4())


def parse_dsn(dsn: str) -> dict[str, Any]:
    """postgresql://user:password@host[:port]/database[?sslmode=require|verify-ca|verify-full&sslrootcert=/path].
    Refuses anything weaker or unsafe; the error text never contains the DSN."""
    try:
        u = urlsplit(dsn)
        port = u.port or 5432
    except ValueError:
        raise StoreNotConfigured("the store DSN is not a valid URL") from None
    if u.scheme not in ("postgresql", "postgres"):
        raise StoreNotConfigured("the store DSN must be a postgresql:// URL")
    if not u.hostname or not u.username or not u.password or not u.path.strip("/"):
        raise StoreNotConfigured("the store DSN needs a host, user, password and database")
    if port == TRANSACTION_POOLER_PORT:
        raise StoreNotConfigured(
            "the store DSN points at the transaction pooler (6543): use a direct connection or the session pooler (5432)"
        )
    q = {k: v[-1] for k, v in parse_qs(u.query).items()}
    mode = q.get("sslmode", "verify-full")
    if mode in WEAK_SSLMODES:
        raise StoreNotConfigured("TLS with certificate verification is required: this sslmode is refused")
    if mode not in ("require", "verify-ca", "verify-full"):
        raise StoreNotConfigured("unknown sslmode")
    root = q.get("sslrootcert")
    if root is not None and not Path(root).is_file():
        raise StoreNotConfigured("sslrootcert does not name a readable file")
    return {
        "user": unquote(u.username),
        "password": unquote(u.password),
        "host": u.hostname,
        "port": port,
        "database": u.path.strip("/"),
        "sslrootcert": root,
    }


def tls_context(sslrootcert: str | None) -> ssl.SSLContext:
    """ALWAYS verifying (certificate and host name): the system CA bundle, or the single file named by sslrootcert.

    With a CA file, exactly one flag is cleared: VERIFY_X509_STRICT. The database provider's published root CA carries no
    keyUsage extension, which Python 3.13's strict default refuses; chain building, expiry, host name checking and
    CERT_REQUIRED all stay on. Nothing else is relaxed. This is the one builder (the engine store and the sweeper use it)."""
    ctx = ssl.create_default_context(cafile=sslrootcert)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if sslrootcert is not None:
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return ctx


class PgSpendStore:
    def __init__(
        self, dsn: str, *, timeout_s: float, connect: Callable[..., Any] | None = None, ca_file: str | None = None
    ) -> None:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < float(timeout_s) <= 30:
            raise StoreNotConfigured("timeout_s must be in (0, 30]")
        self._p = parse_dsn(dsn)
        if (
            ca_file is not None
        ):  # Supabase's certificates chain to ITS OWN root CA (a public file shipped in the image), not the system bundle
            if not isinstance(ca_file, str) or not Path(ca_file).is_file():
                raise StoreNotConfigured("the store CA file is not a readable file")
            self._p["sslrootcert"] = ca_file
        self._timeout = float(timeout_s)
        self._connect_fn = connect

    def __repr__(self) -> str:
        return "PgSpendStore(<redacted>)"

    # ---- connection ----------------------------------------
    def _connect(self) -> Any:
        connect = self._connect_fn
        if connect is None:
            try:
                import pg8000.dbapi as driver  # lazy: an optional extra
            except ImportError:
                raise StoreNotConfigured("the pg8000 driver is not installed (pip install pravrudhi[assist])") from None
            connect = driver.connect
        p = self._p
        try:
            conn = connect(
                user=p["user"],
                password=p["password"],
                host=p["host"],
                port=p["port"],
                database=p["database"],
                ssl_context=tls_context(p["sslrootcert"]),
                timeout=self._timeout,
                application_name="pravrudhi-assist",
            )
            conn.autocommit = True
            return conn
        except StoreError:
            raise
        except Exception as exc:  # noqa: BLE001 - the driver's message may contain the host or the user: only the class name leaves this module
            raise StoreError(f"the store cannot be reached ({type(exc).__name__})") from None

    def _call(self, sql: str, params: tuple[Any, ...]) -> Any:
        conn = None
        try:
            conn = self._connect()
            cur = conn.cursor()
            try:
                cur.execute(sql, params)
                rows = cur.fetchall()
            finally:
                with contextlib.suppress(Exception):
                    cur.close()
            return rows
        except StoreError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise StoreError(f"the store call failed ({type(exc).__name__})") from None
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    @staticmethod
    def _json(rows: Any) -> dict[str, Any]:
        try:
            v = rows[0][0]
            v = json.loads(v) if isinstance(v, (str, bytes)) else v
        except (IndexError, TypeError, ValueError):
            raise StoreError("the store answered with something that is not JSON") from None
        if not isinstance(v, Mapping) or "ok" not in v and "initialised" not in v:
            raise StoreError("the store answered with an unexpected shape")
        return dict(v)

    # ---- the narrow role's functions ----------------------------------------
    def reserve(self, key: str, max_cents: int, ceiling_cents: int) -> dict[str, Any]:
        _int(max_cents, 1), _int(ceiling_cents, 1)
        return self._json(self._call("select assist.reserve(%s, %s, %s)", (_uuid(key), max_cents, ceiling_cents)))

    def finish(self, key: str, list_cost_cents: int | None) -> dict[str, Any]:
        if list_cost_cents is not None:
            _int(list_cost_cents, 0)
        return self._json(self._call("select assist.finish(%s, %s)", (_uuid(key), list_cost_cents)))

    def abandon_stale(self, minutes: int) -> dict[str, Any]:
        return self._json(self._call("select assist.abandon_stale(%s)", (_int(minutes, 1),)))

    def sweep_ok(self) -> dict[str, Any]:
        return self._json(self._call("select assist.sweep_ok()", ()))

    def status(self) -> dict[str, Any]:
        return self._json(self._call("select assist.status()", ()))

    def session_created(self, anthropic_session_id: str, key: str) -> dict[str, Any]:
        return self._json(self._call("select assist.session_created(%s, %s)", (_sid(anthropic_session_id), _uuid(key))))

    def session_deleted(self, anthropic_session_id: str, http_status: int) -> dict[str, Any]:
        return self._json(
            self._call("select assist.session_deleted(%s, %s)", (_sid(anthropic_session_id), _int(http_status, 100)))
        )

    # ---- the sweeper role's functions (a PgSpendStore built from the sweeper DSN) ----
    def canary(self) -> dict[str, Any]:
        return self._json(self._call("select assist.canary()", ()))

    def mark_deleted(self, anthropic_session_id: str, http_status: int) -> dict[str, Any]:
        return self._json(self._call("select assist.mark_deleted(%s, %s)", (_sid(anthropic_session_id), _int(http_status, 100))))

    def prune(self) -> int:
        rows = self._call("select assist.prune()", ())
        try:
            return _int(rows[0][0], 0)
        except (IndexError, TypeError):
            raise StoreError("the store answered with an unexpected shape") from None

    def list_pending(self, older_than_minutes: int) -> list[dict[str, Any]]:
        rows = self._call(
            "select anthropic_session_id, created_at, state from assist.list_pending(%s)", (_int(older_than_minutes, 0),)
        )
        try:
            return [{"anthropic_session_id": str(r[0]), "created_at": r[1], "state": str(r[2])} for r in rows]
        except (IndexError, TypeError):
            raise StoreError("the store answered with an unexpected shape") from None


def _int(v: Any, lo: int) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < lo:
        raise StoreError("an integer argument is out of range")
    return v


def _uuid(v: Any) -> str:
    try:
        u = uuid.UUID(str(v))
    except ValueError:
        raise StoreError("the reservation key is not a uuid") from None
    if str(u) != str(v) or u.version != 4:
        raise StoreError("the reservation key must be a lower-case uuid4")
    return str(u)


def _sid(v: Any) -> str:
    import re

    if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v):
        raise StoreError("not an Anthropic session id")
    return v


def from_env(
    env: Mapping[str, str],
    dsn_env: str,
    *,
    timeout_s: float,
    connect: Callable[..., Any] | None = None,
    ca_file: str | None = None,
) -> tuple[PgSpendStore | None, str | None]:
    """(store, None) or (None, "not_configured"). Unset DSN, a refused DSN or a missing driver all mean the assisted
    route is not configured and screening carries on untouched."""
    dsn = env.get(dsn_env, "").strip()
    if not dsn:
        return None, "not_configured"
    try:
        store = PgSpendStore(dsn, timeout_s=timeout_s, connect=connect, ca_file=ca_file)
    except StoreNotConfigured:
        return None, "not_configured"
    if connect is None:
        import importlib.util

        if importlib.util.find_spec("pg8000") is None:
            return None, "not_configured"
    return store, None
