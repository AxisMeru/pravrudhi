"""#1018 step 1: the tenancy store behind a Cloudflare Worker (D1), the SAME 16 operations as `PgTenancyStore`.

The engine never calls the Cloudflare REST API and never holds `cloudflare.env`: it POSTs one operation at a time to a
tenancy Worker over https with a per-Worker service token (read from the environment by NAME, never logged, never in a
URL, argv or error). The Worker exposes ONLY these operations (no SQL, no generic query). Selected by
`PRAVRUDHI_TENANCY_BACKEND=cloudflare`; flag-off default is unchanged, and there is no fallback between backends (a
failing Worker is `TenancyStoreDown`, fail closed, exactly like the Postgres one).

Retry rule (same as Postgres): a call is retried once ONLY when it is idempotent (reads, revoke, add_member, purge): a
write whose reply was lost must not run twice. Usage and audit writes carry a client request id so the Worker can de-
duplicate a re-sent write.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from pravrudhi.application.tenancy_store import IDEMPOTENT, TenancyStoreDown, _list, _ok_dict, _row_or_none

URL_ENV = "PRAVRUDHI_TENANCY_WORKER_URL"
TOKEN_ENV = "PRAVRUDHI_TENANCY_WORKER_TOKEN"  # a RUNPOD_SECRET reference on the endpoint; the value never appears in the repo
TIMEOUT_S = 5.0
OPERATIONS = (
    "create_org", "get_org", "list_orgs", "add_member", "memberships_for_org", "create_key", "get_key", "revoke_key",
    "keys_for_org", "purge_key", "record_usage", "usage_counts", "usage_summary", "audit_record", "audit_page", "canary",
)  # fmt: skip

Transport = Callable[[str, dict[str, Any]], Any]


def _https_ok(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    return bool(u.netloc) and (
        u.scheme == "https" or (u.scheme == "http" and (u.hostname or "") in ("localhost", "127.0.0.1", "::1"))
    )


class CloudflareTenancyStore:
    def __init__(self, url: str, token: str, *, transport: Transport | None = None, timeout_s: float = TIMEOUT_S) -> None:
        if not _https_ok(url) or not token:
            raise TenancyStoreDown("the tenancy Worker is not configured")
        self._url, self._token, self._timeout = url.rstrip("/"), token, timeout_s
        self._transport = transport or self._http

    def __repr__(self) -> str:
        return "CloudflareTenancyStore(<worker>)"  # never the URL or the token

    def _http(self, op: str, body: dict[str, Any]) -> Any:
        req = urllib.request.Request(  # noqa: S310 - https (or loopback) checked at construction
            f"{self._url}/op/{op}", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
        )  # fmt: skip
        with urllib.request.urlopen(req, timeout=self._timeout) as r:  # noqa: S310
            return json.loads(r.read())

    def _call(self, op: str, **args: Any) -> Any:
        if op not in OPERATIONS:
            raise TenancyStoreDown("unknown tenancy operation")
        body = {"args": args, "request_id": uuid.uuid4().hex}  # the SAME id on a retry, so a re-sent write is de-duplicated
        for attempt in (1, 2) if op in IDEMPOTENT else (1,):
            try:
                return self._transport(op, body)
            except Exception as exc:  # noqa: BLE001 - network, TLS, HTTP and JSON errors all fail closed to one type
                if attempt == (2 if op in IDEMPOTENT else 1):
                    raise TenancyStoreDown(f"the tenancy store call failed ({type(exc).__name__})") from None
        raise TenancyStoreDown("unreachable")  # pragma: no cover

    # ---- the 16 operations (same signatures and result shapes as PgTenancyStore) --------------------------------------------
    def create_org(self, org_id: str, name: str, created: str) -> dict[str, Any]:
        return _ok_dict(self._call("create_org", org_id=org_id, name=name, created=created))

    def get_org(self, org_id: str) -> dict[str, Any] | None:
        return _row_or_none(self._call("get_org", org_id=org_id))

    def list_orgs(self) -> list[dict[str, Any]]:
        return _list(self._call("list_orgs"))

    def add_member(self, org_id: str, user_id: str, role: str) -> dict[str, Any]:
        return _ok_dict(self._call("add_member", org_id=org_id, user_id=user_id, role=role))

    def memberships_for_org(self, org_id: str) -> list[dict[str, Any]]:
        return _list(self._call("memberships_for_org", org_id=org_id))

    def create_key(self, row: Mapping[str, Any]) -> dict[str, Any]:
        keys = ("key_id", "org_id", "label", "hash", "created", "rate_limit_per_minute")
        return _ok_dict(self._call("create_key", **{k: row[k] for k in keys}))

    def get_key(self, key_id: str) -> dict[str, Any] | None:
        return _row_or_none(self._call("get_key", key_id=key_id))

    def revoke_key(self, key_id: str, at: str) -> dict[str, Any]:
        return _ok_dict(self._call("revoke_key", key_id=key_id, at=at))

    def keys_for_org(self, org_id: str) -> list[dict[str, Any]]:
        return _list(self._call("keys_for_org", org_id=org_id))

    def purge_key(self, key_id: str) -> dict[str, Any]:
        return _ok_dict(self._call("purge_key", key_id=key_id))

    def record_usage(self, key_id: str, day: str, failed: bool) -> None:
        _ok_dict(self._call("record_usage", key_id=key_id, day=day, failed=bool(failed)))

    def usage_counts(self, key_id: str) -> tuple[int, int]:
        v = self._call("usage_counts", key_id=key_id)
        if not isinstance(v, Mapping):
            raise TenancyStoreDown("the tenancy store answered with an unexpected shape")
        try:
            return int(v.get("calls", 0)), int(v.get("failed", 0))
        except (TypeError, ValueError):
            raise TenancyStoreDown("the tenancy store answered with an unexpected shape") from None

    def usage_summary(self, org_id: str, first_day: str) -> list[dict[str, Any]]:
        return _list(self._call("usage_summary", org_id=org_id, first_day=first_day))

    def audit_record(self, row: Mapping[str, Any], retention_s: int) -> dict[str, Any]:
        keys = ("ts", "key_id", "mode", "status_code", "run_id")
        return _ok_dict(
            self._call("audit_record", **{k: row[k] for k in keys}, contract_ids=list(row["contract_ids"]),
                       outcomes=row["outcomes"], retention_s=int(retention_s))
        )  # fmt: skip

    def audit_page(self, key_id: str, offset: int, limit: int, retention_s: int) -> tuple[list[dict[str, Any]], int]:
        v = self._call("audit_page", key_id=key_id, offset=int(offset), limit=int(limit), retention_s=int(retention_s))
        if not isinstance(v, Mapping) or not isinstance(v.get("rows"), list):
            raise TenancyStoreDown("the tenancy store answered with an unexpected shape")
        try:
            return [dict(r) for r in v["rows"]], int(v.get("total", 0))
        except (TypeError, ValueError):
            raise TenancyStoreDown("the tenancy store answered with an unexpected shape") from None

    def canary(self) -> dict[str, Any]:
        v = self._call("canary")
        if not isinstance(v, Mapping) or "ok" not in v:
            raise TenancyStoreDown("the tenancy store answered with an unexpected shape")
        return dict(v)

    def close(self) -> None:
        return None


def from_env(env: Mapping[str, str], *, transport: Transport | None = None) -> CloudflareTenancyStore | None:
    """None when the Worker URL or token is not set (the caller fails closed: a cloudflare backend is never half-configured)."""
    url, token = env.get(URL_ENV, "").strip(), env.get(TOKEN_ENV, "")
    if not url or not token:
        return None
    return CloudflareTenancyStore(url, token, transport=transport)
