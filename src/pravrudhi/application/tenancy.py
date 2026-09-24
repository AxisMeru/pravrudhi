"""Orgs, memberships and API keys for the partner surface (L4, `docs/decisions/LEG-PLAN-2026-09-23.md`).

`api/partner.py`'s first endpoint (`analyse-facts`) rides the same optional Supabase session identity every
other user-facing route uses; that is fine for a single caller demoing the engine, and wrong for a partner
whose staff and integrations must be told apart, rate-limited and billed separately, and who must never be
able to reach another partner's matters, documents or audit trail through a shared secret. This module is
that separation: an **org** is one partner account, a **membership** ties a Supabase user id to an org with
a role, and an **API key** is a bearer credential scoped to exactly one org that a partner's own backend (not
a human browser session) presents on every call.

**Storage.** Three JSON files under `<root>/.pravrudhi/tenancy/` (`orgs.json`, `memberships.json`,
`keys.json`), each a `{id: row}` dict, read-modify-written under `portable_lock.exclusive_lock` on a sidecar
lock file -- the same shape `memory.py` and `objectives.py` already use for engine-local state, chosen over a
new external database because nothing else in this engine runs one and CLAUDE.md's "no new external DB"
instruction for this pass says not to start. A dict keyed by id (not memory.py's append-only JSONL) is used
here because revocation and rename are in-place mutations of a row that already exists, not a growing log of
independent facts.

**Key secrecy.** `create_key` returns the plaintext secret exactly once, in the return value of that one
call; nothing this module writes to disk, logs, or returns from any other function ever contains it again.
What is stored is an argon2id hash (`argon2-cffi`, the library the module docstring for this card named) of
the secret half of the token, keyed for lookup by a random, non-secret key id carried in the token's own
prefix -- `pnk_<key_id>_<secret>` -- so verifying a presented token costs one dict lookup by `key_id` plus one
argon2 verify against that row alone, never a linear scan hashing every stored key to find a match.

**Scoping.** `require_org_access` is the one place that decides whether a principal (an admin, an org member,
or an org-scoped API key) may act on a given org's resources. Every future org-scoped route (`matters`,
`documents`, the citation/research/draft/audit endpoints the plan lists as still to come) is expected to call
it before touching that org's data, so a cross-org attempt is refused in one place rather than reimplemented,
and drifting, per route.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException

from pravrudhi.api.identity import User
from pravrudhi.api.roles import admin_ids
from pravrudhi.application.portable_lock import exclusive_lock

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_KEY_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
KEY_PREFIX = "pnk"

_hasher = PasswordHasher()


class TenancyError(ValueError):
    """A tenancy request that is malformed, not a permission question -- see `HTTPException` for those."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_id(value: str, *, label: str) -> str:
    v = (value or "").strip().lower()
    if not ID_RE.match(v):
        raise TenancyError(f"{label} {value!r} must be lowercase letters, digits and hyphens (2-63 chars)")
    return v


def tenancy_dir(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "tenancy"


def _store_path(root: Path, name: str) -> Path:
    return tenancy_dir(root) / f"{name}.json"


def _lock_path(root: Path, name: str) -> Path:
    return tenancy_dir(root) / f".{name}.lock"


def _read_store(root: Path, name: str) -> dict[str, dict[str, Any]]:
    path = _store_path(root, name)
    if not path.exists():
        return {}
    try:
        body = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _write_store(root: Path, name: str, rows: dict[str, dict[str, Any]]) -> None:
    path = _store_path(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, sort_keys=True, indent=2))
    tmp.replace(path)  # atomic on POSIX and Windows alike -- no reader ever sees a half-written store


def _with_store[T](
    root: Path, name: str, mutate: Callable[[dict[str, dict[str, Any]]], T]
) -> T:
    """Read-modify-write one store under its own exclusive lock. `mutate` receives the current rows (a plain
    dict it may modify in place) and returns whatever the caller wants back; the mutated rows are then
    persisted before the lock is released, so two concurrent writers to the same store never race."""
    with exclusive_lock(_lock_path(root, name)):
        rows = _read_store(root, name)
        result = mutate(rows)
        _write_store(root, name, rows)
        return result


