"""Run one disposable container job: read-only mounts, no network, GPU on request, wall clock and peak VRAM
measured.

The kernel is the launcher; the job writes into exactly one output directory that the kernel created for it.

ADR-REF: ADR-0039. Every job is capped in RAM and in process count, because on 2026-09-10 they were not: an
uncapped noise-floor study on a 30GB desktop drove free memory to 260MB and the run queue past 200 for two
and a half hours. Nothing was OOM-killed -- the host simply thrashed until `sshd` could not be scheduled
promptly enough to write its protocol banner, so the box accepted TCP connections and answered ping while
being unreachable by every application. Only a hard power-cycle cleared it, and it wiped the run.

A cap makes a runaway job fail as a job. `memory-swap == memory` is the load-bearing half: with swap headroom
a container slides into the same slow thrash instead of failing, which is the failure this cap exists to
prevent rather than relocate.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from pydantic import Field

from pravrudhi_kernel.schema.common import KernelModel

#: Refuse to start a job when the host is already this close to the wall. Calibrated against the 2026-09-10
#: outage rather than guessed: `sar -r` shows MemAvailable at 7.5-8.2GiB across normal hours and 1.29GiB at
#: 11:50 when the collapse became irreversible, so 2GiB separates the two without touching a healthy host.
MIN_AVAIL_GIB = 2.0

#: Load average per CPU above which the host is not going to run another container usefully. Normal hours on
#: this box sit at 0.1-3.1 on 16 CPUs; the outage window reached 143 and then 215.
MAX_LOAD_PER_CPU = 4.0


class HostUnderPressure(RuntimeError):
    """The host cannot take another job right now.

    A refusal, not a failure. Starting a job into a host that is already thrashing is how a 2.5-hour outage
    happened: nothing was OOM-killed, so the box did not shed the load, it just stopped being able to schedule
    `sshd`. An aborted study is cheap next to a power-cycle, which wiped the study anyway.
    """


def host_pressure() -> tuple[bool, str]:
    """Whether the host can take a job, and why not if it cannot.

    Reads `/proc` rather than shelling out, so the check itself costs nothing on a host that is already
    struggling to schedule processes -- which is precisely when it runs.
    """
    try:
        meminfo = dict(
            (parts[0].rstrip(":"), float(parts[1]))
            for parts in (line.split() for line in Path("/proc/meminfo").read_text().splitlines())
            if len(parts) >= 2
        )
        avail_gib = meminfo.get("MemAvailable", 0.0) / (1024.0 * 1024.0)
        load1 = float(Path("/proc/loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        # Unreadable /proc is not evidence of pressure, and refusing every job on a platform without it would
        # be a worse failure than the one this guards against.
        return True, "host pressure unknown (/proc unreadable); proceeding"
    cpus = os.cpu_count() or 1
    if avail_gib < MIN_AVAIL_GIB:
        return False, f"only {avail_gib:.2f}GiB available, below the {MIN_AVAIL_GIB:g}GiB floor"
    if load1 > MAX_LOAD_PER_CPU * cpus:
        return False, f"load average {load1:.1f} over {cpus} CPUs, above {MAX_LOAD_PER_CPU:g}/CPU"
    return True, f"{avail_gib:.2f}GiB available, load {load1:.2f} over {cpus} CPUs"


#: Chosen to COMPOSE, which is the part that is easy to get wrong. This host has 30GiB and also runs a
#: long-lived training container capped at 12g (`~/rtx5090setup/docker/docker-compose.yml`), so two caps of
#: 20g each would each look prudent and together commit 40GiB -- reproducing the outage they were added to
#: prevent. 12 + 12 = 24 of 30, leaving the desktop session and the OS their headroom. A job that needs more
#: is asking for the machine to itself and must say so by setting `mem_gib` explicitly.
DEFAULT_MEM_GIB = 12.0

#: Enough for a scorer fanning out a few interpreters per item; far below the ~360 extra processes seen when
#: the host went down. Docker's own default is unlimited.
DEFAULT_PIDS_LIMIT = 512


class JobSpec(KernelModel):
    image: str
    command: list[str]
    mounts_ro: dict[str, str] = Field(default_factory=dict)  # host path -> container path
    output_dir: str  # host path, mounted rw at /out
    env: dict[str, str] = Field(default_factory=dict)
    gpu: bool = False
    network: bool = False
    timeout_s: int = Field(default=3600, ge=1)
    user: str | None = None  # "uid:gid"
    #: RAM ceiling. Defaults to a real number rather than "unlimited": a default of None would reproduce the
    #: outage the moment anyone forgot to set it, and forgetting is what happened.
    mem_gib: float = Field(default=DEFAULT_MEM_GIB, gt=0)
    #: Process ceiling inside the container. A scorer that runs candidate code out of process is the one job
    #: whose process count depends on what a model wrote, so it is the one that needs a bound.
    pids_limit: int = Field(default=DEFAULT_PIDS_LIMIT, ge=16)


class JobResult(KernelModel):
    exit_code: int
    wall_s: float
    peak_gib_smi: float | None
    stdout_tail: str
    stderr_tail: str
    timed_out: bool


def docker_available() -> bool:
    return shutil.which("docker") is not None and subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def _smi_poll(stop: threading.Event, peak: list[float]) -> None:
    while not stop.is_set():
        try:
            out = (
                subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                .stdout.strip()
                .splitlines()
            )
            used = max(float(x) for x in out) / 1024.0 if out else 0.0
            peak[0] = max(peak[0], used)
        except (subprocess.SubprocessError, ValueError, OSError):
            pass
        stop.wait(0.5)


def run_job(spec: JobSpec) -> JobResult:
    ok, why = host_pressure()
    if not ok:
        raise HostUnderPressure(
            f"refusing to start {spec.image} on a host under pressure: {why}. A per-container cap bounds one "
            f"job; it does not stop a second being stacked on a host that is already at the wall, which is "
            f"what ADR-0039 records."
        )
    out = Path(spec.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["docker", "run", "--rm", "-v", f"{out}:/out:rw"]
    for host, cont in spec.mounts_ro.items():
        cmd += ["-v", f"{host}:{cont}:ro"]
    if not spec.network:
        cmd += ["--network", "none"]
    if spec.gpu:
        cmd += ["--gpus", "all"]
    if spec.user:
        cmd += ["--user", spec.user]
    # Both, always. `--memory` alone lets the container borrow swap and thrash instead of failing, which is
    # the exact collapse ADR-0039 records rather than a milder version of it.
    cmd += [f"--memory={spec.mem_gib:g}g", f"--memory-swap={spec.mem_gib:g}g"]
    cmd += [f"--pids-limit={spec.pids_limit}"]
    for k, v in spec.env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [spec.image, *spec.command]
    stop, peak = threading.Event(), [0.0]
    t = threading.Thread(target=_smi_poll, args=(stop, peak), daemon=True) if spec.gpu else None
    if t:
        t.start()
    t0 = time.monotonic()
    timed_out = False
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=spec.timeout_s)
        code, so, se = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        timed_out, code = True, 124
        so, se = (
            (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            (e.stderr or b"").decode() if isinstance(e.stderr, bytes) else (e.stderr or ""),
        )
    wall = time.monotonic() - t0
    if t:
        stop.set()
        t.join(timeout=2)
    return JobResult(
        exit_code=code,
        wall_s=wall,
        peak_gib_smi=(peak[0] if spec.gpu else None),
        stdout_tail=so[-4000:],
        stderr_tail=se[-4000:],
        timed_out=timed_out,
    )
