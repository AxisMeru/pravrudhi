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

from pathlib import Path

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


def test_the_floor_study_is_not_blocked_by_the_floor_it_replaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Found by running it, not by a test. The baseline-pairing check (ADR-0037) lives in
    `HarnessContext.__init__`, and the floor STUDY builds the same context -- so the study was refused by the
    staleness it exists to fix, and the error told the reader to run the command that had just failed.

    Safe to skip while measuring because `floor_dest` still refuses to overwrite another bench's floor on the
    way out, which is the loss that check protects against."""
    import json as J

    import pytest as P
    import yaml

    from pravrudhi.application.harness_track import HarnessContext

    # CI has no model cache; an empty snapshot directory satisfies the resolver, and nothing reads weights.
    (tmp_path / "hf" / "hub" / "models--Qwen--Qwen3-1.7B" / "snapshots" / "ci").mkdir(parents=True)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    prereg = tmp_path / "research" / "prereg"
    prereg.mkdir(parents=True)
    (prereg / "variance.json").write_text(J.dumps(
        {"bench": "mmlu-law-val", "sigma_seed": 0.02, "baseline_sha256": "stale" * 8}
    ))
    cfg = {
        "model": "Qwen/Qwen3-1.7B", "bench": "mmlu-law-val",
        "noise_floor": "research/prereg/variance.json",
        "boundary": {"alpha_eff": 0.05, "alpha_fut": 0.2, "k_max": 4, "sigma_mode": "adaptive",
                     "n0": 3, "delta_min_floor": 0.02, "min_n_confirm": 2},
    }
    yaml.safe_dump(cfg)  # the shape a prereg carries

    # A night is refused, and names re-measuring as the fix.
    with P.raises(ValueError, match="was measured at baseline"):
        HarnessContext(tmp_path, cfg, 1, lambda _: None)
    # The study that writes the floor is not.
    ctx = HarnessContext(tmp_path, cfg, 1, lambda _: None, measuring=True)
    assert ctx.measuring
    assert ctx.variance is None, "measuring must not adopt the stale floor either"
