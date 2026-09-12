"""ADR-0052: the kernel must import when `fcntl` is absent (the Windows condition), and the
portable lock helpers must still behave exactly like the old bare `fcntl.flock` calls on POSIX.

The absent-fcntl case runs in a subprocess: reloading `pravrudhi_kernel.ledger.writer` in-process
mints new `LedgerWriter`/`ChainBroken` class objects, which breaks identity for every other test
module that already imported the pre-reload classes (isinstance/except checks silently stop
matching). A subprocess proves the same thing without touching this process's module registry."""
import os
import subprocess
import sys

from pravrudhi_kernel.ledger import writer as writer_module


def test_import_survives_fcntl_absent() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.modules['fcntl'] = None; "
            "import pravrudhi_kernel.ledger.writer as w; "
            "assert w.fcntl is None",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_lock_exclusive_and_unlock_roundtrip_on_this_platform(tmp_path) -> None:
    p = tmp_path / "lockable"
    p.write_bytes(b"")
    fd = os.open(p, os.O_RDWR)
    try:
        writer_module._lock_exclusive(fd)
        writer_module._unlock(fd)
    finally:
        os.close(fd)


class _FakeMsvcrt:
    """Models the one Windows semantic that matters here: `locking()` locks/unlocks the byte range
    starting at the file's CURRENT position, and an unlock must name the same range as its matching
    lock (real msvcrt raises PermissionError otherwise). This is what caught the first attempt at
    the msvcrt branch: it locked, wrote under O_APPEND (which moves the position to end-of-file),
    then tried to unlock from the new position - a different range - and Windows refused it."""

    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self) -> None:
        self._locked_at: int | None = None

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        pos = os.lseek(fd, 0, os.SEEK_CUR)
        if mode == self.LK_LOCK:
            self._locked_at = pos
        else:
            if self._locked_at != pos:
                raise PermissionError(13, "Permission denied")
            self._locked_at = None


def test_msvcrt_unlock_matches_lock_position_despite_intervening_append_write(tmp_path, monkeypatch) -> None:
    """Reproduces the real Windows failure (ADR-0052 follow-up, windows-import-smoke run on e78032e:
    PermissionError from every LedgerWriter.append) on this platform, via a fake msvcrt modelling its
    position-matching rule, so this class of bug is caught here rather than on the next Windows CI round-trip."""
    monkeypatch.setattr(writer_module, "fcntl", None)
    monkeypatch.setattr(writer_module, "msvcrt", _FakeMsvcrt())
    p = tmp_path / "ledger.jsonl"
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    try:
        writer_module._lock_exclusive(fd)
        os.write(fd, b"a line, as append() would between lock and unlock\n")
        writer_module._unlock(fd)
    finally:
        os.close(fd)
