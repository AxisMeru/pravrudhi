"""A2 matter store kept in an S3-compatible object store (the fallback to the network volume), behind the same flag
`wf_a2_span_index`.

The matter store (`matter_spans.MatterStore`) is one SQLite file per matter on a local disk. This module leaves that code as
it is and replicates each matter file to an object store, so a worker that starts cold (a new pod, a scale-from-zero start, a
second or third worker) finds every matter. Nothing is stored in a different form: an object is the whole matter database,
and there is no database server.

How it works (each rule is a test):

* A matter is `<prefix>/<tenant-dir>/<matter-id>/`: `snapshot-<seq>-<uuid>.sqlite3` objects (the name is never reused: a
  unique name per write), `HEAD` (JSON: seq, key, sha256, bytes: the one pointer that says which snapshot is current) and
  `LOCK`. The tenant directory name is the store's own (a class prefix and a hash of the tenant id), so a tenant only ever
  lists and reads its own prefix: another tenant's matter is exactly as "not found" as one that does not exist.
* READ: one small `HEAD` request; when the local copy is older, the snapshot is downloaded, checked against its sha256,
  written under a unique temporary name and renamed into place (atomic). A matter with no `HEAD` is not found, and a stale
  local copy of it is removed.
* WRITE (add or delete a document, delete a matter): take the matter's remote lock (a conditional create with an expiry; an
  expired lock is taken over with a conditional replace), bring the local copy up to date, run the store's own operation,
  upload a new snapshot under a new unique name, then replace `HEAD` conditionally on the version it read. The lock is
  checked again just before `HEAD` moves. Any failure after the local change discards the local copy of that matter, so the
  next call re-reads the last good snapshot and the caller gets a 503, never a half-written matter. Up to three workers may
  write: they take turns by the lock.
* DELETE removes `HEAD` first (the matter is gone to every reader), then every snapshot, then the lock.
* The conditional writes are probed once at first use (a create-if-absent must refuse a second create). A service that lacks
  them is not used: every call answers 503 `store_unavailable` rather than risk two workers overwriting each other.
* Credentials are read from the environment by NAME and never logged, returned or put in an error.

Operator requirements (a runbook line, not enforced here): a PRIVATE bucket, a key limited to this prefix, server-side
encryption turned on, and an https endpoint (plain http is accepted only for localhost). Until a leftover-snapshot sweep
has run, a deleted document's text may still sit in an older snapshot after a failed cleanup (the next write or the
retry removes it), so no retention sentence about the object store belongs in the product text yet.

Known limits, stated plainly: a whole matter file moves on every write (fine for the sizes the caps allow, not for very large
matters); the tenant-bytes cap is counted on the local copies only (the matter-count cap is counted on the remote objects); a
snapshot older than the previous one is removed after each write, the previous one is kept. Data in the object store is the
operator's retention concern as much as a disk is: a delete here removes it from this service's store and nothing more is
claimed."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pravrudhi.application.matter_spans import DB_NAME, MatterError, MatterNotFound, MatterStore, tenant_dir_name
from pravrudhi.application.matter_spans_config import MatterSpansConfig

_logger = logging.getLogger(__name__)

ENDPOINT_ENV = "PRAVRUDHI_MATTER_OBJECT_ENDPOINT"
BUCKET_ENV = "PRAVRUDHI_MATTER_OBJECT_BUCKET"
PREFIX_ENV = "PRAVRUDHI_MATTER_OBJECT_PREFIX"
REGION_ENV = "PRAVRUDHI_MATTER_OBJECT_REGION"
ACCESS_KEY_ENV = "PRAVRUDHI_MATTER_OBJECT_ACCESS_KEY"
SECRET_KEY_ENV = "PRAVRUDHI_MATTER_OBJECT_SECRET_KEY"
LOCK_TTL_S = 60.0
LOCK_WAIT_S = 15.0
HTTP_TIMEOUT_S = 15.0
SYNC_NAME = "sync.json"
_MATTER_ID = re.compile(r"[0-9a-f]{32}")


class Precondition(Exception):
    """The object store refused a conditional write (HTTP 412, or 409 on some services): the key changed or already exists."""


class ObjectStoreError(Exception):
    """The object store could not be reached or answered something unusable. Carries no URL, key or credential."""


class Objects(Protocol):
    def put(self, key: str, data: bytes, *, if_none_match: bool = False, if_match: str | None = None) -> str: ...
    def get(self, key: str) -> tuple[bytes, str] | None: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str) -> list[tuple[str, int]]: ...


# ---- settings (environment only: no secret in a file, none in the repo) -------------------------------------------------------


@dataclass(frozen=True)
class ObjectStoreSettings:
    endpoint: str
    bucket: str
    prefix: str = "matter_spans"
    region: str = "us-east-1"


def object_store_settings(environ: dict[str, str] | None = None) -> ObjectStoreSettings | None:
    """The object-store settings, or None when no endpoint and bucket are named (the plain disk store is used). Pure."""
    env = os.environ if environ is None else environ
    endpoint, bucket = env.get(ENDPOINT_ENV, "").strip(), env.get(BUCKET_ENV, "").strip()
    if not endpoint or not bucket:
        return None
    prefix = env.get(PREFIX_ENV, "").strip().strip("/") or "matter_spans"
    return ObjectStoreSettings(endpoint.rstrip("/"), bucket, prefix, env.get(REGION_ENV, "").strip() or "us-east-1")


# ---- the client: S3 REST with Signature V4, standard library only --------------------------------------------------------------


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sign_v4(
    method: str,
    host: str,
    path: str,
    query: dict[str, str],
    headers: dict[str, str],
    payload_sha: str,
    *,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime,
    service: str = "s3",
) -> dict[str, str]:
    """The headers that sign one request (AWS Signature V4, header form). `path` is already URI-encoded. Pure."""
    amz = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz[:8]
    hs = {k.lower(): " ".join(v.split()) for k, v in headers.items()}
    hs["host"], hs["x-amz-date"], hs["x-amz-content-sha256"] = host, amz, payload_sha
    names = sorted(hs)
    canon_q = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in sorted(query.items())
    )
    canon = "\n".join([method, path, canon_q, "".join(f"{n}:{hs[n]}\n" for n in names), ";".join(names), payload_sha])
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz, scope, _sha256(canon.encode())])
    k = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), day), region), service), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out = dict(headers)
    out.update({"x-amz-date": amz, "x-amz-content-sha256": payload_sha})
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={';'.join(names)}, Signature={sig}"
    return out


class S3Objects:
    """Path-style S3 client: PUT, GET, DELETE and ListObjectsV2, with If-None-Match / If-Match on PUT."""

    def __init__(
        self, settings: ObjectStoreSettings, environ: dict[str, str] | None = None, clock: Callable[[], datetime] | None = None
    ) -> None:
        env = os.environ if environ is None else environ
        self._access, self._secret = env.get(ACCESS_KEY_ENV, ""), env.get(SECRET_KEY_ENV, "")
        if not self._access or not self._secret:
            raise ObjectStoreError("the object-store credentials are not set")
        u = urllib.parse.urlsplit(settings.endpoint)
        if not u.netloc or not (
            u.scheme == "https" or (u.scheme == "http" and (u.hostname or "") in ("localhost", "127.0.0.1", "::1"))
        ):
            raise ObjectStoreError("the object-store endpoint must be an https URL")
        self._base, self._host, self._bucket, self._region = (
            f"{u.scheme}://{u.netloc}",
            u.netloc,
            settings.bucket,
            settings.region,
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def _call(
        self, method: str, key: str, body: bytes = b"", query: dict[str, str] | None = None, headers: dict[str, str] | None = None
    ) -> tuple[int, dict[str, str], bytes]:
        path = "/" + urllib.parse.quote(self._bucket, safe="-_.~") + ("/" + urllib.parse.quote(key, safe="-_.~/") if key else "")
        q = query or {}
        signed = sign_v4(
            method,
            self._host,
            path,
            q,
            headers or {},
            _sha256(body),
            access_key=self._access,
            secret_key=self._secret,
            region=self._region,
            now=self._clock(),
        )
        url = (
            self._base
            + path
            + (
                "?"
                + "&".join(
                    f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in sorted(q.items())
                )
                if q
                else ""
            )
        )
        data = body if method in ("PUT", "POST") else None
        req = urllib.request.Request(url, data=data, method=method, headers=signed)  # noqa: S310 - scheme checked above
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as r:  # noqa: S310
                return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
        except urllib.error.HTTPError as e:
            return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise ObjectStoreError("the object store could not be reached") from e

    def put(self, key: str, data: bytes, *, if_none_match: bool = False, if_match: str | None = None) -> str:
        h: dict[str, str] = {}
        if if_none_match:
            h["If-None-Match"] = "*"
        if if_match is not None:
            h["If-Match"] = if_match
        status, hdr, _ = self._call("PUT", key, data, headers=h)
        if status in (409, 412):
            raise Precondition()
        if status not in (200, 201, 204):
            raise ObjectStoreError(f"the object store refused a write (HTTP {status})")
        return hdr.get("etag", "")

    def get(self, key: str) -> tuple[bytes, str] | None:
        status, hdr, body = self._call("GET", key)
        if status == 404:
            return None
        if status != 200:
            raise ObjectStoreError(f"the object store refused a read (HTTP {status})")
        return body, hdr.get("etag", "")

    def delete(self, key: str) -> None:
        status, _, _ = self._call("DELETE", key)
        if status not in (200, 202, 204, 404):
            raise ObjectStoreError(f"the object store refused a delete (HTTP {status})")

    def list(self, prefix: str) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        token = ""
        while True:
            q = {"list-type": "2", "prefix": prefix}
            if token:
                q["continuation-token"] = token
            status, _, body = self._call("GET", "", query=q)
            if status != 200:
                raise ObjectStoreError(f"the object store refused a listing (HTTP {status})")
            try:
                root = ET.fromstring(body)  # noqa: S314 - the answer of the operator's own object store, no entity use
            except ET.ParseError as e:
                raise ObjectStoreError("the object store gave an unreadable listing") from e
            ns = root.tag.partition("}")[0] + "}" if root.tag.startswith("{") else ""
            for c in root.findall(f"{ns}Contents"):
                k, s = c.findtext(f"{ns}Key"), c.findtext(f"{ns}Size")
                if k is not None and s is not None and s.isdigit():
                    out.append((k, int(s)))
            if (root.findtext(f"{ns}IsTruncated") or "").lower() != "true":
                return out
            token = root.findtext(f"{ns}NextContinuationToken") or ""
            if not token:
                return out


class MemoryObjects:
    """An in-memory object store with the same conditional-write rules, for tests and for local trials. Thread-safe."""

    def __init__(self, conditional: bool = True) -> None:
        self._d: dict[str, tuple[bytes, str]] = {}
        self._lock = threading.Lock()
        self._conditional = conditional
        self._n = 0

    def put(self, key: str, data: bytes, *, if_none_match: bool = False, if_match: str | None = None) -> str:
        with self._lock:
            have = self._d.get(key)
            if self._conditional:
                if if_none_match and have is not None:
                    raise Precondition()
                if if_match is not None and (have is None or have[1] != if_match):
                    raise Precondition()
            self._n += 1
            etag = f'"{self._n}-{_sha256(data)[:16]}"'
            self._d[key] = (bytes(data), etag)
            return etag

    def get(self, key: str) -> tuple[bytes, str] | None:
        with self._lock:
            return self._d.get(key)

    def delete(self, key: str) -> None:
        with self._lock:
            self._d.pop(key, None)

    def list(self, prefix: str) -> list[tuple[str, int]]:
        with self._lock:
            return sorted((k, len(v[0])) for k, v in self._d.items() if k.startswith(prefix))


# ---- the store ----------------------------------------------------------------------------------------------------------------


def _unavailable(why: str = "The stored papers cannot be reached just now; try again") -> MatterError:
    return MatterError("store_unavailable", why, 503)


class ObjectBackedMatterStore(MatterStore):
    """`MatterStore` whose matters also live in an object store. `base` is the local working copy (it may start empty)."""

    def __init__(
        self,
        base: Path,
        cfg: MatterSpansConfig,
        objects: Objects,
        prefix: str = "matter_spans",
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(base, cfg)
        self.objects, self.prefix, self._sleep, self._clock = objects, prefix.strip("/"), sleep, clock
        self._probe_ok: bool | None = None
        self._probe_lock = threading.Lock()
        self._local_locks: dict[str, threading.Lock] = {}
        self._local_guard = threading.Lock()

    # -- keys
    def _mprefix(self, tenant: str, matter_id: str) -> str:
        if not isinstance(matter_id, str) or not _MATTER_ID.fullmatch(matter_id):
            raise MatterNotFound()
        return f"{self.prefix}/{tenant_dir_name(tenant)}/{matter_id}/"

    def _tprefix(self, tenant: str) -> str:
        return f"{self.prefix}/{tenant_dir_name(tenant)}/"

    # -- guards
    def _ready(self) -> None:
        """Probe once that the service honours conditional writes; without that two workers could overwrite each other."""
        with self._probe_lock:
            if self._probe_ok is None:
                key = f"{self.prefix}/_probe/{uuid.uuid4().hex}"
                try:
                    self.objects.put(key, b"1", if_none_match=True)
                    try:
                        self.objects.put(key, b"2", if_none_match=True)
                        ok = False  # a second create-if-absent was accepted: the condition is ignored
                    except Precondition:
                        ok = True
                    finally:
                        with suppress(ObjectStoreError):
                            self.objects.delete(key)
                except (ObjectStoreError, Precondition):
                    self._probe_ok = None
                    raise _unavailable() from None
                self._probe_ok = ok
                if not ok:
                    _logger.warning(
                        "the object store ignores conditional writes: the matter store answers 503 until it is replaced"
                    )
            if not self._probe_ok:
                raise _unavailable("The stored papers are not available on this service")

    @contextmanager
    def _wrap(self) -> Iterator[None]:
        try:
            yield
        except ObjectStoreError:
            raise _unavailable() from None
        except Precondition:
            raise MatterError("store_busy", "The store is busy; try again", 503) from None

    def _local_lock(self, key: str) -> threading.Lock:
        with self._local_guard:
            return self._local_locks.setdefault(key, threading.Lock())

    # -- head and snapshots
    def _read_head(self, tenant: str, matter_id: str) -> tuple[dict[str, Any], str] | None:
        got = self.objects.get(self._mprefix(tenant, matter_id) + "HEAD")
        if got is None:
            return None
        try:
            head = json.loads(got[0])
            if not (
                isinstance(head, dict)
                and isinstance(head["seq"], int)
                and isinstance(head["key"], str)
                and isinstance(head["sha256"], str)
            ):
                raise ValueError
        except (ValueError, KeyError):
            raise ObjectStoreError("a matter pointer is unreadable") from None
        return head, got[1]

    def _local_seq(self, mdir: Path) -> int:
        try:
            return int(json.loads((mdir / SYNC_NAME).read_text())["seq"]) if (mdir / DB_NAME).is_file() else -1
        except (OSError, ValueError, KeyError, TypeError):
            return -1

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")  # a unique temporary name: two writers never share one
        try:
            fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            with suppress(OSError):
                tmp.unlink()

    def _pull(self, tenant: str, matter_id: str, head: dict[str, Any]) -> None:
        got = self.objects.get(self._mprefix(tenant, matter_id) + str(head["key"]).rsplit("/", 1)[-1])
        if got is None or _sha256(got[0]) != head["sha256"]:
            raise ObjectStoreError("a matter snapshot is missing or does not match its pointer")
        mdir = self._matter_dir(tenant, matter_id)
        self._disk_floor(len(got[0]))
        self._private_dir(mdir)
        self._atomic_write(mdir / DB_NAME, got[0])
        self._atomic_write(mdir / SYNC_NAME, json.dumps({"seq": head["seq"]}).encode())

    def _drop_local(self, tenant: str, matter_id: str) -> None:
        mdir = self._matter_dir(tenant, matter_id)
        if mdir.exists() or mdir.is_symlink():
            if mdir.is_symlink():
                mdir.unlink()
            else:
                shutil.rmtree(mdir, ignore_errors=True)

    def _current(self, tenant: str, matter_id: str) -> tuple[dict[str, Any], str]:
        """Bring the local copy to the remote HEAD (or raise not found). Returns the head and its etag. A snapshot that vanished
        between reading HEAD and reading the snapshot (a writer moved on and swept it) is retried against the new HEAD."""
        for attempt in range(3):
            found = self._read_head(tenant, matter_id)
            if found is None:
                self._drop_local(tenant, matter_id)
                raise MatterNotFound()
            head, etag = found
            mdir = self._matter_dir(tenant, matter_id)
            if self._local_seq(mdir) == head["seq"]:
                return head, etag
            try:
                self._pull(tenant, matter_id, head)
                return head, etag
            except ObjectStoreError:
                if attempt == 2:
                    raise
                self._sleep(0.05 + random.random() * 0.1)  # noqa: S311 - jitter, not security
        raise ObjectStoreError("unreachable")  # pragma: no cover

    def _sweep(self, prefix: str, keep: set[str]) -> int:
        """Delete every snapshot of a matter except the named ones (the caller holds the matter's lock). Returns how many went."""
        n = 0
        for k, _size in self.objects.list(prefix):
            leaf = k.rsplit("/", 1)[-1]
            if leaf.startswith("snapshot-") and leaf not in keep:
                self.objects.delete(k)
                n += 1
        return n

    def _push(self, tenant: str, matter_id: str, prev: tuple[dict[str, Any], str] | None, keep_previous: bool = True) -> None:
        mdir = self._matter_dir(tenant, matter_id)
        db = mdir / DB_NAME
        tmp = mdir / f"snapshot.{uuid.uuid4().hex}.tmp"
        try:
            src = sqlite3.connect(str(db), timeout=10.0)
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)  # a consistent copy, whatever else touches the file
            finally:
                dst.close()
                src.close()
            data = tmp.read_bytes()
        finally:
            with suppress(OSError):
                tmp.unlink()
        seq = (prev[0]["seq"] if prev else 0) + 1
        name = f"snapshot-{seq:012d}-{uuid.uuid4().hex}.sqlite3"
        mp = self._mprefix(tenant, matter_id)
        self.objects.put(mp + name, data, if_none_match=True)
        head = {"seq": seq, "key": name, "sha256": _sha256(data), "bytes": len(data)}
        self.objects.put(mp + "HEAD", json.dumps(head).encode(), **({"if_match": prev[1]} if prev else {"if_none_match": True}))
        self._atomic_write(mdir / SYNC_NAME, json.dumps({"seq": seq}).encode())
        keep = {name, str(prev[0]["key"]) if prev and keep_previous else ""}
        if keep_previous:
            with suppress(ObjectStoreError):  # an old snapshot left behind is harmless here; the next write removes it
                self._sweep(mp, keep)
        else:
            self._sweep(
                mp, keep
            )  # after a delete no older copy may stay: a failure here is the caller's 503, a retry sweeps again

    # -- the remote lock
    @contextmanager
    def _remote_lock(self, tenant: str, matter_id: str) -> Iterator[Callable[[], bool]]:
        key = self._mprefix(tenant, matter_id) + "LOCK"
        me = uuid.uuid4().hex
        deadline = self._clock() + LOCK_WAIT_S

        def body() -> bytes:
            return json.dumps({"owner": me, "expires": self._clock() + LOCK_TTL_S}).encode()

        while True:
            try:
                self.objects.put(key, body(), if_none_match=True)
                break
            except Precondition:
                got = self.objects.get(key)
                if got is not None:
                    try:
                        expires = float(json.loads(got[0]).get("expires", 0))
                    except (ValueError, TypeError, AttributeError):
                        expires = 0.0
                    if expires < self._clock():  # an expired lock is taken over, conditionally on exactly the version seen
                        with suppress(Precondition):
                            self.objects.put(key, body(), if_match=got[1])
                            break
                if self._clock() >= deadline:
                    raise MatterError("store_busy", "The store is busy; try again", 503) from None
                self._sleep(0.15 + random.random() * 0.25)  # noqa: S311 - jitter, not security

        def still_mine() -> bool:
            got = self.objects.get(key)
            if got is None:
                return False
            try:
                d = json.loads(got[0])
                return bool(d.get("owner") == me and float(d.get("expires", 0)) > self._clock())
            except (ValueError, TypeError, AttributeError):
                return False

        try:
            yield still_mine
        finally:
            with suppress(ObjectStoreError):
                got = self.objects.get(key)
                if got is not None and json.loads(got[0]).get("owner") == me:
                    self.objects.delete(key)

    @contextmanager
    def _write(self, tenant: str, matter_id: str, keep_previous: bool = True) -> Iterator[tuple[dict[str, Any], str]]:
        """Serialise writers on one matter (threads: a local lock; workers: the remote lock); yields the head read under it."""
        mp = self._mprefix(tenant, matter_id)
        with self._local_lock(mp), self._remote_lock(tenant, matter_id) as mine:
            prev = self._current(tenant, matter_id)
            try:
                yield prev
                if not mine():
                    raise MatterError("store_busy", "The store is busy; try again", 503)
                self._push(tenant, matter_id, prev, keep_previous)
            except BaseException:
                with suppress(OSError, MatterError):
                    self._drop_local(tenant, matter_id)  # the next call re-reads the last good snapshot
                raise

    # -- the public operations
    def create_matter(self, tenant: str) -> str:
        with self._wrap():
            self._ready()
            count = sum(1 for k, _ in self.objects.list(self._tprefix(tenant)) if k.endswith("/HEAD"))
            if count >= self.cfg.max_matters_per_tenant:
                raise MatterError(
                    "too_many_matters",
                    f"Your organisation may hold at most {self.cfg.max_matters_per_tenant} sets of papers",
                    409,
                )
            matter_id = super().create_matter(tenant)
            try:
                self._push(tenant, matter_id, None)
            except BaseException:
                with suppress(OSError, MatterError):
                    self._drop_local(tenant, matter_id)
                raise
            return matter_id

    def _read(self, tenant: str, matter_id: str) -> None:
        self._ready()
        self._current(tenant, matter_id)

    def list_documents(self, tenant: str, matter_id: str) -> dict[str, Any]:
        with self._wrap():
            self._read(tenant, matter_id)
            return super().list_documents(tenant, matter_id)

    def list_matters(self, tenant: str) -> dict[str, Any]:
        """The tenant's matters as the object store knows them (a cold worker may hold no local copy): every matter with a HEAD
        pointer, each brought to its current snapshot, then counted. Bounded like the folder listing (`max_list_matters`, and
        `list_budget_s` for the pulls): a cold tenant would download every snapshot in one request. What is not reached is
        reported as `truncated`. A matter that is mid-delete or unreadable is left out.

        ORDER: by matter id, which says nothing about age (the folder store lists newest activity first). The object listing
        carries no modification time, so a cut list here is an arbitrary but stable first N, not the newest N: until the object
        interface returns a time, do not rely on this listing for a tenant that can exceed `max_list_matters` (R2, #597)."""
        started = time.monotonic()
        with self._wrap():
            self._ready()
            tp = self._tprefix(tenant)
            ids = sorted({k[len(tp) :].split("/", 1)[0] for k, _ in self.objects.list(tp) if k.endswith("/HEAD")})
            ids = [m for m in ids if _MATTER_ID.fullmatch(m)]
            ready: list[str] = []
            truncated = len(ids) > self.cfg.max_list_matters
            for matter_id in ids[: self.cfg.max_list_matters]:
                if time.monotonic() - started > self.cfg.list_budget_s:
                    truncated = True
                    break
                try:
                    self._current(tenant, matter_id)
                except MatterNotFound:
                    continue
                ready.append(matter_id)
            out = self._listing(tenant, ready, started)
            out["truncated"] = bool(out["truncated"] or truncated)
            return out

    def read_passages(self, tenant: str, matter_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self._wrap():
            self._read(tenant, matter_id)
            return super().read_passages(tenant, matter_id)

    def query(self, tenant: str, matter_id: str, q: str) -> dict[str, Any]:
        with self._wrap():
            self._read(tenant, matter_id)
            return super().query(tenant, matter_id, q)

    def check_quote(self, tenant: str, matter_id: str, doc_id: str, span_id: int, quote: str) -> dict[str, Any]:
        with self._wrap():
            self._read(tenant, matter_id)
            return super().check_quote(tenant, matter_id, doc_id, span_id, quote)

    def add_document(
        self,
        tenant: str,
        matter_id: str,
        text: str,
        name: str,
        ocr_pages: list[int] | None = None,
        *,
        ocr_layout: dict[int, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        with self._wrap():
            self._ready()
            with self._write(tenant, matter_id):
                return super().add_document(tenant, matter_id, text, name, ocr_pages, ocr_layout=ocr_layout)

    def delete_document(self, tenant: str, matter_id: str, doc_id: str) -> dict[str, Any]:
        with self._wrap():
            self._ready()
            with self._write(tenant, matter_id, keep_previous=False) as (
                head,
                _etag,
            ):  # no older snapshot may keep the deleted text
                try:
                    return super().delete_document(tenant, matter_id, doc_id)
                except MatterError as e:
                    # A document already gone for readers whose older snapshots were left (a delete whose cleanup failed): this
                    # retry sweeps them and answers as a delete, not "no such document" while old text is still held.
                    mp = self._mprefix(tenant, matter_id)
                    if e.code == "document_not_found" and self._sweep(mp, {str(head["key"])}) > 0:
                        return {
                            "deleted": True, "matter_id": matter_id, "doc_id": doc_id,
                            "message": "The document was deleted from this service's store.",
                        }  # fmt: skip
                    raise

    def delete_matter(self, tenant: str, matter_id: str) -> dict[str, Any]:
        with self._wrap():
            self._ready()
            mp = self._mprefix(tenant, matter_id)
            with self._local_lock(mp), self._remote_lock(tenant, matter_id):
                self._current(tenant, matter_id)
                out = super().delete_matter(tenant, matter_id)
                self.objects.delete(mp + "HEAD")  # first: from here no reader finds the matter
                for k, _size in self.objects.list(mp):
                    if not k.endswith("/LOCK"):
                        self.objects.delete(k)
            return out

    def delete_tenant(self, tenant: str) -> dict[str, Any]:
        with self._wrap():
            self._ready()
            tp = self._tprefix(tenant)
            listed = self.objects.list(tp)
            for k, _size in listed:
                if k.endswith("/HEAD"):
                    self.objects.delete(k)  # first: no matter of the tenant is found from here
            for k, _size in listed:
                if not k.endswith("/HEAD"):
                    self.objects.delete(k)
            out = super().delete_tenant(tenant)
            out["matters"] = max(int(out.get("matters", 0)), sum(1 for k, _ in listed if k.endswith("/HEAD")))
            return out


class _Unavailable:
    """Stands in when the settings name an object store that cannot be used (no credentials, a bad endpoint): every call is a 503,
    so a worker never falls back to a disk that a restart would empty."""

    def _no(self, *_a: Any, **_k: Any) -> Any:
        raise ObjectStoreError("the object store is not usable")

    put = get = delete = list = _no


def make_store(
    base: Path, cfg: MatterSpansConfig, settings: ObjectStoreSettings, environ: dict[str, str] | None = None
) -> ObjectBackedMatterStore:
    """The object-backed store for the environment's settings. With credentials or an endpoint that cannot be used it is returned,
    answering 503 `store_unavailable` to every call (and warning once, with no value in the message)."""
    objects: Objects
    try:
        objects = S3Objects(settings, environ)
    except ObjectStoreError as e:
        _logger.warning("matter object store not usable (%s): the matter store answers 503 until it is configured", e)
        objects = _Unavailable()
    return ObjectBackedMatterStore(base, cfg, objects, settings.prefix)