# --------------------------------------------------------------------------------------------------------
# Orgs
# --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Org:
    id: str
    name: str
    created: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "created": self.created}


def create_org(root: Path, org_id: str, name: str) -> Org:
    oid = _validate_id(org_id, label="org id")
    if not name.strip():
        raise TenancyError("org name must not be blank")

    def mutate(rows: dict[str, dict[str, Any]]) -> Org:
        if oid in rows:
            raise TenancyError(f"org {oid!r} already exists")
        org = Org(id=oid, name=name.strip(), created=_now())
        rows[oid] = org.to_dict()
        return org

    return _with_store(root, "orgs", mutate)


def get_org(root: Path, org_id: str) -> Org | None:
    row = _read_store(root, "orgs").get(org_id)
    return Org(**row) if row else None


def list_orgs(root: Path) -> list[Org]:
    return [Org(**row) for row in sorted(_read_store(root, "orgs").values(), key=lambda r: r["id"])]


# --------------------------------------------------------------------------------------------------------
# Memberships
# --------------------------------------------------------------------------------------------------------

MEMBER_ROLES = frozenset({"owner", "member"})


@dataclass(frozen=True)
class Membership:
    org_id: str
    user_id: str
    role: str


def add_member(root: Path, org_id: str, user_id: str, role: str = "member") -> Membership:
    oid = _validate_id(org_id, label="org id")
    if role not in MEMBER_ROLES:
        raise TenancyError(f"role {role!r} must be one of {sorted(MEMBER_ROLES)}")
    if not user_id.strip():
        raise TenancyError("user id must not be blank")
    uid = user_id.strip()

    def mutate(rows: dict[str, dict[str, Any]]) -> Membership:
        org_rows = rows.setdefault(oid, {})
        org_rows[uid] = {"role": role}
        return Membership(org_id=oid, user_id=uid, role=role)

    return _with_store(root, "memberships", mutate)


def memberships_for_org(root: Path, org_id: str) -> list[Membership]:
    org_rows = _read_store(root, "memberships").get(org_id, {})
    return [Membership(org_id=org_id, user_id=uid, role=row["role"]) for uid, row in sorted(org_rows.items())]


def membership_role(root: Path, org_id: str, user_id: str) -> str | None:
    org_rows = _read_store(root, "memberships").get(org_id, {})
    row = org_rows.get(user_id)
    return row["role"] if row else None


# --------------------------------------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------------------------------------


def _new_key_id() -> str:
    return "".join(secrets.choice(_KEY_ID_ALPHABET) for _ in range(16))


@dataclass(frozen=True)
class ApiKeyRecord:
    """What is stored for one key -- never the plaintext secret, only its argon2id hash."""

    key_id: str
    org_id: str
    label: str
    hash: str
    created: str
    revoked: bool = False
    revoked_at: str | None = None
    rate_limit_per_minute: int = 60

    def to_dict(self) -> dict[str, Any]:
        return {
            "key_id": self.key_id, "org_id": self.org_id, "label": self.label, "hash": self.hash,
            "created": self.created, "revoked": self.revoked, "revoked_at": self.revoked_at,
            "rate_limit_per_minute": self.rate_limit_per_minute,
        }

    def to_public_dict(self) -> dict[str, Any]:
        """Everything about the key except its hash -- what an API response may show."""
        d = self.to_dict()
        d.pop("hash")
        return d


@dataclass(frozen=True)
class CreatedApiKey:
    """The one-time return of `create_key`: the stored record, plus the plaintext secret that is never
    stored and never returned again by anything else in this module."""

    record: ApiKeyRecord
    secret: str


