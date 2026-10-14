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
import logging
import os
import re
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import HTTPException

from pravrudhi.api.identity import User
from pravrudhi.api.roles import admin_ids
from pravrudhi.application.portable_lock import exclusive_lock
from pravrudhi.application.tenancy_store import PgTenancyStore, TenancyStoreDown
from pravrudhi.application.tenancy_store import from_env as _store_from_env

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
_KEY_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
KEY_PREFIX = "pnk"

_hasher = PasswordHasher()
_logger = logging.getLogger(__name__)

BACKEND_ENV = "PRAVRUDHI_TENANCY_BACKEND"
KEY_CACHE_ENV = "PRAVRUDHI_TENANCY_KEY_CACHE_S"
DEFAULT_KEY_CACHE_S = 15.0


class TenancyUnavailable(HTTPException):
    """The tenancy store cannot answer: a keyed request is refused with 503, never read as an unknown key and never accepted."""

    def __init__(self) -> None:
        super().__init__(status_code=503, detail="key service unavailable")


_pg_lock = threading.Lock()
_pg_cache: dict[str, PgTenancyStore | None] = {}
_key_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _backend_is_supabase() -> bool:
    return os.environ.get(BACKEND_ENV, "file").strip().lower() == "supabase"


def _pg() -> PgTenancyStore | None:
    """None = the file backend (dev, tests, a local engine). In the hosted shape `PRAVRUDHI_TENANCY_BACKEND=supabase` REQUIRES the
    store: a missing DSN or driver raises TenancyUnavailable, it never falls back to the volatile files (that IS the bug)."""
    if not _backend_is_supabase():
        return None
    with _pg_lock:
        key = os.environ.get("SUPABASE_TENANCY_DB_URL", "")
        if key not in _pg_cache:
            _pg_cache[key] = _store_from_env(os.environ)
        store = _pg_cache[key]
    if store is None:
        raise TenancyUnavailable()
    return store


_status_lock = threading.Lock()
_status: dict[str, Any] = {"value": "checking", "at": 0.0, "running": False}
_STATUS_TTL_S = 30.0


def store_status() -> str:
    """`file` (local backend), or the hosted store's state: `ok`, `unavailable` or `checking` (first call before an answer).
    Published on /api/health as a label only (no value, no path). Never blocks the caller: a stale answer starts one background
    re-check and returns the last known state."""
    if not _backend_is_supabase():
        return "file"
    now = time.monotonic()
    with _status_lock:
        stale = now - float(_status["at"]) > _STATUS_TTL_S
        start = stale and not _status["running"]
        if start:
            _status["running"] = True
        value = str(_status["value"])

    def check() -> None:
        try:
            pg = _pg()
            ok = pg is not None and pg.canary().get("ok") is True
            result = "ok" if ok else "unavailable"
        except Exception:  # noqa: BLE001 - a health label must never raise
            result = "unavailable"
        with _status_lock:
            _status.update(value=result, at=time.monotonic(), running=False)

    if start:
        threading.Thread(target=check, name="tenancy-status", daemon=True).start()
    return value


def reset_backend_for_tests() -> None:
    with _status_lock:
        _status.update(value="checking", at=0.0, running=False)
    with _pg_lock:
        _pg_cache.clear()
        _key_cache.clear()


def _key_cache_s() -> float:
    try:
        v = float(os.environ.get(KEY_CACHE_ENV, DEFAULT_KEY_CACHE_S))
    except ValueError:
        return DEFAULT_KEY_CACHE_S
    return v if 0 <= v <= 300 else DEFAULT_KEY_CACHE_S


def _down() -> TenancyUnavailable:
    return TenancyUnavailable()


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
    pg = _pg()
    if pg is not None:
        try:
            res = pg.create_org(oid, name.strip(), _now())
        except TenancyStoreDown as exc:
            raise _down() from exc
        if res.get("ok") is not True:
            raise TenancyError(f"org {oid!r} already exists" if res.get("reason") == "exists" else "org could not be created")
        return Org(**res["org"])

    def mutate(rows: dict[str, dict[str, Any]]) -> Org:
        if oid in rows:
            raise TenancyError(f"org {oid!r} already exists")
        org = Org(id=oid, name=name.strip(), created=_now())
        rows[oid] = org.to_dict()
        return org

    return _with_store(root, "orgs", mutate)


def get_org(root: Path, org_id: str) -> Org | None:
    pg = _pg()
    if pg is not None:
        try:
            row = pg.get_org(org_id)
        except TenancyStoreDown as exc:
            raise _down() from exc
        return Org(**row) if row else None
    row = _read_store(root, "orgs").get(org_id)
    return Org(**row) if row else None


