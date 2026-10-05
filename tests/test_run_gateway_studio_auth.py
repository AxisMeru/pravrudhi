"""deploy/gateway/run_gateway.sh refuses to start a Studio container whose PRAVRUDHI_AUTH would not be `required`."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "deploy" / "gateway" / "run_gateway.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _guard() -> str:
    text = SCRIPT.read_text()
    m = re.search(r"^studio_auth_guard\(\) \{\n.*?^\}\n", text, flags=re.S | re.M)
    assert m, "studio_auth_guard is missing from run_gateway.sh"
    return m.group(0)


def _run(tmp_path: Path, env_file: str | None, gateway_auth: str | None) -> subprocess.CompletedProcess[str]:
    conf = tmp_path / "conf"
    conf.mkdir()
    if env_file is not None:
        (conf / "chat.env").write_text(env_file)
    env = {"PATH": "/usr/bin:/bin", "CONF": str(conf)}
    if gateway_auth is not None:
        env["PRAVRUDHI_AUTH"] = gateway_auth
    return subprocess.run([BASH or "bash", "-c", _guard() + "\nstudio_auth_guard"], env=env, capture_output=True, text=True)


@pytest.mark.parametrize("env_file", [None, "", "OTHER=1\n", "PRAVRUDHI_AUTH=required\n", "export PRAVRUDHI_AUTH=\"required\"\n",
                                       "PRAVRUDHI_AUTH='required'\n", "PRAVRUDHI_AUTH=disabled\nPRAVRUDHI_AUTH=required\n"])
def test_required_by_default_or_by_every_source_starts(tmp_path: Path, env_file: str | None) -> None:
    r = _run(tmp_path, env_file, None)
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("env_file", ["PRAVRUDHI_AUTH=disabled\n", "PRAVRUDHI_AUTH=optional\n", "PRAVRUDHI_AUTH=\n",
                                       "PRAVRUDHI_AUTH=required\nPRAVRUDHI_AUTH=disabled\n", "export PRAVRUDHI_AUTH='off'\n",
                                       "PRAVRUDHI_AUTH=Required\n"])
def test_a_container_env_file_that_weakens_it_refuses(tmp_path: Path, env_file: str) -> None:
    r = _run(tmp_path, env_file, None)
    assert r.returncode == 1 and "REFUSING" in r.stderr and "required" in r.stderr


@pytest.mark.parametrize("auth", ["disabled", "optional", "", "off"])
def test_a_gateway_environment_that_weakens_it_refuses(tmp_path: Path, auth: str) -> None:
    r = _run(tmp_path, None, auth)
    assert r.returncode == 1 and "REFUSING" in r.stderr


def test_the_studio_container_is_started_with_auth_pinned_and_read_back() -> None:
    text = SCRIPT.read_text()
    assert 'extra=(-e "PRAVRUDHI_AUTH=required"' in text  # an explicit -e wins over the env file
    assert 'studio_auth_guard || exit 1' in text
    guard_at, rm_at = text.index("studio_auth_guard || exit 1"), text.index('docker rm -f "$name" >/dev/null 2>&1 || true')
    assert guard_at < rm_at  # refuses BEFORE the running container is touched
    assert "printenv PRAVRUDHI_AUTH" in text  # the running container's real value is read back