def create_key(root: Path, org_id: str, *, label: str = "", rate_limit_per_minute: int = 60) -> CreatedApiKey:
    oid = _validate_id(org_id, label="org id")
    if get_org(root, oid) is None:
        raise TenancyError(f"org {oid!r} does not exist")
    key_id = _new_key_id()
    secret_part = secrets.token_urlsafe(32)
    token = f"{KEY_PREFIX}_{key_id}_{secret_part}"
    record = ApiKeyRecord(
        key_id=key_id, org_id=oid, label=label, hash=_hasher.hash(secret_part), created=_now(),
        rate_limit_per_minute=rate_limit_per_minute,
    )

    def mutate(rows: dict[str, dict[str, Any]]) -> None:
        rows[key_id] = record.to_dict()

    _with_store(root, "keys", mutate)
    return CreatedApiKey(record=record, secret=token)


def revoke_key(root: Path, key_id: str) -> ApiKeyRecord:
    def mutate(rows: dict[str, dict[str, Any]]) -> ApiKeyRecord:
        row = rows.get(key_id)
        if row is None:
            raise TenancyError(f"key {key_id!r} does not exist")
        row["revoked"] = True
        row["revoked_at"] = _now()
        return ApiKeyRecord(**row)

    return _with_store(root, "keys", mutate)


def keys_for_org(root: Path, org_id: str) -> list[ApiKeyRecord]:
    rows = _read_store(root, "keys")
    return [
        ApiKeyRecord(**row) for row in sorted(rows.values(), key=lambda r: r["created"]) if row["org_id"] == org_id
    ]


class InvalidApiKey(ValueError):
    """A presented token that does not name a live, unrevoked key -- deliberately one exception for every
    failure shape (malformed, unknown key id, wrong secret, revoked) so a caller can never distinguish
    "this key id does not exist" from "this key id exists but the secret is wrong", which would otherwise
    let an attacker enumerate valid key ids by timing or error-shape alone."""


def verify_key(root: Path, token: str) -> ApiKeyRecord:
    """Resolve a presented `X-Pravrudhi-Api-Key` token to its (unrevoked) record, or raise `InvalidApiKey`.

    Looks the key id up directly (an `O(1)` dict read, never a scan that argon2-verifies every stored key)
    and only then runs the one argon2 verify the token's secret half needs -- but not-found, wrong-secret
    and revoked all raise the identical exception after paying the identical argon2 cost, so neither timing
    nor message tells a caller which one happened. Revocation is checked strictly AFTER the verify, never
    before it: an early return on `revoked` would make a revoked key's failure roughly 2000x cheaper than
    every other rejection (reviewer 1, fix-before-merge on the first version of this function), which is
    itself a timing side-channel -- it would tell a caller "this key id used to be real" for free.
    """
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        raise InvalidApiKey("malformed token")
    _prefix, key_id, secret_part = parts
    rows = _read_store(root, "keys")
    row = rows.get(key_id)
    if row is None:
        # Still pay the hashing cost so "unknown key id" takes comparable time to every other failure --
        # verified against a fixed dummy hash rather than skipping straight to the raise.
        with contextlib.suppress(VerifyMismatchError):
            _hasher.verify(_DUMMY_HASH, secret_part)
        raise InvalidApiKey("unknown key id")
    try:
        _hasher.verify(row["hash"], secret_part)
    except VerifyMismatchError as exc:
        raise InvalidApiKey("secret does not match") from exc
    if row.get("revoked"):
        raise InvalidApiKey("key is revoked")
    return ApiKeyRecord(**row)


_DUMMY_HASH = PasswordHasher().hash(secrets.token_urlsafe(32))


# --------------------------------------------------------------------------------------------------------
# Principal resolution and scoping
# --------------------------------------------------------------------------------------------------------


API_KEY_HEADER = "x-pravrudhi-api-key"


@dataclass(frozen=True)
class OrgPrincipal:
    """The caller, once an API key has been verified: which org it is scoped to, and the key id (for usage
    accounting), never the secret."""

    org_id: str
    key_id: str


