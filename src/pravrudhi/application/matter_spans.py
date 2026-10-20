"""A2: the matter span-index, first slice (flag `wf_a2_span_index`, default off).

A firm puts the papers of a matter in; a question is answered with the VERBATIM spans of those papers that contain every term of
the question, each with its document, page and character offsets, or with "not found in the matter papers". No model reads or
writes anything: a returned span is the stored text itself, so it is inert (a paper that says "ignore your rules" cannot act).

Design, each point a rule from the reviewers' attack list (#959):

* ONE SQLite FILE PER MATTER, in a directory per tenant: isolation by file, not by a tenant column; nothing is stored once for
  two tenants (no content-addressed dedupe); a document id is random (uuid4), never sequential; a matter id that belongs to
  another tenant is exactly as "not found" as one that does not exist.
* Spans are paragraphs (cut at sentence ends to at most MAX_SPAN characters) with the page and the character offsets in the text
  as received; the stored span is `text[start:end]` verbatim. A form feed starts a page (the intake convention).
* CAPS UNDER A LOCK: the matter-count and tenant-bytes caps are checked and spent under one exclusive lock on the tenant folder
  (flock on the folder itself, no lock file), across threads and processes; the document and character caps are checked inside
  the matter's own write transaction.
* "Not found" is decided by a MATCHED-TERM rule, never by a score: a span is a hit only if EVERY term of the query is in it (a
  quoted phrase counts as one term); bm25 only orders the hits. The query is built from the user's terms, each escaped and
  double-quoted: raw user text never reaches FTS5, so hyphens, apostrophes, `NEAR(`, `AND`, colons and empty strings are
  data, not syntax. A closed list of function words is not required to match.
* A document is searched only once it is committed (all spans in, or none: one transaction); a page with NO TEXT LAYER is
  recorded (`pages_without_text`), the document is `indexed_complete` only when every page had text, and every answer, found or
  not, says how many pages had little or no text; a result list says "Showing 10 of 40" when it is cut.
* DELETION: DELETE + FTS5 `optimize` + VACUUM (with `secure_delete` on), then every file of the matter (database, journal, -wal,
  -shm, temp and upload leftovers) is removed. The tests scan EVERY file under the store for the deleted secret bytes.
* No claim of irrecoverability is made anywhere: a delete removes the matter from this service's store; what a disk or a backup
  may still hold is the operator's retention statement, not this module's.

This module has no web code and calls no model. The routes live in `api/partner.py`, registered only when the flag is on (the
delete route also when a store directory already exists).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictStr, model_validator

from pravrudhi.application.matter_spans_config import MatterSpansConfig

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows: the tenant lock then holds within one process only (see `_tenant_lock`)
    fcntl = None  # type: ignore[assignment]

_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()

MAX_SPAN = 1_500
SCHEMA_VERSION = 1  # PRAGMA user_version of every matter file, written when the matter is created
MIN_QUOTE_CHARS = 20  # a quote shorter than this (and than MIN_QUOTE_WORDS words) proves nothing
MIN_QUOTE_WORDS = 4
UNREAD_PAGE_CHARS = 20  # fewer non-space characters than this = 'little or no text' (as in intake); still indexed
#: bytes on disk per character of text, an upper bound measured on the worst shape (500,000 characters of one-word paragraphs
#: gave a 10.7 MB file: 21x); an ingest is refused BEFORE writing when it would not fit
DISK_EXPANSION = 25
MAX_QUERY_TERMS = 12
MAX_TERM_CHARS = 64
MAX_NAME_CHARS = 200
DB_NAME = "matter.sqlite3"
_MATTER_ID = re.compile(r"^[0-9a-f]{32}$")
_DOC_ID = re.compile(r"^[0-9a-f]{32}$")
#: function words a question may contain without each having to be in the span (a closed list; every OTHER term must match)
STOPWORDS = frozenset(
    [
        "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "did", "do", "does", "for", "from", "had",
        "has", "have", "how", "i", "if", "in", "is", "it", "its", "of", "on", "or", "so", "than", "that", "the",
        "their", "then", "there", "these", "they", "this", "those", "to", "was", "were", "what", "when", "where",
        "which", "who", "whom", "why", "will", "with", "would",
    ]
)
_SCHEMA = (
    "CREATE TABLE document (doc_id TEXT PRIMARY KEY, name TEXT NOT NULL, n_chars INTEGER NOT NULL, n_pages INTEGER NOT NULL, "
    "n_spans INTEGER NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL, ingest_done INTEGER NOT NULL, "
    "pages_without_text TEXT NOT NULL DEFAULT '[]')",
    "CREATE TABLE span (id INTEGER PRIMARY KEY, doc_id TEXT NOT NULL, page INTEGER NOT NULL, start INTEGER NOT NULL, "
    "end INTEGER NOT NULL, text TEXT NOT NULL)",
    "CREATE INDEX span_doc ON span(doc_id)",
    "CREATE VIRTUAL TABLE span_fts USING fts5(text, joined, tokenize='unicode61 remove_diacritics 2')",
)


class MatterError(Exception):
    """A refusal with a stable machine code and an HTTP status for the route."""

    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code, self.status_code = code, status_code


class MatterNotFound(MatterError):
    def __init__(self) -> None:
        super().__init__("matter_not_found", "No such set of papers", 404)


# ---- requests -------------------------------------------------------------------------------------------------------------


class DocumentRequest(BaseModel):
    matter_id: StrictStr = Field(min_length=32, max_length=32)
    text: StrictStr | None = Field(default=None, max_length=10_000_000)
    pdf_base64: StrictStr | None = Field(default=None, max_length=11_000_000)
    #: #1015: a photo or an image of a page (read only while wf_c1_scanned_intake is on; with the flag off the route refuses it).
    image_base64: StrictStr | None = Field(default=None, max_length=11_000_000)
    mime: Literal["image/png", "image/jpeg", "image/tiff", "image/heic", "image/heif"] | None = None
    name: StrictStr = Field(default="document", max_length=MAX_NAME_CHARS)

    @model_validator(mode="after")
    def _one_source(self) -> DocumentRequest:
        if sum(v is not None for v in (self.text, self.pdf_base64, self.image_base64)) != 1:
            raise ValueError("send exactly one of text, pdf_base64 and image_base64")
        if (self.image_base64 is None) != (self.mime is None):
            raise ValueError("mime goes with image_base64 and only with it")
        return self


class QueryRequest(BaseModel):
    matter_id: StrictStr = Field(min_length=32, max_length=32)
    q: StrictStr = Field(max_length=2_000)


class MatterRef(BaseModel):
    matter_id: StrictStr = Field(min_length=32, max_length=32)


class DeleteRequest(BaseModel):
    matter_id: StrictStr = Field(min_length=32, max_length=32)
    doc_id: StrictStr | None = Field(default=None, min_length=32, max_length=32)


# ---- the routes' names and the lists that must name them while they exist ----------------------------------------------------

ROUTE_PREFIX = "/api/v1/matter-spans"
ROUTE_CREATE, ROUTE_DOCUMENTS, ROUTE_QUERY, ROUTE_LIST, ROUTE_DELETE = (
    f"{ROUTE_PREFIX}/{n}" for n in ("matters", "documents", "query", "list", "delete")
)
ROUTE_MINE = f"{ROUTE_PREFIX}/my-matters"  # the caller's own matters (flag wf_a2_list_matters)


def open_route_lists(paths: list[str]) -> None:
    """Add the registered routes to the whole-surface gate's key-only list (`identity.PARTNER_KEY_ROUTES`/`PATHS`) and to
    `roles.USER_FACING`. Called only for routes that are registered, so with the feature off the lists are the released ones."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", p) for p in paths}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | set(paths)
    roles.USER_FACING = roles.USER_FACING | set(paths)


