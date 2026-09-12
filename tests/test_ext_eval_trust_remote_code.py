"""scripts/ext_eval.sh: trust_remote_code is an explicit, off-by-default opt-in.

Never invokes a real GPU job: `docker` is shadowed on PATH by a fake executable that only
records the arguments it was called with and exits 0, so this never runs `--gpus all` or
downloads/loads a real model. A fake HF_HOME snapshot directory satisfies the script's own
`ls -d .../snapshots/*/` lookup.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EXT_EVAL = REPO_ROOT / "scripts" / "ext_eval.sh"

_FAKE_DOCKER = """#!/usr/bin/env bash
# Records the argv it was called with (one per line) and exits 0. Never runs anything real.
# Prints one benign stdout line so ext_eval.sh's `| grep -vE "Warning|warn" | tail -25`
# (pipefail-sensitive) has a non-matching line to pass through instead of failing on empty
# input, and writes a minimal results_*.json into the mounted /out dir (the last -v mount
# always names the host $OUT directory) so the script's own "did lm-eval produce a results
# file" tail-end check exercises its real (non-empty) path, same as a genuine run would.
printf '%s\\n' "$@" > "$FAKE_DOCKER_LOG"
echo "fake docker ok"
for arg in "$@"; do
  case "$arg" in
    *:/out:rw) host_out="${arg%%:*}" ;;
  esac
done
echo '{"results": {}}' > "$host_out/results_1.json"
exit 0
"""


def _run(tmp_path: Path, extra_args: list[str]) -> str:
    hf_home = tmp_path / "hf_home"
    snap = hf_home / "hub" / "models--fake--model" / "snapshots" / "abc123"
    snap.mkdir(parents=True)

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    docker_shim = fake_bin / "docker"
    docker_shim.write_text(_FAKE_DOCKER)
    docker_shim.chmod(docker_shim.stat().st_mode | stat.S_IEXEC)

    out_dir = tmp_path / "out"
    log = tmp_path / "docker_argv.log"

    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["HF_HOME"] = str(hf_home)
    env["FAKE_DOCKER_LOG"] = str(log)

    subprocess.run(
        ["bash", str(EXT_EVAL), "fake/model", "sometask", str(out_dir), "", "", "", *extra_args],
        env=env, cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    return log.read_text()


def test_trust_remote_code_off_by_default(tmp_path):
    argv = _run(tmp_path, extra_args=[])
    assert "trust_remote_code=False" in argv
    assert "trust_remote_code=True" not in argv


def test_trust_remote_code_explicit_opt_in(tmp_path):
    argv = _run(tmp_path, extra_args=["true"])
    assert "trust_remote_code=True" in argv


def test_trust_remote_code_explicit_opt_out(tmp_path):
    argv = _run(tmp_path, extra_args=["false"])
    assert "trust_remote_code=False" in argv
