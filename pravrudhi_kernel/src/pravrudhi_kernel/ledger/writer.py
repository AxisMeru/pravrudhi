"""Append-only hash-chained JSONL writer. The only path by which evidence enters the ledger."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi_kernel.ledger.jcs import canonicalize
from pravrudhi_kernel.ledger.verify import verify
from pravrudhi_kernel.schema import Bucket, LedgerEvent
from pravrudhi_kernel.schema.common import Pramana, Surface

# ADR-0052: `fcntl` is POSIX-only, and a bare top-level `import fcntl` made the kernel - and so the whole CLI,
# which imports it transitively - unimportable on Windows at all (`pravrudhi --version` was the first command
# to try). Selected by which import actually succeeds, not by `sys.platform`: that is what lets a test prove
# the import path is safe without a real Windows machine, by monkeypatching `fcntl` absent.
try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]


# Windows locking (unlike POSIX flock, which is purely advisory and never blocks an ordinary read/write, even
# from a second fd in the same process) is mandatory: a locked byte range refuses reads and writes from every
# OTHER handle until unlocked. _tail() opens a second handle on the same path to read the last line, so any
# lock byte that real ledger content can ever occupy will eventually collide with a legitimate reader. Lock a
# fixed sentinel offset far beyond any realistic ledger size instead - the same idiom SQLite's Windows VFS uses
# for its own lock bytes - so the lock never overlaps real data. Locking beyond EOF does not extend the file.
_WINDOWS_LOCK_BYTE = 2**48


def _lock_exclusive(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX)
    elif msvcrt is not None:
        # locking() locks/unlocks the range starting at the fd's CURRENT position, and an unlock call must name
        # the same range as its lock - so both calls seek to the sentinel byte first, restoring the caller's
        # position afterward.
        pos = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, _WINDOWS_LOCK_BYTE, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        os.lseek(fd, pos, os.SEEK_SET)
    else:
        raise RuntimeError("no portable file lock available on this platform (neither fcntl nor msvcrt)")


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    elif msvcrt is not None:
        pos = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, _WINDOWS_LOCK_BYTE, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.lseek(fd, pos, os.SEEK_SET)
    else:
        raise RuntimeError("no portable file lock available on this platform (neither fcntl nor msvcrt)")


class ChainBroken(RuntimeError):
    pass


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def genesis_prev_hash(kernel_release: str) -> str:
    return _sha("pravrudhi-genesis|" + kernel_release)


def chain_hash(prev_hash: str, event_without_this_hash: dict[str, Any]) -> str:
    return _sha(prev_hash + "\n" + canonicalize(event_without_this_hash))


def schema_hash() -> str:
    return _sha(canonicalize(LedgerEvent.model_json_schema()))


def now_rfc3339_ms() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{datetime.now(UTC).microsecond // 1000:03d}Z"


class LedgerWriter:
    """Holds the chain head; every append verifies continuity and fsyncs.

    `clock` is injectable so golden ledgers are byte-deterministic.
    """

    def __init__(self, path: Path, kernel_release: str, *, clock: Callable[[], str] | None = None) -> None:
        self.path = Path(path)
        self.kernel_release = kernel_release
        self._clock = clock or now_rfc3339_ms
        self.seq = -1
        self.head_hash = genesis_prev_hash(kernel_release)
        self.t_last = ""

    @property
    def head_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".head")

    @classmethod
    def open(
        cls,
        path: Path,
        kernel_release: str,
        *,
        clock: Callable[[], str] | None = None,
        glossary_hash: str | None = None,
    ) -> LedgerWriter:
        w = cls(path, kernel_release, clock=clock)
        if w.path.exists() and w.path.stat().st_size > 0:
            res = verify(w.path)
            if not res.ok:
                raise ChainBroken(f"{w.path}: {res.reason} at seq {res.first_bad_seq}")
            assert res.head_hash is not None and res.t_last is not None
            w.seq, w.head_hash, w.t_last = res.n - 1, res.head_hash, res.t_last
            return w
        w.path.parent.mkdir(parents=True, exist_ok=True)
        w.append(
            "audit",
            "kernel",
            {
                "kind": "genesis",
                "kernel_release": kernel_release,
                "schema_hash": schema_hash(),
                "glossary_hash": glossary_hash or _sha("no-glossary"),
            },
            epoch=0,
            night=0,
        )
        return w

    def _tail(self) -> tuple[int, str, str] | None:
        """(seq, this_hash, t) of the last line on disk, or None for an empty file."""
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return None
        if size == 0:
            return None
        with self.path.open("rb") as fh:
            back = min(size, 65536)
            fh.seek(size - back)
            chunk = fh.read(back)
        line = chunk.rstrip(b"\n").rsplit(b"\n", 1)[-1]
        last = json.loads(line)
        return int(last["seq"]), str(last["this_hash"]), str(last["t"])

    def _build(
        self,
        kind: str,
        actor: str,
        payload: dict[str, Any],
        *,
        epoch: int,
        night: int,
        cycle: int | None,
        candidate_id: str | None,
        surface: str | Surface | None,
        bucket: dict[str, str] | Bucket | None,
        provenance: str | Pramana | None,
    ) -> LedgerEvent:
        t = self._clock()
        if self.t_last and t < self.t_last:
            t = self.t_last
        body: dict[str, Any] = {
            "seq": self.seq + 1,
            "t": t,
            "epoch": epoch,
            "night": night,
            "cycle": cycle,
            "kind": kind,
            "actor": actor,
            "candidate_id": candidate_id,
            "surface": surface,
            "bucket": bucket if not isinstance(bucket, Bucket) else bucket.model_dump(),
            "provenance": provenance,
            "kernel_release": self.kernel_release,
            "payload": payload,
            "prev_hash": self.head_hash,
        }
        # validate everything but this_hash first so a refused event never touches the file
        probe = LedgerEvent.model_validate(body | {"this_hash": "0" * 64})
        canonical_body = json.loads(probe.model_dump_json(exclude={"this_hash"}))
        this_hash = chain_hash(self.head_hash, canonical_body)
        return LedgerEvent.model_validate(canonical_body | {"this_hash": this_hash})

    def append(
        self,
        kind: str,
        actor: str,
        payload: dict[str, Any],
        *,
        epoch: int,
        night: int,
        cycle: int | None = None,
        candidate_id: str | None = None,
        surface: str | Surface | None = None,
        bucket: dict[str, str] | Bucket | None = None,
        provenance: str | Pramana | None = None,
    ) -> LedgerEvent:
        """Append under an exclusive file lock. If another writer advanced the file since this writer last
        touched it, the head is taken from the file's last line and an audit{kind: head_resync} row is written
        first (ADR-0013): concurrent writers interleave, never fork the chain."""
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            _lock_exclusive(fd)
            lines: list[LedgerEvent] = []
            tail = self._tail()
            if tail is not None and tail[1] != self.head_hash:
                stale = {"seq": self.seq, "head_hash": self.head_hash}
                self.seq, self.head_hash, self.t_last = tail[0], tail[1], max(tail[2], self.t_last)
                resync = self._build(
                    "audit",
                    "kernel",
                    {"kind": "head_resync", "severity": "info", "stale": stale, "file_seq": tail[0], "file_hash": tail[1]},
                    epoch=epoch, night=night, cycle=None, candidate_id=None, surface=None, bucket=None, provenance=None,
                )
                lines.append(resync)
                self.seq, self.head_hash, self.t_last = resync.seq, resync.this_hash, resync.t
            ev = self._build(
                kind, actor, payload, epoch=epoch, night=night, cycle=cycle, candidate_id=candidate_id,
                surface=surface, bucket=bucket, provenance=provenance,
            )
            lines.append(ev)
            os.write(fd, "".join(e.model_dump_json() + "\n" for e in lines).encode("utf-8"))
            os.fsync(fd)
        finally:
            _unlock(fd)
            os.close(fd)
        self.seq, self.head_hash, self.t_last = ev.seq, ev.this_hash, ev.t
        self.head_path.write_text(json.dumps({"seq": ev.seq, "this_hash": ev.this_hash}) + "\n")
        return ev