def list_orgs(root: Path) -> list[Org]:
    pg = _pg()
    if pg is not None:
        try:
            return [Org(**r) for r in pg.list_orgs()]
        except TenancyStoreDown as exc:
            raise _down() from exc
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
    pg = _pg()
    if pg is not None:
        try:
            res = pg.add_member(oid, uid, role)
        except TenancyStoreDown as exc:
            raise _down() from exc
        if res.get("ok") is not True:
            raise TenancyError("org does not exist" if res.get("reason") == "no_org" else "membership could not be added")
        return Membership(org_id=oid, user_id=uid, role=role)

    def mutate(rows: dict[str, dict[str, Any]]) -> Membership:
        org_rows = rows.setdefault(oid, {})
        org_rows[uid] = {"role": role}
        return Membership(org_id=oid, user_id=uid, role=role)

    return _with_store(root, "memberships", mutate)


def memberships_for_org(root: Path, org_id: str) -> list[Membership]:
    pg = _pg()
    if pg is not None:
        try:
            return [Membership(**r) for r in pg.memberships_for_org(org_id)]
        except TenancyStoreDown as exc:
            raise _down() from exc
    org_rows = _read_store(root, "memberships").get(org_id, {})
    return [Membership(org_id=org_id, user_id=uid, role=row["role"]) for uid, row in sorted(org_rows.items())]


def membership_role(root: Path, org_id: str, user_id: str) -> str | None:
    if _pg() is not None:
        return next((m.role for m in memberships_for_org(root, org_id) if m.user_id == user_id), None)
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

    pg = _pg()
    if pg is not None:
        try:
            res = pg.create_key(record.to_dict())
        except TenancyStoreDown as exc:
            raise _down() from exc
        if res.get("ok") is not True:
            raise TenancyError("key could not be created")
        return CreatedApiKey(record=record, secret=token)

    def mutate(rows: dict[str, dict[str, Any]]) -> None:
        rows[key_id] = record.to_dict()

    _with_store(root, "keys", mutate)
    return CreatedApiKey(record=record, secret=token)


def revoke_key(root: Path, key_id: str) -> ApiKeyRecord:
    pg = _pg()
    if pg is not None:
        try:
            res = pg.revoke_key(key_id, _now())
        except TenancyStoreDown as exc:
            raise _down() from exc
        _key_cache.pop(key_id, None)  # this process stops honouring the key at once (other replicas within the cache TTL)
        if res.get("ok") is not True:
            raise TenancyError(f"key {key_id!r} does not exist")
        return ApiKeyRecord(**res["key"])

    def mutate(rows: dict[str, dict[str, Any]]) -> ApiKeyRecord:
        row = rows.get(key_id)
        if row is None:
            raise TenancyError(f"key {key_id!r} does not exist")
        row["revoked"] = True
        row["revoked_at"] = _now()
        return ApiKeyRecord(**row)

    return _with_store(root, "keys", mutate)


def keys_for_org(root: Path, org_id: str) -> list[ApiKeyRecord]:
    pg = _pg()
    if pg is not None:
        try:
            return [ApiKeyRecord(**r) for r in pg.keys_for_org(org_id)]
        except TenancyStoreDown as exc:
            raise _down() from exc
    rows = _read_store(root, "keys")
    return [
        ApiKeyRecord(**row) for row in sorted(rows.values(), key=lambda r: r["created"]) if row["org_id"] == org_id
    ]


class InvalidApiKey(ValueError):
    """A presented token that does not name a live, unrevoked key -- deliberately one exception for every
    failure shape (malformed, unknown key id, wrong secret, revoked) so a caller can never distinguish
    "this key id does not exist" from "this key id exists but the secret is wrong", which would otherwise
    let an attacker enumerate valid key ids by timing or error-shape alone."""


def _key_row(root: Path, key_id: str) -> dict[str, Any] | None:
    """The stored row for a key id: the file store's dict, or the Supabase row through a short positive cache (`key_cache_s`).
    A store failure raises TenancyUnavailable (503): it is never read as 'unknown key'. Only a row that EXISTS is cached."""
    pg = _pg()
    if pg is None:
        return _read_store(root, "keys").get(key_id)
    ttl = _key_cache_s()
    now = time.monotonic()
    hit = _key_cache.get(key_id)
    if hit is not None and hit[0] > now:
        return hit[1]
    try:
        row = pg.get_key(key_id)
    except TenancyStoreDown as exc:
        raise _down() from exc
    if row is not None and ttl > 0:
        if len(_key_cache) > 10_000:
            _key_cache.clear()
        _key_cache[key_id] = (now + ttl, row)
    return row


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
    row = _key_row(root, key_id)
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

