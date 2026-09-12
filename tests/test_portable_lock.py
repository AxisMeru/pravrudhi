"""ADR-0052's fix was applied to one file; the class of bug it fixed was never swept. This is the sweep.

`fcntl` is POSIX-only. ADR-0052 made `pravrudhi_kernel/ledger/writer.py` portable because a bare top-level
`import fcntl` there made the kernel — and the whole CLI that imports it transitively — unimportable on Windows.
Nobody then checked whether the same pattern existed elsewhere, and it did: `application/requests.py` imports
`fcntl` at module level, and `application/loom_run.py` imports it inside a function.

v0.1.7's Windows packaged smoke found the first one the only way it could be found, by getting far enough to
run the engine. Three releases in one evening each peeled a layer: the kernel could not import, then the
harness could not unpack, then the desktop app could not locate the engine, and now the engine itself dies on
`import fcntl` at `application/requests.py:25`. Each fix was correct and each revealed the next.

The two failure modes differ and both matter. A module-level import kills the process at import time, before
any code runs — that is the crash in the smoke. A function-level import survives import and kills the call
instead, so it is invisible until someone exercises that path on Windows. Only the first is currently
observable, which is exactly why the second needs a test rather than a bug report.

Why the engine gets its own helper rather than reusing the kernel's: the dependency runs one way. The kernel
cannot import from the engine, so `writer.py` must keep its own copy; the engine can and should have exactly
one. Two implementations total, forced by the dependency direction, rather than four scattered try/excepts.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


class TestTheEngineImportsWithoutFcntl:
    """The condition that actually broke Windows: can these modules be imported at all with `fcntl` absent?

    Simulated by making the import fail rather than by checking `sys.platform`, which is the same technique
    ADR-0052 used for the kernel — it lets the import path be proven on any OS, and it tests the real
    mechanism (an ImportError at module load) rather than a proxy for it.
    """

    @staticmethod
    def _import_in_subprocess_without_fcntl(module: str) -> subprocess.CompletedProcess[str]:
        """Import `module` in a fresh interpreter where `fcntl` cannot be imported.

        A subprocess, rather than `importlib.reload` with the import hooked in-process. The first version of
        this file did the latter and broke thirteen unrelated tests: `reload` rebinds a module's CLASS objects,
        so every other test module that had already done `from ...requests import RequestError` was left
        holding the *previous* class, and `pytest.raises(RequestError)` stopped matching the exception actually
        raised. Restoring by reloading again does not help — the other modules' references are already stale.
        The failure mode was invisible per-file and only appeared in a whole-suite run, which is the second
        time in one evening that a test passing in isolation was not evidence.

        A subprocess also tests the real thing rather than a simulation of it: a genuinely fresh interpreter
        importing the module from scratch, which is exactly what a Windows machine does.
        """
        # `find_spec`, not `find_module`: the latter was removed from the import system in Python 3.12 and a
        # finder defining only it is silently ignored, so the first version of this blocker was a no-op and
        # both tests here passed VACUOUSLY — they would have passed with the bug still in place. Caught by
        # checking that the blocker could actually block before trusting the green, which is the same check
        # that has to be made of any test whose whole purpose is to catch a regression.
        code = (
            "import sys\n"
            "class Blocker:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name == 'fcntl':\n"
            "            raise ImportError(\"No module named 'fcntl'\")\n"
            "        return None\n"
            "sys.meta_path.insert(0, Blocker())\n"
            "sys.modules.pop('fcntl', None)\n"
            "try:\n"
            "    import fcntl\n"
            "except ImportError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('blocker failed: fcntl was importable, so this proves nothing')\n"
            f"import {module}\n"
            "print('ok')\n"
        )
        return subprocess.run(  # noqa: S603 - fixed argv, no shell, test-only
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
        )

    def test_requests_imports_with_fcntl_absent(self) -> None:
        """`application/requests.py:25` was a bare top-level `import fcntl`. This is the v0.1.7 crash."""
        done = self._import_in_subprocess_without_fcntl("pravrudhi.application.requests")
        assert done.returncode == 0, f"engine module is unimportable without fcntl:\n{done.stderr[-2000:]}"

    def test_loom_run_imports_with_fcntl_absent(self) -> None:
        done = self._import_in_subprocess_without_fcntl("pravrudhi.application.loom_run")
        assert done.returncode == 0, f"engine module is unimportable without fcntl:\n{done.stderr[-2000:]}"


class TestPortableLockWorksOnThisPlatform:
    def test_an_exclusive_lock_is_taken_and_released(self, tmp_path: Path) -> None:
        from pravrudhi.application.portable_lock import exclusive_lock

        path = tmp_path / "a.lock"
        with exclusive_lock(path):
            assert path.exists()
        with exclusive_lock(path):
            pass  # a second acquisition after release must not block or raise

    def test_a_non_blocking_lock_refuses_rather_than_waits(self, tmp_path: Path) -> None:
        """`loom_run` needs LOCK_NB semantics: a pipeline record already in use must fail fast, not queue."""
        from pravrudhi.application.portable_lock import LockUnavailable, exclusive_lock

        path = tmp_path / "b.lock"
        with exclusive_lock(path), pytest.raises(LockUnavailable), exclusive_lock(path, blocking=False):
            pass

    def test_it_refuses_rather_than_silently_not_locking_when_no_mechanism_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A platform with neither mechanism must raise, never pretend. Silently not locking is worse than
        failing: the callers use this to serialise concurrent writes to a shared JSON store."""
        from pravrudhi.application import portable_lock

        monkeypatch.setattr(portable_lock, "fcntl", None)
        monkeypatch.setattr(portable_lock, "msvcrt", None)
        with pytest.raises(RuntimeError, match="no portable file lock"), \
                portable_lock.exclusive_lock(tmp_path / "c.lock"):
            pass


class TestTheCallSitesUseIt:
    def test_requests_locked_still_serialises(self, tmp_path: Path) -> None:
        """The behaviour `requests.locked` existed for must survive the portability change."""
        from pravrudhi.application import requests

        req = requests.capture(tmp_path, "ask", request_id="r-lock")
        with requests.locked(tmp_path):
            pass
        assert requests.get(tmp_path, req.id) is not None

    def test_no_module_level_posix_only_import_remains_in_the_engine(self) -> None:
        """The sweep itself, as a test: this is what stops the next one being found by a Windows user.

        A module-level POSIX-only import anywhere under `src/pravrudhi/` breaks the engine on Windows at import
        time, which is the failure that cost three releases to reach. Guarded imports (inside a try/except, or
        indented in a function) are fine and are not matched here.
        """
        import re

        root = Path(__file__).resolve().parents[1] / "src" / "pravrudhi"
        offenders = [
            f"{p.relative_to(root)}:{n}"
            for p in root.rglob("*.py")
            for n, line in enumerate(p.read_text().splitlines(), 1)
            if re.match(r"^import (fcntl|termios|pwd|grp|resource)\b", line)
        ]
        assert offenders == [], f"module-level POSIX-only imports break the engine on Windows: {offenders}"
