"""RUN-METADATA sidecar helper (R1 review, PR #6, liaison bd481ca): `nvidia_smi_snapshot`/`models_response`/
`local_container_state` must fail SOFT (return None, never raise) when the underlying command or endpoint is
unavailable -- a CPU-only CI host has no `nvidia-smi` binary at all, so an unguarded subprocess call would
raise `FileNotFoundError` and break every script that calls this at run time.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pytest
from _t2_run_metadata import (  # type: ignore[import-not-found]  # noqa: E402
    _run,
    local_container_state,
    models_response,
    nvidia_smi_snapshot,
)


def test_nvidia_smi_snapshot_returns_none_when_the_binary_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **kw: object) -> None:
        raise FileNotFoundError("nvidia-smi not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert nvidia_smi_snapshot() is None


def test_run_returns_none_on_a_nonzero_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    assert _run(["some-command"]) is None


def test_run_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **kw: object) -> None:
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=10)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert _run(["nvidia-smi"]) is None


def test_run_strips_and_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        returncode = 0
        stdout = "  12532 MiB, 32607 MiB, 0 %\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _Result())
    assert _run(["nvidia-smi"]) == "12532 MiB, 32607 MiB, 0 %"


def test_models_response_returns_none_when_the_endpoint_is_unreachable() -> None:
    # No mock server at this port -- a real connection failure, not a monkeypatch, exercising the actual
    # except-Exception fallback in models_response.
    assert models_response("http://127.0.0.1:1") is None


def test_local_container_state_returns_none_when_docker_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **kw: object) -> None:
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(subprocess, "run", _raise)
    assert local_container_state("vllm-judge") is None
