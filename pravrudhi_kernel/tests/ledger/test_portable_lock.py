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