MIN_PROVISION_SECRET_LENGTH = 32
"""A configured `TENANCY_PROVISION_SECRET_ENV` shorter than this is treated as though it were never set at
all -- fail closed, the same as unset -- rather than accepted as a weak-but-working unlock. A short static
secret compared over HTTP is guessable in a feasible number of attempts even with `provision_rate_limit_per_
minute` throttling it; refusing to use it is safer than an operator believing a 6-character value they typed
in a hurry is protecting these routes."""

_short_secret_warned_lock = threading.Lock()
_short_secret_warned = False


def _warn_short_secret_once() -> None:
    """Exactly one warning per process for a too-short configured secret -- an operator who set a weak value
    should hear about it, but not once per request; never logs the value itself."""
    global _short_secret_warned
    with _short_secret_warned_lock:
        if _short_secret_warned:
            return
        _short_secret_warned = True
    _logger.warning(
        "%s is set but shorter than %d characters -- treating tenancy provisioning as NOT configured "
        "(failing closed) rather than accepting a weak secret. Set a longer value to use this path.",
        TENANCY_PROVISION_SECRET_ENV, MIN_PROVISION_SECRET_LENGTH,
    )


def is_tenancy_admin(user: User | None, headers: Mapping[str, str]) -> bool:
    """Authorises every tenancy provisioning route (create org, create/list/revoke keys) -- passes only when
    at least one of two things holds, and refuses everyone else, including an anonymous caller on an
    auth-disabled deployment that `roles.is_admin` would otherwise wave through:

    (a) `user` is a real, verified, non-anonymous identity (never `None` -- `auth_mode() == DISABLED`'s "the
        local caller is the operator by construction" reasoning is not consulted here at all) whose id or
        email is on the same allowlist (`roles.admin_ids()`) `roles.role_of` uses for every other admin
        surface; or
    (b) the caller presents `TENANCY_PROVISION_HEADER` and it matches `TENANCY_PROVISION_SECRET_ENV`, which
        must itself be at least `MIN_PROVISION_SECRET_LENGTH` characters or this path is treated as
        unconfigured entirely (see `_warn_short_secret_once`).

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
    if secret and len(secret) < MIN_PROVISION_SECRET_LENGTH:
        _warn_short_secret_once()
        secret = ""
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

    def snapshot(self, key_id: str, per_minute: int) -> tuple[int, int, int]:
        """(limit, remaining calls in this window, seconds until the window resets). Read-only: admits nothing."""
        window = int(self._now() // 60)
        with self._lock:
            start, count = self._windows.get(key_id, (window, 0))
            used = count if start == window else 0
        return per_minute, max(per_minute - used, 0), self.retry_after_seconds()

    def usage_total(self, key_id: str) -> int:
        """Calls admitted for this key since process start -- an in-memory counter, not a ledger claim: it
        resets on restart and exists only to answer `/usage` cheaply, not as billing evidence."""
        with self._lock:
            return self._usage_total.get(key_id, 0)


# Usage counters in the Supabase backend are written OFF the request path through a bounded in-memory queue and one worker thread:
# counting must never slow or fail a call. On overflow or a store error the row is dropped and counted (`usage_queue_stats`).
_USAGE_MAX = 1000
_usage_q: Any = None
_usage_stats = {"queued": 0, "written": 0, "dropped": 0}
_usage_cond = threading.Condition()


def usage_queue_stats() -> dict[str, int]:
    return dict(_usage_stats)


def _usage_worker() -> None:
    while True:
        with _usage_cond:
            while not _usage_q:
                _usage_cond.wait()
            item = _usage_q.popleft()
        try:
            pg = _pg()
            if pg is not None:
                name, args = item
                getattr(pg, name)(*args)
                _usage_stats["written"] += 1
        except (TenancyStoreDown, HTTPException):
            _usage_stats["dropped"] += 1
        except Exception:  # noqa: BLE001 - a counter write must never kill the worker
            _usage_stats["dropped"] += 1
        with _usage_cond:
            _usage_cond.notify_all()


def flush_usage(timeout_s: float = 5.0) -> bool:
    """Wait until the usage queue is empty (tests, shutdown). True when it drained in time."""
    deadline = time.monotonic() + timeout_s
    with _usage_cond:
        while _usage_q and time.monotonic() < deadline:
            _usage_cond.wait(0.05)
        done = not _usage_q
    time.sleep(0.05)  # the item being written when the queue emptied
    return done


def _enqueue_usage(key_id: str, day: str, failed: bool) -> None:
    enqueue_store_write("record_usage", key_id, day, failed)


def enqueue_store_write(name: str, *args: Any) -> None:
    """A best-effort write to the tenancy store off the request path (usage counters, audit rows): bounded, dropped and counted
    on overflow or on a store error, never raising into the call that produced it."""
    global _usage_q
    from collections import deque

    with _usage_cond:
        if _usage_q is None:
            _usage_q = deque()
            threading.Thread(target=_usage_worker, name="tenancy-usage", daemon=True).start()
        if len(_usage_q) >= _USAGE_MAX:
            _usage_stats["dropped"] += 1
            return
        _usage_q.append((name, args))
        _usage_stats["queued"] += 1
        _usage_cond.notify_all()


def record_usage(root: Path, key_id: str, *, failed: bool = False, now: datetime | None = None) -> None:
    """Persist one analyse-facts call against `key_id` (`failed=True` additionally counts that call's 503; every
    admitted call is in `calls` first, so failed <= calls).
    Survives a restart and is shared by replicas on one volume, unlike `KeyRateLimiter`."""
    if _backend_is_supabase():
        _enqueue_usage(key_id, (now or datetime.now(UTC)).astimezone(UTC).date().isoformat(), failed)
        return

    def mutate(rows: dict[str, dict[str, Any]]) -> None:
        row = rows.setdefault(key_id, {"calls": 0, "failed": 0})
        row["failed" if failed else "calls"] = int(row.get("failed" if failed else "calls", 0)) + 1

    _with_store(root, "usage", mutate)

    day = (now or datetime.now(UTC)).astimezone(UTC).date().isoformat()

    def mutate_daily(rows: dict[str, dict[str, Any]]) -> None:
        d = rows.setdefault(key_id, {}).setdefault(day, {"calls": 0, "failed": 0})
        d["failed" if failed else "calls"] = int(d.get("failed" if failed else "calls", 0)) + 1

    _with_store(root, "usage_daily", mutate_daily)


def usage_summary(root: Path, org_id: str, *, now: datetime, days: int) -> list[dict[str, Any]]:
    """Per key of `org_id` (revoked keys included), the per-UTC-day counts for the last `days` days (today
    included), oldest first. Only days with at least one call appear. Reads the persistent store; the counts
    for a day are those recorded under that UTC date at call time."""
    first = (now.astimezone(UTC).date() - timedelta(days=days - 1)).isoformat()
    pg = _pg()
    if pg is not None:
        try:
            return pg.usage_summary(org_id, first)
        except TenancyStoreDown as exc:
            raise _down() from exc
    daily = _read_store(root, "usage_daily")
    out: list[dict[str, Any]] = []
    for k in keys_for_org(root, org_id):
        rows = daily.get(k.key_id, {})
        out.append({
            "key_id": k.key_id, "label": k.label, "revoked": k.revoked,
            "days": [
                {"day": d, "calls": int(v.get("calls", 0)), "failed": int(v.get("failed", 0))}
                for d, v in sorted(rows.items()) if d >= first
            ],
        })
    return out


def usage_counts(root: Path, key_id: str) -> tuple[int, int]:
    """(admitted calls, of which failed 503s) for this key, from the persistent store."""
    pg = _pg()
    if pg is not None:
        try:
            return pg.usage_counts(key_id)
        except TenancyStoreDown as exc:
            raise _down() from exc
    row = _read_store(root, "usage").get(key_id, {})
    return int(row.get("calls", 0)), int(row.get("failed", 0))


__all__ = [
    "API_KEY_HEADER", "ApiKeyRecord", "CreatedApiKey", "InvalidApiKey", "KeyRateLimiter", "MEMBER_ROLES",
    "MIN_PROVISION_SECRET_LENGTH", "Membership", "Org", "OrgPrincipal", "TENANCY_PROVISION_HEADER",
    "TENANCY_PROVISION_SECRET_ENV", "TenancyError", "add_member", "create_key", "create_org", "get_org",
    "is_tenancy_admin", "keys_for_org", "list_orgs", "membership_role", "memberships_for_org",
    "principal_from_headers", "require_org_access", "require_tenancy_admin", "record_usage", "revoke_key",
    "usage_counts", "usage_summary", "verify_key",
]