def principal_from_headers(root: Path, headers: Any) -> OrgPrincipal | None:
    """`None` when no API key header was sent (the caller is relying on Supabase identity, or nothing, and
    that path is untouched by this module). Raises `HTTPException(401)` when a key header WAS sent but does
    not verify -- a caller who supplied a bad key is never silently treated as anonymous."""
    token = headers.get(API_KEY_HEADER)
    if not token:
        return None
    try:
        record = verify_key(root, token)
    except InvalidApiKey as exc:
        raise HTTPException(status_code=401, detail="Invalid or revoked API key") from exc
    return OrgPrincipal(org_id=record.org_id, key_id=record.key_id)


def require_org_access(principal_org_id: str | None, resource_org_id: str, *, is_admin: bool) -> None:
    """The one gate every org-scoped resource must pass through. An engine admin (the operator, per
    `roles.is_admin`) always passes -- support and audit need to reach any org. An API-key principal passes
    only when its org matches the resource's org exactly. Everyone else (including a Supabase user with no
    org key at all) is refused. Always a 403, never a 404: a partner learning "this id does not exist" vs
    "this id exists but is not yours" is not this engine's business to distinguish, but a 404 here would
    invite a caller to keep guessing ids to map another org's resource space, which 403 does not.
    """
    if is_admin:
        return
    if principal_org_id is not None and principal_org_id == resource_org_id:
        return
    raise HTTPException(status_code=403, detail="This resource does not belong to your organisation.")


# --------------------------------------------------------------------------------------------------------
# Tenancy provisioning authorization (fail-closed)
# --------------------------------------------------------------------------------------------------------

TENANCY_PROVISION_SECRET_ENV = "PRAVRUDHI_TENANCY_PROVISION_SECRET"
"""A static secret, set by the operator, that unlocks org/key provisioning with no Supabase identity at all
-- required on a self-hosted deployment that runs with `PRAVRUDHI_AUTH` left at its `disabled` default (the
5090 demo-mode config), where `roles.role_of(None)` resolves an anonymous caller to `ADMIN` by construction.
That fallback is correct for the `ADMIN_ONLY` surfaces in `roles.py` -- Pravrudhi improving *itself*, which
only makes sense for the single operator running their own local engine -- and it is exactly wrong here: it
would let anyone who reaches this deployment's port create a partner org and mint that org's first live API
key (reviewer 1, fix-before-merge, demonstrated with a real, unoverridden `TestClient`). Provisioning
therefore never consults `role_of` or `is_admin` at all. Empty or unset means this deployment has configured
no way to provision tenancy over HTTP -- deliberately fail-closed, the same shape `roles.ADMIN_ENV` already
uses for its own allowlist."""

TENANCY_PROVISION_HEADER = "x-pravrudhi-tenancy-secret"
"""Carries `TENANCY_PROVISION_SECRET_ENV`'s value. Compared with `hmac.compare_digest`, never `==` -- a
plain string comparison short-circuits on the first mismatched byte, which leaks how many leading bytes a
guess got right through response timing; `compare_digest` runs in time dependent only on the (public)
lengths involved."""


def is_tenancy_admin(user: User | None, headers: Mapping[str, str]) -> bool:
    """Authorises every tenancy provisioning route (create org, create/list/revoke keys) -- passes only when
    at least one of two things holds, and refuses everyone else, including an anonymous caller on an
    auth-disabled deployment that `roles.is_admin` would otherwise wave through:

    (a) `user` is a real, verified, non-anonymous identity (never `None` -- `auth_mode() == DISABLED`'s "the
        local caller is the operator by construction" reasoning is not consulted here at all) whose id or
        email is on the same allowlist (`roles.admin_ids()`) `roles.role_of` uses for every other admin
        surface; or
    (b) the caller presents `TENANCY_PROVISION_HEADER` and it matches `TENANCY_PROVISION_SECRET_ENV`.

    Returns `False` -- never raises -- so a caller can decide whether `False` means "403" (the provisioning
    routes) or "fall through to some other check" (a future org-scoped route that also accepts a member,
    say); `require_tenancy_admin` is the raising form provisioning routes actually call.
    """
    if user is not None:
        allowed = admin_ids()
        candidates = {user.id.strip().lower()}
        if user.email:
            candidates.add(user.email.strip().lower())
        if allowed and candidates & allowed:
            return True
    secret = os.environ.get(TENANCY_PROVISION_SECRET_ENV, "")
    if secret:
        presented = headers.get(TENANCY_PROVISION_HEADER, "")
        if presented and hmac.compare_digest(presented, secret):
            return True
    return False


