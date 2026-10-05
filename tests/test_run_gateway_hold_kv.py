"""#273: STUDIO_HOLD_KV=1 starts the Studio tunnel but does not write engine_url_studio to KV (a blank key kept as
containment survives a gateway restart). Default behaviour is unchanged; the product is never held."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "deploy" / "gateway" / "run_gateway.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

HARNESS = r"""
LOG="$WORK/calls.log"; : > "$LOG"
LOGS="$WORK/logs"; mkdir -p "$LOGS"
cloudflared() { echo "https://quiet-tunnel-name.trycloudflare.com"; echo "Registered tunnel connection"; sleep 30; }
curl() { echo "curl $*" >> "$LOG"; return 0; }
sleep() { :; }
seq() { echo 1; }
port_of() { echo 18765; }
auth=(-H "Authorization: Bearer cf-token-value")
API=https://api.example.test; CF_KV_ID=kv123; PIDS=()
"""


def _tunnel_fn() -> str:
    m = re.search(r"^tunnel\(\) \{\n.*?^\}\n", SCRIPT.read_text(), flags=re.S | re.M)
    assert m, "tunnel() is missing from run_gateway.sh"
    return m.group(0)


def _run(tmp_path: Path, edition: str, **env: str) -> tuple[subprocess.CompletedProcess[str], str]:
    work = tmp_path / "w"
    work.mkdir()
    full = {"PATH": "/usr/bin:/bin", "WORK": str(work), **env}
    body = HARNESS + _tunnel_fn() + f"\ntunnel {edition}; echo rc=$?; kill $(jobs -p) 2>/dev/null; true"
    r = subprocess.run([BASH or "bash", "-c", body], env=full, capture_output=True, text=True, timeout=60)
    return r, (work / "calls.log").read_text()


def test_by_default_the_studio_url_is_written_to_its_kv_key(tmp_path: Path) -> None:
    r, log = _run(tmp_path, "studio")
    assert "rc=0" in r.stdout
    assert "curl" in log and "values/engine_url_studio" in log and "https://quiet-tunnel-name.trycloudflare.com" in log
    assert "-> KV engine_url_studio" in r.stdout and "HELD" not in r.stdout


def test_with_the_hold_flag_nothing_is_written_and_the_url_is_logged(tmp_path: Path) -> None:
    r, log = _run(tmp_path, "studio", STUDIO_HOLD_KV="1")
    assert "rc=0" in r.stdout
    assert log.strip() == ""  # no curl at all: no KV write
    assert "KV write HELD (STUDIO_HOLD_KV=1)" in r.stdout and "https://quiet-tunnel-name.trycloudflare.com" in r.stdout
    assert "engine_url_studio not written" in r.stdout
    assert "cf-token-value" not in r.stdout + r.stderr


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "11", " 1"])
def test_only_the_exact_value_one_holds(tmp_path: Path, value: str) -> None:
    r, log = _run(tmp_path, "studio", STUDIO_HOLD_KV=value)
    assert "values/engine_url_studio" in log and "HELD" not in r.stdout


@pytest.mark.parametrize("upstream", ["", "runpod"])
def test_the_product_is_never_held_and_keeps_its_runpod_rollback_key(tmp_path: Path, upstream: str) -> None:
    env = {"STUDIO_HOLD_KV": "1"}
    if upstream:
        env["PRODUCT_UPSTREAM"] = upstream
    r, log = _run(tmp_path, "product", **env)
    key = "engine_url_product_rollback" if upstream == "runpod" else "engine_url_product"
    assert f"values/{key}" in log and "HELD" not in r.stdout


def test_the_studio_auth_guard_from_266_is_intact_and_documented() -> None:
    text = SCRIPT.read_text()
    assert "studio_auth_guard || return 1" in text and "studio_auth_readback" in text and "bring_up_engines" in text
    assert "STUDIO_HOLD_KV=1" in text.split("REBUILD_IMAGE", 1)[0]  # documented in the header comment


def test_the_hold_is_documented_where_a_restart_would_otherwise_drop_it() -> None:
    gateway = SCRIPT.read_text().split("REBUILD_IMAGE", 1)[0]
    assert "gateway.env" in gateway and "restart checklist" in gateway and "Environment=STUDIO_HOLD_KV=1" in gateway
    unit = (SCRIPT.parent / "pravrudhi-gateway.service").read_text()
    assert "#Environment=STUDIO_HOLD_KV=1" in unit and "gateway.env" in unit  # an example, commented out: no behaviour change