# ---- paths: one directory per tenant, one folder and one database file per matter -----------------------------------------


def tenant_dir_name(tenant: str) -> str:
    """A directory name for a tenant id that cannot walk out of the store and does not put the id on disk: a readable class
    prefix plus a hash of the id."""
    if not tenant or len(tenant) > 200:
        raise MatterError("bad_tenant", "Sign in to use stored papers", 403)
    kind = re.sub(r"[^a-z]", "", tenant.split(":", 1)[0].lower())[:8] or "t"
    return f"{kind}-{hashlib.sha256(tenant.encode()).hexdigest()[:24]}"


class MatterStore:
    def __init__(self, base: Path, cfg: MatterSpansConfig) -> None:
        self.base = Path(base)
        self.cfg = cfg

    # -- paths
    def _tenant_dir(self, tenant: str) -> Path:
        """The tenant's folder, checked at the start of EVERY operation: a folder that is a symbolic link, or that resolves to
        anywhere but inside the store, is refused before anything is read, locked, scrubbed or removed."""
        d = self.base / tenant_dir_name(tenant)
        if d.is_symlink() or (d.exists() and self.base.resolve() not in d.resolve().parents):
            raise MatterError("bad_path", "The stored papers could not be reached safely", 500)
        return d

    def _matter_dir(self, tenant: str, matter_id: str) -> Path:
        if not isinstance(matter_id, str) or not _MATTER_ID.fullmatch(matter_id):
            raise MatterNotFound()
        return self._tenant_dir(tenant) / matter_id

    def _db_path(self, tenant: str, matter_id: str) -> Path:
        d = self._matter_dir(tenant, matter_id)
        db = d / DB_NAME
        if d.is_symlink() or db.is_symlink() or not db.is_file():
            raise MatterNotFound()  # another tenant's matter, a link, or none: the same answer (a link is never opened)
        return db

    @contextmanager
    def _open(self, path: Path) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
        try:
            conn.execute("PRAGMA secure_delete = ON")
            conn.execute("PRAGMA temp_store = MEMORY")
            conn.execute("PRAGMA journal_mode = DELETE")
            yield conn
        finally:
            conn.close()

    # -- matters
    def _private_dir(self, path: Path) -> None:
        """Create `path` and every missing parent up to the store, each mode 0700, and tighten the ones that exist (the store
        and the tenant folder): a group-writable parent would let another local user list or swap a matter folder."""
        chain = [path, *[q for q in path.parents if q == self.base or self.base in q.parents]]
        for d in reversed(chain):
            if not d.is_dir():
                d.mkdir(mode=0o700, parents=True, exist_ok=True)
            with suppress(OSError):  # not the owner of an existing folder: leave it, the operator owns that decision
                os.chmod(d, 0o700)

    def _disk_floor(self, need: int) -> None:
        """Fail closed: an ingest that would leave less than `min_free_bytes` free is refused (503) BEFORE anything is written."""
        probe = self.base
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        try:
            free = shutil.disk_usage(probe).free
        except OSError as e:
            raise MatterError("store_unavailable", "The store cannot be measured", 503) from e
        if free - need < self.cfg.min_free_bytes:
            raise MatterError("store_full", "There is no room to store this document", 503)

    def _tenant_bytes(self, tenant: str) -> int:
        total = 0
        tdir = self._tenant_dir(tenant)
        if tdir.is_dir():
            for root, _dirs, files in os.walk(tdir):
                for f in files:
                    with suppress(OSError):
                        total += os.lstat(os.path.join(root, f)).st_size
        return total

    @contextmanager
    def _tenant_lock(self, tenant: str, create: bool = False, missing_ok: bool = False) -> Iterator[bool]:
        """An exclusive lock on the tenant's folder (flock on the folder itself: no lock file is left to delete), so the
        matter-count and tenant-bytes caps are checked and spent under one lock, across threads and processes alike. Yields
        True when held; with `missing_ok`, yields False (and holds nothing) when the tenant has no folder, instead of raising."""
        tdir = self._tenant_dir(tenant)
        if fcntl is None:  # pragma: no cover - no flock on this platform: serialise the threads of this process only
            with _THREAD_LOCKS_GUARD:
                lock = _THREAD_LOCKS.setdefault(str(tdir), threading.Lock())
            with lock:
                if create:
                    self._private_dir(tdir)
                elif not tdir.is_dir():
                    if missing_ok:
                        yield False
                        return
                    raise MatterNotFound()
                yield True
            return
        for _ in range(5):
            if create:
                self._private_dir(tdir)
            try:
                fd = os.open(tdir, os.O_RDONLY | os.O_DIRECTORY)
            except FileNotFoundError:
                if create:
                    continue
                if missing_ok:
                    yield False
                    return
                raise MatterNotFound() from None
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    same = os.fstat(fd).st_ino == tdir.stat().st_ino
                except FileNotFoundError:
                    same = False
                if not same:
                    continue  # the folder was removed (and maybe made again) while we waited: lock the one that exists now
                yield True
                return
            finally:
                os.close(fd)  # closing the descriptor releases the lock
        raise MatterError("store_busy", "The store is busy; try again", 503)

    def create_matter(self, tenant: str) -> str:
        tdir = self._tenant_dir(tenant)
        self._disk_floor(1_000_000)  # before anything is created
        with self._tenant_lock(tenant, create=True):
            if sum(1 for p in tdir.iterdir() if p.is_dir()) >= self.cfg.max_matters_per_tenant:
                raise MatterError(
                    "too_many_matters",
                    f"Your organisation may hold at most {self.cfg.max_matters_per_tenant} sets of papers",
                    409,
                )
            matter_id = uuid.uuid4().hex
            mdir = tdir / matter_id
            self._private_dir(mdir)
            try:
                fd = os.open(mdir / DB_NAME, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)  # private from its first byte
                os.close(fd)
                with self._open(mdir / DB_NAME) as conn:
                    for stmt in _SCHEMA:
                        conn.execute(stmt)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            except Exception:
                shutil.rmtree(mdir, ignore_errors=True)
                raise
            return matter_id

    def list_documents(self, tenant: str, matter_id: str) -> dict[str, Any]:
        with self._open(self._db_path(tenant, matter_id)) as conn:
            docs = _documents(conn)
        return {"matter_id": matter_id, "documents": docs, **_page_notes(docs)}

    def _paper_count(self, tenant: str, matter_id: str) -> int | None:
        """Fully indexed documents of one matter, or None when the matter is not readable right now (not created yet, or a
        folder that is no matter: it is left out of a listing, never guessed)."""
        try:
            with self._open(self._db_path(tenant, matter_id)) as conn:
                return int(conn.execute("SELECT COUNT(*) FROM document WHERE ingest_done = 1").fetchone()[0])
        except (MatterNotFound, sqlite3.Error):
            return None

    def _listing(self, tenant: str, ids: list[str], started: float | None = None) -> dict[str, Any]:
        """Count the papers of at most `max_list_matters` matters within `list_budget_s` (from `started`); what is not
        opened is reported as `truncated`, never guessed. A bound, not a page: the caller sees the first matters it can see."""
        t0 = time.monotonic() if started is None else started
        out: list[dict[str, Any]] = []
        truncated = len(ids) > self.cfg.max_list_matters
        for matter_id in ids[: self.cfg.max_list_matters]:
            if time.monotonic() - t0 > self.cfg.list_budget_s:
                truncated = True
                break
            n = self._paper_count(tenant, matter_id)
            if n is not None:
                out.append({"matter_id": matter_id, "papers": n})
        return {"matters": out, "truncated": truncated}

    def list_matters(self, tenant: str) -> dict[str, Any]:
        """The caller's OWN matters, newest activity first: matter id and the number of fully indexed papers. Read-only; the
        tenant is the caller's organisation (or user), so another tenant's matters cannot appear and a tenant with none gets an
        empty list, never a refusal that would tell the caller anything. Nothing is created for a tenant with no folder."""
        tdir = self._tenant_dir(tenant)
        if not tdir.is_dir():
            return {"matters": [], "truncated": False}
        found: list[tuple[float, str]] = []
        for p in tdir.iterdir():
            db = p / DB_NAME
            if _MATTER_ID.fullmatch(p.name) and p.is_dir() and not p.is_symlink() and db.is_file() and not db.is_symlink():
                with suppress(OSError):
                    found.append((-db.stat().st_mtime, p.name))
        return self._listing(tenant, [m for _t, m in sorted(found)])

    def read_passages(self, tenant: str, matter_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """READ-ONLY, for the chronology: the committed documents of a matter and every passage of them, in ONE read transaction,
        tenant-checked exactly as every A2 read (another tenant's matter, a link or none: the same 404). A document that is not
        fully indexed is never returned; nothing is written."""
        with self._open(self._db_path(tenant, matter_id)) as conn:
            conn.execute("BEGIN")
            try:
                docs = [d for d in _documents(conn) if d["ingest_done"]]
                rows = conn.execute(
                    "SELECT s.id, s.doc_id, s.page, s.start, s.end, s.text FROM span s JOIN document d ON d.doc_id = s.doc_id "
                    "WHERE d.ingest_done = 1 ORDER BY d.rowid, s.start"
                ).fetchall()
            finally:
                conn.execute("ROLLBACK")
        spans = [{"span_id": r[0], "doc_id": r[1], "page": r[2], "start": r[3], "end": r[4], "text": r[5]} for r in rows]
        return docs, spans

    # -- documents
    def add_document(self, tenant: str, matter_id: str, text: str, name: str) -> dict[str, Any]:
        if not text.strip():
            raise MatterError("no_text", "The document has no text to index (a scanned PDF has no text layer)")
        if len(text) > self.cfg.max_chars:
            raise MatterError("too_long", f"The document is longer than {self.cfg.max_chars} characters", 413)
        pages = text.split("\f")
        if len(pages) > 1 and pages[-1] == "":
            pages.pop()  # the form feed that ENDS the last page starts no page
        without_text = [i + 1 for i, pg in enumerate(pages) if sum(1 for ch in pg if not ch.isspace()) < UNREAD_PAGE_CHARS]
        spans = split_spans(text)
        if not spans:
            raise MatterError("no_text", "The document has no text to index")
        if len(spans) > self.cfg.max_spans_per_doc:
            raise MatterError("too_many_spans", f"A document may hold at most {self.cfg.max_spans_per_doc} passages", 413)
        with self._tenant_lock(tenant):
            return self._ingest(tenant, matter_id, text, name, pages, without_text, spans)

    def _ingest(
        self, tenant: str, matter_id: str, text: str, name: str, pages: list[str], without_text: list[int],
        spans: list[tuple[int, int, int]],
    ) -> dict[str, Any]:
        """The write, under the tenant lock: the tenant-bytes check and the bytes it spends are one step, so two ingests of one
        tenant cannot both pass the cap."""
        need = len(text) * DISK_EXPANSION
        if self._tenant_bytes(tenant) + need > self.cfg.max_tenant_bytes:
            raise MatterError("tenant_store_full", "Your stored papers have reached their limit", 413)
        self._disk_floor(need)
        doc_id = uuid.uuid4().hex
        sha = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        with self._open(self._db_path(tenant, matter_id)) as conn:
            # ONE transaction takes the write lock first: the checks, the document row and every span are atomic, so two ingests
            # cannot both pass a quota check, a crash leaves nothing behind, and a reader never sees a half-indexed document
            conn.execute("BEGIN IMMEDIATE")
            try:
                orphans = [r[0] for r in conn.execute("SELECT doc_id FROM document WHERE ingest_done = 0")]
                for o in orphans:  # a row a manual edit or an old crash left behind: removed, never counted
                    conn.execute("DELETE FROM span_fts WHERE rowid IN (SELECT id FROM span WHERE doc_id = ?)", (o,))
                    conn.execute("DELETE FROM span WHERE doc_id = ?", (o,))
                    conn.execute("DELETE FROM document WHERE doc_id = ?", (o,))
                docs = conn.execute("SELECT COUNT(*), COALESCE(SUM(n_chars), 0) FROM document").fetchone()
                if docs[0] >= self.cfg.max_docs:
                    raise MatterError(
                        "too_many_documents", f"A set of papers may hold at most {self.cfg.max_docs} documents", 409
                    )
                if docs[1] + len(text) > self.cfg.max_total_chars:
                    raise MatterError(
                        "matter_too_large", f"A set of papers may hold at most {self.cfg.max_total_chars} characters", 413
                    )
                conn.execute(
                    "INSERT INTO document (doc_id, name, n_chars, n_pages, n_spans, sha256, created, ingest_done, "
                    "pages_without_text) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
                    (doc_id, name.strip()[:MAX_NAME_CHARS] or "document", len(text), len(pages), len(spans), sha,
                     datetime.now(UTC).isoformat(timespec="seconds"), json.dumps(without_text)),
                )
                for page, start, end in spans:
                    cur = conn.execute(
                        "INSERT INTO span (doc_id, page, start, end, text) VALUES (?, ?, ?, ?, ?)",
                        (doc_id, page, start, end, text[start:end]),
                    )
                    conn.execute(
                        "INSERT INTO span_fts (rowid, text, joined) VALUES (?, ?, ?)",
                        (cur.lastrowid, *_fts_columns(text[start:end])),
                    )
                conn.execute("UPDATE document SET ingest_done = 1 WHERE doc_id = ?", (doc_id,))
                conn.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise
        return {"matter_id": matter_id, "doc_id": doc_id, "name": name, "n_pages": len(pages), "n_spans": len(spans),
                "n_chars": len(text), "pages_without_text": without_text, "indexed_complete": not without_text}

    # -- questions
    def query(self, tenant: str, matter_id: str, q: str) -> dict[str, Any]:
        terms = parse_query(q)
        match, variants = build_match(q, terms)
        with self._open(self._db_path(tenant, matter_id)) as conn:
            docs = _documents(conn)
            total = conn.execute(
                "SELECT COUNT(*) FROM span_fts f JOIN span s ON s.id = f.rowid JOIN document d ON d.doc_id = s.doc_id "
                "WHERE span_fts MATCH ? AND d.ingest_done = 1",
                (match,),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT s.id, s.doc_id, d.name, s.page, s.start, s.end, s.text FROM span_fts f "
                "JOIN span s ON s.id = f.rowid JOIN document d ON d.doc_id = s.doc_id "
                "WHERE span_fts MATCH ? AND d.ingest_done = 1 ORDER BY bm25(span_fts), s.id LIMIT ?",
                (match, self.cfg.max_results),
            ).fetchall()
        notes = _page_notes(docs)
        base = {
            "matter_id": matter_id,
            "terms": terms,
            "searched_as_typed": (
                SEARCHED_WITH_VARIANTS if variants else SEARCHED_AS_TYPED
            ),
            "variants": variants,
            "completeness_note": COMPLETENESS_NOTE,
            "documents": docs,
            "not_fully_indexed": [d["doc_id"] for d in docs if not d["ingest_done"]],
            **notes,
        }
        tail = f"; {notes['pages_note']}" if notes["pages_note"] else ""
        if not rows:
            return {**base, "found": False, "total_matches": 0, "truncated": False,
                    "message": "Not found in the papers you added" + tail, "spans": []}
        spans = [
            {"span_id": r[0], "doc_id": r[1], "name": r[2], "page": r[3], "start": r[4], "end": r[5], "text": r[6]} for r in rows
        ]
        truncated = total > len(spans)
        head = (
            f"Showing {len(spans)} of {total} passages found in the papers you added"
            if truncated
            else f"{len(spans)} passage{'' if len(spans) == 1 else 's'} found in the papers you added"
        )
        return {**base, "found": True, "total_matches": total, "truncated": truncated, "message": head + tail, "spans": spans}

    def check_quote(self, tenant: str, matter_id: str, doc_id: str, span_id: int, quote: str) -> dict[str, Any]:
        """Is `quote` a verbatim part of the span it cites (after the same normalisation of both sides)? A quote taken from one
        span and cited as another fails; a quote too short to prove anything (a letter, "the") is refused."""
        if not isinstance(doc_id, str) or not _DOC_ID.fullmatch(doc_id):
            return {"ok": False, "reason": "no_such_document"}
        nq = normalise_for_quote(quote)
        if len(nq) < MIN_QUOTE_CHARS and len(nq.split()) < MIN_QUOTE_WORDS:
            return {"ok": False, "reason": "quote_too_short",
                    "detail": f"A quote needs at least {MIN_QUOTE_CHARS} characters or {MIN_QUOTE_WORDS} words"}
        with self._open(self._db_path(tenant, matter_id)) as conn:
            row = conn.execute("SELECT text FROM span WHERE id = ? AND doc_id = ?", (span_id, doc_id)).fetchone()
        if row is None:
            return {"ok": False, "reason": "no_such_span"}
        ok = nq in normalise_for_quote(row[0])
        return {"ok": ok, "reason": None if ok else "not_in_the_cited_span", "normalised": list(NORMALISATIONS)}

    # -- deletion (never behind the flag)
    def delete_document(self, tenant: str, matter_id: str, doc_id: str) -> dict[str, Any]:
        if not _DOC_ID.fullmatch(doc_id or ""):
            raise MatterError("document_not_found", "No such document", 404)
        with self._open(self._db_path(tenant, matter_id)) as conn:
            if conn.execute("SELECT 1 FROM document WHERE doc_id = ?", (doc_id,)).fetchone() is None:
                raise MatterError("document_not_found", "No such document", 404)
            _scrub_document(conn, doc_id)
            _compact(conn)
        return {"deleted": True, "matter_id": matter_id, "doc_id": doc_id,
                "message": "The document was deleted from this service's store."}

    def _delete_matter_locked(self, tenant: str, matter_id: str) -> int:
        mdir = self._matter_dir(tenant, matter_id)
        db = mdir / DB_NAME
        if db.is_symlink() and not mdir.is_symlink():  # the database is a link: nothing is opened or scrubbed; the folder goes
            _remove_tree(mdir, self.base)
            raise MatterNotFound()
        if mdir.is_symlink() or not db.is_file():
            raise MatterNotFound()
        with self._open(db) as conn:
            n = conn.execute("SELECT COUNT(*) FROM document").fetchone()[0]
            for (doc_id,) in conn.execute("SELECT doc_id FROM document").fetchall():
                _scrub_document(conn, doc_id)
            _compact(conn)
        _remove_tree(mdir, self.base)
        return int(n)

    def delete_matter(self, tenant: str, matter_id: str) -> dict[str, Any]:
        self._matter_dir(tenant, matter_id)  # a malformed id is "not found" before any lock is taken
        with self._tenant_lock(tenant):
            link = self._matter_dir(tenant, matter_id)
            if link.is_symlink():  # never followed: the link is removed, and to the caller there is no such matter
                link.unlink()
                raise MatterNotFound()
            n = self._delete_matter_locked(tenant, matter_id)
            with suppress(OSError):
                self._tenant_dir(tenant).rmdir()  # only when no other matter of the tenant is left
        return {"deleted": True, "matter_id": matter_id, "documents": n,
                "message": "The papers and their stored text were deleted from this service's store."}

    def delete_tenant(self, tenant: str) -> dict[str, Any]:
        """Every folder of the tenant goes, whether or not it holds a database (a failed create, a manual edit or a crash can
        leave a folder with only a journal or -wal that still holds text): none is skipped, and the count says what was found."""
        tdir = self._tenant_dir(tenant)
        n = bare = links = 0
        with self._tenant_lock(tenant, missing_ok=True) as held:
            if held:
                for p in sorted(tdir.iterdir()):
                    if p.is_symlink():  # never followed: the link goes, what it points at is not ours to touch
                        p.unlink()
                        links += 1
                    elif not p.is_dir():
                        continue  # a plain file: removed with the tenant folder below
                    elif _MATTER_ID.fullmatch(p.name) and (p / DB_NAME).is_file() and not (p / DB_NAME).is_symlink():
                        self._delete_matter_locked(tenant, p.name)
                        n += 1
                    else:
                        links += 1 if (p / DB_NAME).is_symlink() else 0  # its database is a link: unlinked with the folder
                        bare += 0 if (p / DB_NAME).is_symlink() else 1
                        _remove_tree(p, self.base)
                if tdir.is_dir():
                    _remove_tree(tdir, self.base)
        return {"deleted": True, "matters": n, "folders_without_database": bare, "symlinks_removed": links,
                "message": "All stored papers of your organisation were deleted from this service's store."}


# ---- internals ------------------------------------------------------------------------------------------------------------


def _documents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute(
        "SELECT doc_id, name, n_pages, n_spans, n_chars, ingest_done, pages_without_text FROM document ORDER BY rowid"
    ):
        unread = json.loads(r[6] or "[]")
        out.append({"doc_id": r[0], "name": r[1], "n_pages": r[2], "n_spans": r[3], "n_chars": r[4], "ingest_done": bool(r[5]),
                    "pages_without_text": unread, "indexed_complete": bool(r[5]) and not unread})
    return out


def _page_notes(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """The denominator of what may not have been searched: pages with little or no text, per document and in total. Every
    answer carries it, so "Not found in the papers you added" is never read as "not in the pages we were given" when some pages
    were not read."""
    total = sum(d["n_pages"] for d in docs)
    unread = sum(len(d["pages_without_text"]) for d in docs)
    parts = [f'{len(d["pages_without_text"])} of {d["n_pages"]} pages of "{d["name"]}"' for d in docs if d["pages_without_text"]]
    note = ""
    if parts:
        shown = "; ".join(parts[:3]) + (f"; and {len(parts) - 3} more documents" if len(parts) > 3 else "")
        note = f"{shown} had little or no text and may not have been searched in full"
    return {"pages_total": total, "pages_not_read": unread, "pages_note": note}


def _scrub_document(conn: sqlite3.Connection, doc_id: str) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM span_fts WHERE rowid IN (SELECT id FROM span WHERE doc_id = ?)", (doc_id,))
        conn.execute("DELETE FROM span WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM document WHERE doc_id = ?", (doc_id,))
        conn.execute("COMMIT")
    except BaseException:
        with suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise


def _compact(conn: sqlite3.Connection) -> None:
    """Merge the FTS5 segments and rewrite the file, so the deleted text is not left in a free page or an old segment."""
    conn.execute("INSERT INTO span_fts (span_fts) VALUES ('optimize')")
    conn.execute("VACUUM")


def _remove_tree(path: Path, base: Path) -> None:
    """Remove a folder of the store (every file in it, the journal, -wal, -shm and any temp or upload leftover). Refuses a path
    that is not strictly inside the store, and never follows a symbolic link out of it."""
    real, root = path.resolve(), base.resolve()
    if real == root or root not in real.parents:
        raise MatterError("bad_path", "The papers could not be reached safely", 500)
    shutil.rmtree(real)


def _fts_columns(text: str) -> tuple[str, str]:
    """What FTS5 indexes for a span, normalised to NFC (so the two spellings of a nukta letter are one): the span in the `text`
    column and, when a line-break hyphen split a word, the span with the words joined in the separate `joined` column. They are
    two columns because FTS5 matches a phrase inside ONE column: put in one column, a phrase could match across the seam
    between the two copies ("gamma alpha" in "alpha x-\\ny gamma"); in two, it cannot."""
    text = unicodedata.normalize("NFC", text)
    joined = re.sub(r"(?<=\w)[-\u00ad]\s*\n\s*(?=\w)", "", text)
    return text, ("" if joined == text else joined)


NORMALISATIONS = ("NFC", "curly quotes to straight", "no-break and zero-width spaces to space", "whitespace collapsed",
                  "line-break hyphen joined", "case folded")


def normalise_for_quote(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.translate({0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x00A0: " ", 0x200B: "", 0x200C: "", 0x200D: "",
                     0x2060: "", 0xFEFF: "", 0x00AD: ""})
    s = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", s)
    return re.sub(r"\s+", " ", s).strip().casefold()



# ---- search forms (Track B's quality pass, 17 Oct): a long word also matches its common endings, and "section 420" also
# matches the forms a paper writes ("S.420", "u/s 420", "420 IPC"). Every term still needs a hit; only the forms that count as a
# hit widen. The strings below are customer-visible (PENDING R1).
SEARCHED_AS_TYPED = (
    "The words were searched as typed: not stemmed, not translated, not spell-corrected; "
    "a quoted term with no letter or digit in it (a symbol, a section sign) cannot be searched and finds nothing"
)
SEARCHED_WITH_VARIANTS = (
    "The words were searched as typed and also in the extra forms listed under variants (a longer word with other endings; "
    "a section number as it is often written); not translated, not spell-corrected; "
    "a quoted term with no letter or digit in it (a symbol, a section sign) cannot be searched and finds nothing"
)
COMPLETENESS_NOTE = "This may not be all the mentions: a word or number written another way is not found."
SECTION_WORDS = frozenset(["section", "sections", "sec", "s", "u"])
SECTION_ACTS = ("ipc", "bns", "crpc", "bnss", "ni")
_NUMBER_TERM = re.compile(r"^\d{1,4}[A-Za-z]?$")
_SECTION_REF = re.compile(r"(?i)(?<![\w])(?:sections?|sec\.?|s\.|u/s)\s*(\d{1,4}[A-Za-z]?)(?![\w])")
_ENDINGS = ("ing", "ed", "es", "s", "d")


def _q(t: str) -> str:
    return '"' + t.replace('"', '""') + '"'


SECTION_LIST_WINDOW = 8  # a section number may sit in a list after the word: "Sections 406, 407, 420 and 34"


def build_match(q: str, terms: list[str]) -> tuple[str, list[dict[str, Any]]]:
    """The FTS5 expression for a question and the list of extra forms it also searches. Terms are joined by AND exactly as before;
    a term may widen into an OR of forms. Nothing of the user's text reaches FTS5 except through `_q` (escaped, double-quoted)."""
    refs = {m.group(1).casefold() for m in _SECTION_REF.finditer(unicodedata.normalize("NFC", q))}
    number_terms = [t for t in terms if _NUMBER_TERM.fullmatch(t) and t.casefold() in refs]
    drop_words = bool(number_terms)
    parts: list[str] = []
    variants: list[dict[str, Any]] = []
    for t in terms:
        low = t.casefold()
        if drop_words and low in SECTION_WORDS:
            continue  # the section word is part of the section group below, not a separate must-match word
        if t in number_terms:
            forms = [f"section {low}", f"sections {low}", f"sec {low}", f"s {low}", f"u s {low}"]
            forms += [f"{low} {a}" for a in SECTION_ACTS]
            near = [f"NEAR({_q(w)} {_q(t)}, {SECTION_LIST_WINDOW})" for w in ("section", "sections")]
            alternatives = [*(_q(f) for f in forms), *near]
            parts.append("(" + f"({_q('section')} {_q(t)}) OR " + " OR ".join(alternatives) + ")")
            listed = f"section or sections within {SECTION_LIST_WINDOW} words of {t}"
            variants.append({"term": f"section {t}", "forms": [*forms, listed]})
            continue
        if t.isalpha() and len(t) >= 6:
            stem = t
            for suf in _ENDINGS:
                if low.endswith(suf) and len(t) - len(suf) >= 5:
                    stem = t[: -len(suf)]
                    break
            parts.append(_q(stem) + "*")
            variants.append({"term": t, "forms": [stem + "*"]})
            continue
        parts.append(_q(t))
    if not parts:  # only section words were typed: search them as typed
        parts = [_q(t) for t in terms]
        variants = []
    return " AND ".join(parts), variants


_PHRASE = re.compile(r'"([^"]{1,200})"')
_WORD = re.compile(r"[^\W_]+(?:[-'’][^\W_]+)*", re.UNICODE)


def parse_query(q: str) -> list[str]:
    """The terms of a question: each quoted phrase is one term; every other word is a term unless it is a function word. At least
    one term is required; at most MAX_QUERY_TERMS. Nothing of the user's text is passed to FTS5 except these terms, each of which
    the caller escapes and double-quotes."""
    if not isinstance(q, str) or not q.strip():
        raise MatterError("empty_query", "Ask with at least one word")
    q = unicodedata.normalize("NFC", q)
    terms: list[str] = []
    rest = q
    for m in _PHRASE.finditer(q):
        phrase = " ".join(m.group(1).split())
        if phrase:
            terms.append(phrase)
    rest = _PHRASE.sub(" ", q)
    for w in _WORD.findall(rest):
        if w.casefold() not in STOPWORDS:
            terms.append(w)
    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        if t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    if not out:
        raise MatterError("no_searchable_term", "Ask with at least one word that is not a common word such as the, of or and")
    if len(out) > MAX_QUERY_TERMS or any(len(t) > MAX_TERM_CHARS for t in out):
        raise MatterError("query_too_long", f"Use at most {MAX_QUERY_TERMS} terms of at most {MAX_TERM_CHARS} characters")
    return out


_PARA = re.compile(r"\S(?:.*?\S)?(?=\s*(?:\n[ \t]*\n|\Z))", re.DOTALL)
_SENT_END = re.compile(r"(?<=[.;:?!])\s+|\n")


def split_spans(text: str) -> list[tuple[int, int, int]]:
    """`(page, start, end)` for every span of `text`: a paragraph (blank-line separated), cut at a sentence end or a line end to
    at most MAX_SPAN characters; offsets are into `text` as received; a form feed starts a new page. `text[start:end]` is the
    span."""
    spans: list[tuple[int, int, int]] = []
    page_start = 0
    for page_no, page in enumerate(text.split("\f"), 1):
        for m in _PARA.finditer(page):
            lo, hi = page_start + m.start(), page_start + m.end()
            while hi - lo > MAX_SPAN:
                cut = lo + MAX_SPAN
                window = text[lo:cut]
                ends = [e.end() for e in _SENT_END.finditer(window)]
                cut = lo + (ends[-1] if ends and ends[-1] > MAX_SPAN // 4 else MAX_SPAN)
                end = cut
                while end > lo and text[end - 1].isspace():
                    end -= 1
                if end > lo:
                    spans.append((page_no, lo, end))
                lo = cut
                while lo < hi and text[lo].isspace():
                    lo += 1
            if hi > lo:
                spans.append((page_no, lo, hi))
        page_start += len(page) + 1
    return spans


# ---- reading a PDF (text layer only; the original upload is never stored) -------------------------------------------------


def read_document(req: DocumentRequest, cfg: MatterSpansConfig) -> str:
    if req.image_base64 is not None:  # flag off (the scanned-intake path never reaches here): a photo is not read
        raise MatterError("image_not_enabled", "Photos cannot be added here.", 415)
    if req.pdf_base64 is None:
        return req.text or ""
    from pravrudhi.application.draft_check import DraftRefused, pdf_text

    if len(req.pdf_base64) > (cfg.max_pdf_bytes * 4) // 3 + 16:
        raise MatterError("pdf_too_large", f"The PDF is larger than {cfg.max_pdf_bytes} bytes", 413)
    try:
        data = base64.b64decode(req.pdf_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise MatterError("pdf_bad_encoding", "The PDF is not valid base64") from e
    if len(data) > cfg.max_pdf_bytes:
        raise MatterError("pdf_too_large", f"The PDF is larger than {cfg.max_pdf_bytes} bytes", 413)
    try:
        return pdf_text(data, max_pages=cfg.max_pdf_pages, max_seconds=30.0, max_chars=cfg.max_chars)
    except DraftRefused as e:
        raise MatterError(e.code, str(e), e.status_code) from e


# ---- the command line: deletion needs no flag and no running service -------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse

    from pravrudhi.application.matter_spans_config import store_dir

    ap = argparse.ArgumentParser(prog="python -m pravrudhi.application.matter_spans", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("delete-matter", "delete-document", "delete-tenant"):
        p = sub.add_parser(name)
        p.add_argument("--tenant", required=True, help="the tenant id exactly as the service knows it (for example org:acme)")
        p.add_argument("--store", type=Path, default=None, help="the store directory (default: the configured one)")
        p.add_argument("--root", type=Path, default=Path("."), help="the engine root (to find the configured store)")
        if name != "delete-tenant":
            p.add_argument("--matter", required=True)
        if name == "delete-document":
            p.add_argument("--doc", required=True)
    a = ap.parse_args(argv)
    base = a.store or store_dir(a.root)
    store = MatterStore(base, MatterSpansConfig())
    try:
        if a.cmd == "delete-matter":
            out = store.delete_matter(a.tenant, a.matter)
        elif a.cmd == "delete-document":
            out = store.delete_document(a.tenant, a.matter, a.doc)
        else:
            out = store.delete_tenant(a.tenant)
    except MatterError as e:
        print(f"refused: {e.code}: {e}", file=sys.stderr)
        return 2
    print(out["message"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
