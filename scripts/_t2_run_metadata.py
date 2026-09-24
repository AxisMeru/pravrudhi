"""Shared RUN-METADATA sidecar writer (Lead-2's rule, 2026-09-24, after R2 held the C3-harness raw sign on a
served-model-revision gap): every T2 run script writes this AT RUN TIME, not reconstructed after the fact.
Covers: served model id + full `/v1/models` response, the local judge container's StartedAt/RestartCount
when discoverable, this script's own commit sha, concurrency/delay as configured, start/end timestamps, and
`nvidia-smi` before/after. Every field states plainly what was actually read vs. what could not be determined
here (e.g. this container's own git commit sha before it has been committed).
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def nvidia_smi_snapshot() -> str | None:
    return _run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader"]
    )


def models_response(base_url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=10) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode())
            return data
    except Exception:  # noqa: BLE001 -- metadata collection must never fail the run itself
        return None


def local_container_state(container_name: str) -> dict[str, str] | None:
    """`docker inspect` is read-only -- StartedAt/RestartCount for a locally-named container, when this host
    can see it. Returns None (never guesses) when docker isn't available or the container isn't found."""
    out = _run(["docker", "inspect", container_name, "--format", "{{.State.StartedAt}}\t{{.RestartCount}}"])
    if not out or "\t" not in out:
        return None
    started_at, restart_count = out.split("\t", 1)
    return {"started_at": started_at, "restart_count": restart_count}


def script_commit_sha(script_path: Path) -> str | None:
    """This script's own commit sha, if it has been committed (never fabricated for an uncommitted script --
    a None here is meaningful: the run predates the commit, same caveat T1/T2's earlier metadata notes)."""
    return _run(["git", "-C", str(script_path.parent), "log", "-1", "--format=%H", "--", script_path.name])


class RunMetadata:
    """Collect fields across a run's lifetime and write one sidecar file at the end. `start()` captures
    what's known before any live call; `finish()` adds what's only known after."""

    def __init__(
        self, *, script_path: Path, base_url: str, container_name: str | None,
        concurrency_description: str, delay_s: float,
    ) -> None:
        self.script_path = script_path
        self.base_url = base_url
        self.container_name = container_name
        self.concurrency_description = concurrency_description
        self.delay_s = delay_s
        self.data: dict[str, Any] = {}

    def start(self) -> None:
        self.data["start_utc"] = datetime.now(UTC).isoformat()
        self._t0 = time.monotonic()
        self.data["script_path"] = str(self.script_path)
        self.data["script_commit_sha"] = script_commit_sha(self.script_path) or (
            "NOT YET COMMITTED at run time -- this run predates any commit of this script"
        )
        self.data["base_url"] = self.base_url
        self.data["models_response_before"] = models_response(self.base_url)
        self.data["nvidia_smi_before"] = nvidia_smi_snapshot()
        if self.container_name:
            self.data["container_state"] = local_container_state(self.container_name) or (
                f"docker inspect {self.container_name!r} unavailable or container not found on this host"
            )
        else:
            self.data["container_state"] = "no local container name given (backend may not be a local container)"
        self.data["concurrency"] = self.concurrency_description
        self.data["inter_call_delay_s"] = self.delay_s

    def finish(self, *, extra: dict[str, Any] | None = None) -> None:
        self.data["end_utc"] = datetime.now(UTC).isoformat()
        self.data["elapsed_s"] = time.monotonic() - self._t0
        self.data["nvidia_smi_after"] = nvidia_smi_snapshot()
        self.data["models_response_after"] = models_response(self.base_url)
        if extra:
            self.data.update(extra)

    def write(self, out_path: Path) -> None:
        out_path.write_text(json.dumps(self.data, indent=2, default=str))
