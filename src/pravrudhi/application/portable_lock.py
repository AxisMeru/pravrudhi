"""One exclusive-file-lock helper for the engine, portable across POSIX and Windows.

ADR-0052 made the kernel's ledger writer portable because a bare top-level `import fcntl` there made the whole
CLI unimportable on Windows. That fix was correct and it was applied to exactly one file; the *class* of bug was
never swept. Two more instances survived in the engine: `application/requests.py` imported `fcntl` at module
level, and `application/loom_run.py` imported it inside a function.

v0.1.7's Windows packaged smoke found the first the only way it could be found — by getting far enough to run
the engine at all. Three releases in one evening each peeled a layer: the kernel could not be imported, then the
smoke harness could not unpack the artifact, then the desktop app could not locate the engine, and then the
engine died on `import fcntl` at `application/requests.py:25`. Every fix was right and every one revealed the
next thing nobody could previously see.

The two failure modes are not the same and both are worth naming. A module-level import kills the process at
import time, before any code runs, and is what the smoke hit. A function-level import survives import and kills
the *call* instead, so it stays invisible until somebody exercises that path on Windows — worse, because it
looks fine until it does not.

**Why the engine keeps its own helper instead of reusing the kernel's.** The dependency runs one way: the kernel
must not import from the engine, so `pravrudhi_kernel/ledger/writer.py` has to keep its own copy. The engine can
have exactly one, and this is it. Two implementations total, forced by the dependency direction, rather than
four scattered try/excepts — which is the outcome that made this bug class survivable in the first place.

Selection is by which import actually succeeds, never by `sys.platform`. That is what ADR-0052 chose and for the
same reason: it lets a test prove the import path is safe on any machine by making `fcntl` fail to import,
rather than asserting against a platform string that is only a proxy for the thing that actually breaks.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised by monkeypatching the import, not by the platform
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt
except ImportError:  # pragma: no cover - the POSIX case, which every test run here takes
    msvcrt = None  # type: ignore[assignment]


class LockUnavailable(RuntimeError):
    """A non-blocking acquisition found the lock already held.

    Distinct from `RuntimeError` for "this platform has no lock mechanism": one means somebody else is working
    and the caller should say so and stop, the other means the machine cannot serialise at all and nothing
    should proceed. `loom_run` turns the first into its own `PipelineError`.
    """


@contextlib.contextmanager
def exclusive_lock(path: Path | str, *, blocking: bool = True) -> Iterator[None]:
    """Hold an exclusive lock on `path` for the duration of the block.

    `path` is a dedicated lock file, created if absent — never a file whose contents anyone reads. That matters
    on Windows, where locking is mandatory rather than advisory (a locked byte range refuses reads and writes
    from every other handle), so locking byte 0 of a file something else might read would deadlock a legitimate
    reader. The kernel's writer has to lock a far-off sentinel byte for exactly that reason because it locks the
    ledger itself; here the lock file has no readers and byte 0 is safe.

    Raises `LockUnavailable` when `blocking=False` and the lock is held, and `RuntimeError` when the platform
    offers neither mechanism — never silently proceeding unlocked, which would be worse than failing, since
    every caller uses this to serialise concurrent writes to a shared store.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        if fcntl is not None:
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            try:
                fcntl.flock(handle, flags)
            except BlockingIOError as exc:
                raise LockUnavailable(f"{path} is already locked") from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
        elif msvcrt is not None:
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(handle.fileno(), mode, 1)
            except OSError as exc:
                # LK_NBLCK raises OSError (EACCES/EDEADLOCK) rather than BlockingIOError when the range is held.
                raise LockUnavailable(f"{path} is already locked") from exc
            try:
                yield
            finally:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            raise RuntimeError(
                "no portable file lock available on this platform (neither fcntl nor msvcrt); refusing rather "
                "than proceeding unlocked, because every caller uses this to serialise concurrent writes"
            )