def require_tenancy_admin(user: User | None, headers: Mapping[str, str]) -> None:
    """The raising form of `is_tenancy_admin`. 403, not 401: this is an authorization refusal (the caller may
    be a perfectly valid, signed-in user, just not one on the allowlist and not carrying the secret), the
    same distinction `roles.require_admin` draws for every other admin-only surface."""
    if not is_tenancy_admin(user, headers):
        raise HTTPException(
            status_code=403,
            detail="Tenancy provisioning requires an allowlisted admin identity or the provisioning secret.",
        )


# --------------------------------------------------------------------------------------------------------
# Per-key rate limiting and usage counters
# --------------------------------------------------------------------------------------------------------


class KeyRateLimiter:
    """Fixed-window per-API-key-id counter, the same shape as `api.partner.RateLimiter` (bounded LRU table,
    stale-window sweep, never evicts a live entry to admit a new one) but keyed by `key_id` instead of
    client IP, and with each key's own `rate_limit_per_minute` rather than one value for every caller --
    an org that paid for a higher ceiling is not throttled at the anonymous default."""

    def __init__(self, *, max_keys: int = 10_000, now: Callable[[], float] = time.monotonic) -> None:
        self._max_keys = max_keys
        self._now = now
        self._lock = threading.Lock()
        self._windows: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._usage_total: dict[str, int] = {}

    def _evict_stale_locked(self, current_window: int) -> None:
        while self._windows:
            oldest_key, (start, _count) = next(iter(self._windows.items()))
            if start == current_window:
                break
            del self._windows[oldest_key]

    def allow(self, key_id: str, per_minute: int) -> bool:
        window = int(self._now() // 60)
        with self._lock:
            self._evict_stale_locked(window)
            if key_id in self._windows:
                start, count = self._windows.pop(key_id)
                if start != window:
                    start, count = window, 0
            elif len(self._windows) >= self._max_keys:
                return False
            else:
                start, count = window, 0
            if count >= per_minute:
                self._windows[key_id] = (start, count)
                return False
            self._windows[key_id] = (start, count + 1)
            self._usage_total[key_id] = self._usage_total.get(key_id, 0) + 1
            return True

    def retry_after_seconds(self) -> int:
        return 60 - int(self._now() % 60)

    def usage_total(self, key_id: str) -> int:
        """Calls admitted for this key since process start -- an in-memory counter, not a ledger claim: it
        resets on restart and exists only to answer `/usage` cheaply, not as billing evidence."""
        with self._lock:
            return self._usage_total.get(key_id, 0)


__all__ = [
    "API_KEY_HEADER", "ApiKeyRecord", "CreatedApiKey", "InvalidApiKey", "KeyRateLimiter", "MEMBER_ROLES",
    "Membership", "Org", "OrgPrincipal", "TENANCY_PROVISION_HEADER", "TENANCY_PROVISION_SECRET_ENV",
    "TenancyError", "add_member", "create_key", "create_org", "get_org", "is_tenancy_admin", "keys_for_org",
    "list_orgs", "membership_role", "memberships_for_org", "principal_from_headers", "require_org_access",
    "require_tenancy_admin", "revoke_key", "verify_key",
]
