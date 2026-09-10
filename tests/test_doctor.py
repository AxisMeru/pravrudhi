"""Exercise readiness against real temporary installations, including incomplete and damaged state."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pravrudhi import KERNEL_VERSION
from pravrudhi.application.doctor import run_doctor
from pravrudhi.application.init import PACKAGED_PREREG
from pravrudhi_kernel.ledger import LedgerWriter
from pravrudhi_kernel.metrics import seal_pool


@pytest.fixture
def ready_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "project"
    (root / ".pravrudhi").mkdir(parents=True)
    (root / ".pravrudhi" / "config.yaml").write_text("version: 1\n")
    LedgerWriter.open(root / "research" / "ledger.jsonl", KERNEL_VERSION)
    shutil.copytree(PACKAGED_PREREG, root / "research" / "prereg")
    seal_pool(root / ".pravrudhi" / "kernel" / "pools" / "test", "test", [{"question": "1+1", "answer": "2"}], {})
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return root


def test_uninitialised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("PATH", "")
    report = run_doctor(tmp_path)
    assert report["ok"] is False
    assert {check["name"] for check in report["checks"]} == {
        "initialised", "ledger", "docker", "gpu", "pools", "prereg", "routing", "loop_alive",
    }
    for check in report["checks"]:
        assert set(check) == {"name", "ok", "detail"}
        assert isinstance(check["detail"], str) and check["detail"]
        # Three checks stay ok on a bare machine and say why. No GPU on PATH cannot start a night but is not
        # an error; with no agent installed at all there is no route to judge (see `_routing_check`); and a
        # workspace that has never beaten is unobserved rather than stalled, which is the distinction
        # `loop_alive` has to draw or it would fail every fresh install.
        assert check["ok"] is (check["name"] in {"gpu", "routing", "loop_alive"})
    assert list(tmp_path.iterdir()) == []
    assert capsys.readouterr() == ("", "")


def test_initialised(ready_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = {p.relative_to(ready_root): p.read_bytes() for p in ready_root.rglob("*") if p.is_file()}
    report = run_doctor(ready_root)
    assert report["ok"] is True
    # Eight since 2026-09-10: `loop_alive` was added because `scheduler_fresh` had computed a dead loop all
    # along, published it, and been read correctly by a cloud routine -- while the command a session actually
    # runs in its first five minutes never asked. A workspace with no heartbeat has not stalled, so the check
    # passes here rather than failing every fresh install.
    assert len(report["checks"]) == 8
    assert [c for c in report["checks"] if c["name"] == "loop_alive"]
    assert all(check["ok"] is True and check["detail"] for check in report["checks"])
    assert before == {p.relative_to(ready_root): p.read_bytes() for p in ready_root.rglob("*") if p.is_file()}
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(("path", "failed"), [
    (".pravrudhi/config.yaml", {"initialised"}),
    ("research/ledger.jsonl", {"initialised", "ledger"}),
    (".pravrudhi/kernel/pools/test/manifest.json", {"pools"}),
    ("research/prereg/controller.yaml", {"prereg"}),
])
def test_missing_file(ready_root: Path, path: str, failed: set[str]) -> None:
    (ready_root / path).unlink()
    report = run_doctor(ready_root)
    assert report["ok"] is False
    assert {check["name"] for check in report["checks"] if not check["ok"]} == failed


def test_missing_docker(ready_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    report = run_doctor(ready_root)
    assert report["ok"] is False
    assert [check["name"] for check in report["checks"] if not check["ok"]] == ["docker"]
    docker_check = next(check for check in report["checks"] if check["name"] == "docker")
    assert "not installed" in docker_check["detail"]


def _write_docker_stub(bin_dir: Path, stderr: str) -> None:
    docker = bin_dir / "docker"
    docker.write_text(f"#!/bin/sh\necho '{stderr}' >&2\nexit 1\n")
    docker.chmod(0o755)


def test_docker_daemon_not_running(ready_root: Path, tmp_path: Path) -> None:
    _write_docker_stub(tmp_path / "bin", "Cannot connect to the Docker daemon. Is the docker daemon running?")
    report = run_doctor(ready_root)
    assert report["ok"] is False
    docker_check = next(check for check in report["checks"] if check["name"] == "docker")
    assert docker_check["ok"] is False
    assert "daemon is not running" in docker_check["detail"]
    assert "permission denied" not in docker_check["detail"].lower()


def test_docker_permission_denied(ready_root: Path, tmp_path: Path) -> None:
    _write_docker_stub(tmp_path / "bin", "Got permission denied while trying to connect to the Docker daemon socket")
    report = run_doctor(ready_root)
    assert report["ok"] is False
    docker_check = next(check for check in report["checks"] if check["name"] == "docker")
    assert docker_check["ok"] is False
    assert "permission denied" in docker_check["detail"].lower()
    assert "docker group" in docker_check["detail"]


def test_gpu_absent(ready_root: Path) -> None:
    report = run_doctor(ready_root)
    gpu_check = next(check for check in report["checks"] if check["name"] == "gpu")
    assert gpu_check["ok"] is True
    assert "no gpu detected" in gpu_check["detail"].lower()


@pytest.mark.parametrize("damage", ["tamper", "empty", "malformed", "encoding"])
def test_invalid_ledger(ready_root: Path, damage: str) -> None:
    ledger = ready_root / "research" / "ledger.jsonl"
    if damage == "tamper":
        event = json.loads(ledger.read_text())
        event["payload"]["kind"] = "changed"
        ledger.write_text(json.dumps(event) + "\n")
    else:
        ledger.write_bytes({"empty": b"", "malformed": b"not json\n", "encoding": b"\xff"}[damage])
    report = run_doctor(ready_root)
    assert report["ok"] is False
    failures = [check for check in report["checks"] if not check["ok"]]
    assert len(failures) == 1 and failures[0]["name"] == "ledger"
    if damage == "tamper":
        assert "this_hash mismatch" in failures[0]["detail"]


def test_routing_reports_a_tier_whose_chosen_route_has_no_runnable_agent(
    ready_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check that would have caught the outage of 2026-09-08.

    `systemd --user` gives a unit a PATH without nvm's bin directory, so `opencode` and `codex` were invisible to
    the heartbeat while `claude` was not. The router still chose the Lite Plan seat, because a route's tiers say
    nothing about whether its CLI is on PATH, and every dispatch was rejected with "no agent available" hourly for
    five hours. The unit reported success each time and `doctor` was green throughout, because nothing looked at
    whether the route the router would pick could actually run.
    """
    from pravrudhi.application import doctor as doctor_module

    # Exactly the shape of the outage: opencode is invisible, claude is not.
    monkeypatch.setattr(
        doctor_module, "_build_agent",
        lambda root, name, model: None if "opencode" in name else object(),
    )
    result = run_doctor(ready_root)
    routing = next(c for c in result["checks"] if c["name"] == "routing")
    assert not routing["ok"], routing
    assert "mechanical" in routing["detail"], routing["detail"]


