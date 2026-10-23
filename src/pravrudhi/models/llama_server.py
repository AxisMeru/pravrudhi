"""Lifecycle of the local llama.cpp server container (deliberation window only).

Started, health-checked, stopped.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from pravrudhi.models.openai_compat import ChatClient

IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda"


class LlamaServer:
    """Starts and stops a local llama.cpp server container for the proposer model and gives a chat client for it."""
    def __init__(
        self,
        gguf: Path,
        *,
        name: str = "pravrudhi-proposer",
        port: int = 8080,
        ctx: int = 32768,
        n_gpu_layers: int = 999,
        parallel: int = 1,
        extra: list[str] | None = None,
    ) -> None:
        self.gguf = Path(gguf).resolve()  # HF snapshots are symlinks into blobs/; the mount must contain the real file
        self.name, self.port, self.ctx, self.n_gpu_layers, self.parallel = (
            name,
            port,
            ctx,
            n_gpu_layers,
            parallel,
        )
        self.extra = extra or []
        self.client = ChatClient(f"http://127.0.0.1:{port}/v1", model=self.gguf.name)

    def start(self, wait_s: int = 300) -> ChatClient:
        self.stop()
        cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            self.name,
            "--gpus",
            "all",
            "-p",
            f"127.0.0.1:{self.port}:8080",
            "-v",
            f"{self.gguf.parent}:/models:ro",
            IMAGE,
            "-m",
            f"/models/{self.gguf.name}",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "-c",
            str(self.ctx),
            "-ngl",
            str(self.n_gpu_layers),
            "--parallel",
            str(self.parallel),
            "--flash-attn",
            "on",
            *self.extra,
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        t0 = time.monotonic()
        while time.monotonic() - t0 < wait_s:
            if self.client.healthy():
                return self.client
            time.sleep(2)
        logs = subprocess.run(["docker", "logs", "--tail", "40", self.name], capture_output=True, text=True).stdout
        self.stop()
        raise RuntimeError(f"llama-server did not become healthy in {wait_s}s:\n{logs[-2000:]}")

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)

    def running(self) -> bool:
        out = subprocess.run(
            ["docker", "ps", "--filter", f"name=^{self.name}$", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        ).stdout
        return out.strip() == self.name
