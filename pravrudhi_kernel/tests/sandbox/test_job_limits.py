"""Every container job is bounded in RAM and process count, and refuses to start on a host at the wall.

ADR-REF: ADR-0039. On 2026-09-10 a `JobSpec` carried no memory limit, an uncapped noise-floor study drove
this 30GiB host's free memory to 260MB and its run queue past 200, and nothing was OOM-killed -- the box
thrashed for two and a half hours until `sshd` could no longer be scheduled promptly enough to write its
protocol banner. It answered ping and completed TCP handshakes while being unreachable by every application.
A hard power-cycle was the only recovery and it wiped the run.

These tests pin the three properties that stop that recurring, and the composition rule that is the easy one
to get wrong.
"""

from __future__ import annotations

import pytest

from pravrudhi_kernel.sandbox.runner import (
    DEFAULT_MEM_GIB,
    DEFAULT_PIDS_LIMIT,
    MAX_LOAD_PER_CPU,
    MIN_AVAIL_GIB,
    HostUnderPressure,
    JobSpec,
    host_pressure,
    run_job,
)

TRAIN_CONTAINER_CAP_GIB = 12.0  # ~/rtx5090setup/docker/docker-compose.yml `mem_limit`
HOST_RAM_GIB = 30.0


def _spec(**kw: object) -> JobSpec:
    return JobSpec(image="x", command=["y"], output_dir="/tmp/out", **kw)  # type: ignore[arg-type]


def test_a_job_is_capped_by_default_not_only_when_asked() -> None:
    """A default of `None` meaning unlimited would reproduce the outage the moment anyone forgot to set it,
    and forgetting is exactly what happened."""
    assert _spec().mem_gib == DEFAULT_MEM_GIB
    assert _spec().pids_limit == DEFAULT_PIDS_LIMIT
    assert DEFAULT_MEM_GIB > 0
    with pytest.raises(ValueError):
        _spec(mem_gib=0)
    with pytest.raises(ValueError):
        _spec(mem_gib=-1)


def test_the_two_caps_on_this_host_compose() -> None:
    """The property a per-container limit does NOT give you for free. Two caps of 20g -- the figure first
    proposed -- would each look prudent and together commit 40GiB on a 30GiB box, reproducing the outage they
    were added to prevent. Headroom is for the desktop session and the OS, which is what starved."""
    assert DEFAULT_MEM_GIB + TRAIN_CONTAINER_CAP_GIB < HOST_RAM_GIB
    assert HOST_RAM_GIB - (DEFAULT_MEM_GIB + TRAIN_CONTAINER_CAP_GIB) >= 4.0


def test_the_command_sets_memory_and_swap_to_the_same_value() -> None:
    """`--memory` alone lets a container borrow swap and thrash instead of failing. That is the collapse this
    guards against rather than a milder version of it, so the two flags must agree."""
    import subprocess

    seen: dict[str, list[str]] = {}

    def fake_run(cmd, **kw):  # type: ignore[no-untyped-def]
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    real = subprocess.run
    subprocess.run = fake_run  # type: ignore[assignment]
    try:
        run_job(_spec(mem_gib=3, pids_limit=64))
    finally:
        subprocess.run = real  # type: ignore[assignment]

    cmd = seen["cmd"]
    assert "--memory=3g" in cmd
    assert "--memory-swap=3g" in cmd, "swap headroom is how a cap becomes slow thrash instead of a failure"
    assert "--pids-limit=64" in cmd


def test_host_pressure_reads_the_live_host_and_explains_itself() -> None:
    ok, why = host_pressure()
    assert isinstance(ok, bool)
    assert why, "a refusal a reader cannot act on is not better than no refusal"
    # The thresholds separate this box's normal hours (7.5-8.2GiB available, load 0.1-3.1 on 16 CPUs) from
    # the outage window (1.29GiB available, load 143 rising to 215). Calibrated, not guessed.
    assert 0.5 <= MIN_AVAIL_GIB <= 4.0
    assert 2.0 <= MAX_LOAD_PER_CPU <= 8.0


def test_a_job_is_refused_rather_than_stacked_onto_a_host_at_the_wall(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-container cap bounds ONE job. It does nothing about a second being stacked on a host already at
    the wall, which is the shape the outage actually had -- several heavy things overlapping, no single
    runaway."""
    monkeypatch.setattr(
        "pravrudhi_kernel.sandbox.runner.host_pressure", lambda: (False, "only 1.29GiB available")
    )
    with pytest.raises(HostUnderPressure, match="1.29GiB"):
        run_job(_spec())
