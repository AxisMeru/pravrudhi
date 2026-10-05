"""A dispatched agent must not outlive its dispatch.

A coding-agent CLI is a launcher: it spawns a sandbox helper which spawns the work. Killing only the direct child
leaves the grandchildren alive, still talking to the provider and still billing. Eight such orphans were found
running at once on this machine, the oldest three hours after its task had returned a verdict and had its work
merged, and nothing in any log distinguished a finished dispatch from a still-running agent.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from pravrudhi.agents.cli_agents import _run


def _alive(pid: int) -> bool:
    """True while the process can still run. A killed process whose parent never reaps it (PID 1 in a container
    without an init) stays a zombie: `os.kill(pid, 0)` still succeeds for it, but it holds no work and bills nothing."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    try:
        # comm may contain spaces/parens; the state is the first field after the LAST ")".
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    except OSError:  # no /proc (non-Linux): fall back to the signal probe above
        return True
    return state not in ("Z", "X")


def test_a_zombie_is_not_alive_but_a_running_process_is() -> None:
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    try:
        assert _alive(sleeper.pid)
        sleeper.kill()
        for _ in range(50):  # killed but not yet waited on: a zombie
            if Path(f"/proc/{sleeper.pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                break
            time.sleep(0.05)
        assert not _alive(sleeper.pid)
    finally:
        sleeper.wait()


def test_a_timed_out_agent_takes_its_children_with_it(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild.pid"
    # A launcher that spawns a long-lived grandchild and then waits, exactly the shape of a sandboxed agent CLI.
    script = (
        "import os, subprocess, sys, time, pathlib\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(p.pid))\n"
        "time.sleep(600)\n"
    )
    code, _out, err, _wall = _run([sys.executable, "-c", script], tmp_path, timeout_s=3)
    assert code == 124 and "timeout after 3s" in err

    pid = int(marker.read_text())
    for _ in range(50):  # the group signal is delivered asynchronously
        if not _alive(pid):
            break
        time.sleep(0.1)
    assert not _alive(pid), "the grandchild survived its launcher's timeout and would keep billing"


def test_an_agent_that_finishes_normally_still_reports_its_output(tmp_path: Path) -> None:
    code, out, _err, wall = _run([sys.executable, "-c", "print('done')"], tmp_path, timeout_s=30)
    assert code == 0 and out.strip() == "done" and wall >= 0.0


def test_the_agent_runs_in_its_own_process_group(tmp_path: Path) -> None:
    """Without a new session the group signal would reach this test process too."""
    code, out, _err, _wall = _run([sys.executable, "-c", "import os; print(os.getpgrp(), os.getpid())"],
                                  tmp_path, timeout_s=30)
    pgrp, pid = (int(x) for x in out.split())
    assert code == 0 and pgrp == pid, "the agent must lead its own group so the group can be reaped"
    assert pgrp != os.getpgrp()


def test_reap_is_safe_on_a_process_that_has_already_gone(tmp_path: Path) -> None:
    from pravrudhi.agents.cli_agents import _reap

    proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    proc.wait()
    _reap(proc)  # must not raise
    assert signal.SIGKILL is not None
