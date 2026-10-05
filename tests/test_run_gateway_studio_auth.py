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
    assert 'studio_auth_guard || return 1' in text
    guard_at = text.index("studio_auth_guard || return 1")
    rm_at = text.index('docker rm -f "$name"', guard_at)
    assert 0 < rm_at - guard_at < 200  # the very next step is the removal: the refusal comes BEFORE the container is touched
    assert "printenv PRAVRUDHI_AUTH" in text and "studio_auth_readback" in text  # the running container's real value is read back


# -- the early-return path, the isolation of a Studio refusal, and the retried readback ------------------------------


def _functions(*names: str) -> str:
    text = SCRIPT.read_text()
    out = []
    for name in names:
        m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, flags=re.S | re.M)
        assert m, f"{name} is missing from run_gateway.sh"
        out.append(m.group(0))
    return "\n".join(out)


HARNESS = r"""
LOG="$WORK/calls.log"; : > "$LOG"
docker() {
  echo "docker $*" >> "$LOG"
  case "$1" in
    ps) printf '%s\n' "$RUNNING";;
    exec)
      local first
      first="$(head -1 "$WORK/exec.seq" 2>/dev/null || true)"
      sed -i 1d "$WORK/exec.seq" 2>/dev/null || true
      printf '%s\n' "$first";;
  esac
  return 0
}
sleep() { echo "sleep $*" >> "$LOG"; }
curl() { return 0; }
seq() { echo 1; }
port_of() { echo 18765; }
origin_of() { echo https://o.example.test; }
tunnel() { echo "tunnel $1" >> "$LOG"; }
PRAVRUDHI_VERSION=9.9.9; STATE="$WORK/state"; SUPABASE_URL=https://s.example.test; PRAVRUDHI_ADMINS=op-1
"""


def _run_script(
    tmp_path: Path, body: str, *, running: str = "", exec_seq: str = "", env_file: str | None = None
) -> tuple[subprocess.CompletedProcess[str], str]:
    work = tmp_path / "work"
    work.mkdir(parents=True)
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "chat.env").write_text(env_file or "")
    (work / "exec.seq").write_text(exec_seq)
    env = {"PATH": "/usr/bin:/bin", "WORK": str(work), "CONF": str(conf), "RUNNING": running, "STUDIO_READBACK_WAIT": "0"}
    funcs = _functions("studio_auth_guard", "studio_auth_readback", "ensure_engine", "bring_up_engines")
    r = subprocess.run([BASH or "bash", "-c", HARNESS + funcs + "\n" + body], env=env, capture_output=True, text=True)
    return r, (work / "calls.log").read_text()


SEQ = "required\nrequired\n"
STUDIO_UP = "pravrudhi-engine-studio pravrudhi-engine:9.9.9"
PRODUCT_UP = "pravrudhi-engine-product pravrudhi-engine:9.9.9"


def test_a_studio_already_running_with_auth_required_passes_and_registers(tmp_path: Path) -> None:
    r, log = _run_script(tmp_path, "ensure_engine studio; echo rc=$?", running=STUDIO_UP, exec_seq="required\n")
    assert "rc=0" in r.stdout and "docker rm" not in log


def test_the_early_return_path_also_checks_and_refuses_an_open_studio(tmp_path: Path) -> None:
    r, log = _run_script(tmp_path, "ensure_engine studio; echo rc=$?", running=STUDIO_UP, exec_seq="disabled\ndisabled\n")
    assert "rc=1" in r.stdout and "STUDIO REFUSED" in r.stderr
    assert "docker rm -f pravrudhi-engine-studio" in log and "docker run" not in log


def test_a_refused_studio_never_registers_its_tunnel_and_the_product_still_comes_up(tmp_path: Path) -> None:
    body = "RUNNING=\"$RUNNING\"; bring_up_engines; echo rc=$?"
    r, log = _run_script(tmp_path, body, running=f"{STUDIO_UP}\n{PRODUCT_UP}", exec_seq="disabled\ndisabled\n")
    assert "rc=1" in r.stdout  # non-zero at the end
    assert "tunnel studio" not in log and "tunnel product" in log
    assert "STUDIO NOT REGISTERED" in r.stderr and "docker rm -f pravrudhi-engine-product" not in log
    assert "docker stop" not in log


def test_a_studio_that_refuses_to_start_leaves_the_product_alone_too(tmp_path: Path) -> None:
    body = "bring_up_engines; echo rc=$?"
    r, log = _run_script(tmp_path, body, running=PRODUCT_UP, env_file="PRAVRUDHI_AUTH=disabled\n")
    assert "rc=1" in r.stdout and "REFUSING to start the Studio container" in r.stderr
    assert "tunnel studio" not in log and "tunnel product" in log
    assert "docker run" not in log and "docker rm -f pravrudhi-engine-product" not in log


def test_both_up_and_authenticated_registers_both_and_returns_zero(tmp_path: Path) -> None:
    both = f"{STUDIO_UP}\n{PRODUCT_UP}"
    r, log = _run_script(tmp_path, "bring_up_engines; echo rc=$?", running=both, exec_seq="required\n")
    assert "rc=0" in r.stdout and "tunnel studio" in log and "tunnel product" in log


def test_the_readback_is_retried_once_after_a_short_wait_before_tearing_down(tmp_path: Path) -> None:
    # first read empty (the exec raced the start), second read `required`: not torn down
    r, log = _run_script(tmp_path, "ensure_engine studio; echo rc=$?", running=STUDIO_UP, exec_seq="\nrequired\n")
    assert "rc=0" in r.stdout and "docker rm" not in log
    assert log.count("docker exec") == 2 and "sleep 0" in log
    # two bad reads: torn down, and not a third attempt
    r2, log2 = _run_script(tmp_path / "b", "ensure_engine studio; echo rc=$?", running=STUDIO_UP, exec_seq="\n\nrequired\n")
    assert "rc=1" in r2.stdout and log2.count("docker exec") == 2 and "docker rm -f pravrudhi-engine-studio" in log2


def test_the_main_body_exits_non_zero_after_a_refused_studio() -> None:
    text = SCRIPT.read_text()
    assert 'bring_up_engines || STUDIO_FAILED=1' in text
    assert "PRODUCT edition only" in text
    last = text.rstrip().splitlines()[-1]
    assert last.startswith("exit 1") and "Studio was refused" in last


def test_both_containers_are_started_with_auth_pinned_not_just_the_image_env(tmp_path: Path) -> None:
    """The common `docker run` line carries -e PRAVRUDHI_AUTH=required, after --env-file so it wins (product too)."""
    for edition in ("product", "studio"):
        body = f"ensure_engine {edition}; echo rc=$?"
        r, log = _run_script(tmp_path / edition, body, running="", exec_seq=SEQ)
        run_line = next((line for line in log.splitlines() if line.startswith("docker run")), "")
        assert run_line, f"{edition}: no docker run was made: {r.stderr}"
        assert "-e PRAVRUDHI_AUTH=required" in run_line, edition
        assert run_line.index("--env-file") < run_line.index("-e PRAVRUDHI_AUTH=required")  # an explicit -e overrides the file


def test_the_product_pin_is_documented_in_the_gateway_readme() -> None:
    readme = (SCRIPT.parent / "README.md").read_text()
    assert "-e PRAVRUDHI_AUTH=required" in readme and "BOTH containers" in readme