def test_routing_is_satisfied_when_every_tier_can_run_what_it_chose(
    ready_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.application import doctor as doctor_module

    monkeypatch.setattr(doctor_module, "_build_agent", lambda root, name, model: object())
    result = run_doctor(ready_root)
    routing = next(c for c in result["checks"] if c["name"] == "routing")
    assert routing["ok"], routing


def test_routing_names_a_cli_that_exists_but_is_not_on_this_process_path(
    ready_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Say where the binary actually is, because "not installed" was the misleading part.

    On 2026-09-08 `opencode` was installed and worked perfectly in the operator's shell. It was invisible only to
    `systemd --user`, whose PATH omits nvm's bin directory. A report saying the CLI is not installed sends someone
    to reinstall a CLI that is already there. Naming the path it was found at turns the same failure into an
    obvious one, and this is the check that has to survive a move to a machine nobody has configured yet.
    """
    from pravrudhi.application import doctor as doctor_module

    elsewhere = tmp_path / "nvm" / "versions" / "node" / "v24.20.0" / "bin"
    elsewhere.mkdir(parents=True)
    (elsewhere / "opencode").write_text("#!/bin/sh\nexit 0\n")
    (elsewhere / "opencode").chmod(0o755)

    monkeypatch.setattr(doctor_module, "_build_agent", lambda root, name, model: None if "opencode" in name else object())
    monkeypatch.setattr(doctor_module, "_SEARCH_ROOTS", (elsewhere.parent.parent,))  # the node/ dir holding version dirs

    routing = next(c for c in run_doctor(ready_root)["checks"] if c["name"] == "routing")
    assert not routing["ok"], routing
    assert str(elsewhere / "opencode") in routing["detail"], routing["detail"]
    assert "not on this process's PATH" in routing["detail"], routing["detail"]
