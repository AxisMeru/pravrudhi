"""Every safeguard in update_apply must refuse cleanly, with no side effect, before it may ever apply anything."""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from pravrudhi.application.update_apply import (
    ApplyResult,
    UpdateConfig,
    apply,
    doctor_passes,
    install_release,
    load_config,
    prune_old_releases,
    rollback,
    run_in_progress,
    save_config,
    should_check,
    switch_current,
    updates_disabled,
    verify_digest,
)
from pravrudhi.application.updates import RELEASES_URL
from pravrudhi_kernel.schema import LedgerEvent


def make_event(seq: int, night: int, payload: dict[str, object], kind: str = "audit") -> str:
    ev = LedgerEvent(
        seq=seq,
        t="2026-01-01T00:00:00.000Z",
        epoch=0,
        night=night,
        cycle=None,
        kind=kind,  # type: ignore[arg-type]
        actor="kernel",
        candidate_id=None,
        surface=None,
        bucket=None,
        provenance=None,
        kernel_release="0.1.0",
        payload=payload,
        prev_hash="0" * 64,
        this_hash="0" * 64,
    )
    return ev.model_dump_json()


def write_ledger(root: Path, lines: list[str]) -> None:
    ledger = root / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")


def snapshot(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def success_runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")


def make_release_payload(tag: str, wheel_name: str, wheel_url: str, sums_url: str) -> dict[str, object]:
    return {
        "tag_name": tag,
        "assets": [
            {"name": wheel_name, "browser_download_url": wheel_url},
            {"name": "SHA256SUMS", "browser_download_url": sums_url},
        ],
    }


def fake_fetch(url_map: dict[str, bytes]) -> Callable[[str], bytes]:
    def fetch(url: str) -> bytes:
        return url_map[url]

    return fetch


def release_fixture(version: str) -> tuple[dict[str, bytes], str]:
    """A self-consistent (fetch url map, expected version) pair for one fake release."""
    wheel_bytes = f"contents-{version}".encode()
    wheel_name = f"pravrudhi-{version}-py3-none-any.whl"
    digest = hashlib.sha256(wheel_bytes).hexdigest()
    sums_text = f"{digest}  {wheel_name}\n"
    wheel_url = f"https://example/{version}/wheel"
    sums_url = f"https://example/{version}/sums"
    payload = make_release_payload(f"v{version}", wheel_name, wheel_url, sums_url)
    url_map = {
        RELEASES_URL: json.dumps(payload).encode("utf-8"),
        wheel_url: wheel_bytes,
        sums_url: sums_text.encode("utf-8"),
    }
    return url_map, version


# --- config ---------------------------------------------------------------


def test_load_config_defaults_when_missing(tmp_path: Path) -> None:
    expected = UpdateConfig(channel="release", auto_apply=False, check_interval_min=1440, keep_previous=2)
    assert load_config(tmp_path) == expected


def test_save_and_load_config_roundtrip(tmp_path: Path) -> None:
    cfg = UpdateConfig(channel="dev", auto_apply=True, check_interval_min=30, keep_previous=5)
    save_config(tmp_path, cfg)
    assert load_config(tmp_path) == cfg


def test_load_config_rejects_bad_channel(tmp_path: Path) -> None:
    path = tmp_path / ".pravrudhi" / "update.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("channel: nonsense\nauto_apply: true\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.channel == "release"
    assert cfg.auto_apply is True


# --- should_check -----------------------------------------------------------


def test_should_check_true_without_history(tmp_path: Path) -> None:
    assert should_check(tmp_path) is True


def test_should_check_false_within_interval(tmp_path: Path) -> None:
    save_config(tmp_path, UpdateConfig(check_interval_min=1440))
    path = tmp_path / ".pravrudhi" / "update-last-check"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")
    assert should_check(tmp_path) is False


def test_should_check_true_after_interval_elapses(tmp_path: Path) -> None:
    save_config(tmp_path, UpdateConfig(check_interval_min=1))
    path = tmp_path / ".pravrudhi" / "update-last-check"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time() - 3600), encoding="utf-8")
    assert should_check(tmp_path) is True


# --- safeguard 7: kill switch ------------------------------------------------


def test_updates_disabled_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAVRUDHI_NO_UPDATE", raising=False)
    assert updates_disabled() is False
    monkeypatch.setenv("PRAVRUDHI_NO_UPDATE", "1")
    assert updates_disabled() is True


def test_apply_kill_switch_is_a_pure_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_NO_UPDATE", "1")
    before = snapshot(tmp_path)
    result = apply(tmp_path)
    assert result.applied is False
    assert "PRAVRUDHI_NO_UPDATE" in result.reason
    assert snapshot(tmp_path) == before


# --- safeguard 1: run in progress -------------------------------------------


def test_run_in_progress_false_without_ledger(tmp_path: Path) -> None:
    assert run_in_progress(tmp_path) is False


def test_run_in_progress_true_while_night_open(tmp_path: Path) -> None:
    write_ledger(tmp_path, [make_event(0, night=1, payload={"kind": "night_start", "track": "lora"})])
    assert run_in_progress(tmp_path) is True


def test_run_in_progress_false_once_night_closed(tmp_path: Path) -> None:
    write_ledger(
        tmp_path,
        [
            make_event(0, night=1, payload={"kind": "night_start", "track": "lora"}),
            make_event(1, night=1, payload={"kind": "night_closed", "track": "lora"}),
        ],
    )
    assert run_in_progress(tmp_path) is False


def test_run_in_progress_tracks_are_independent(tmp_path: Path) -> None:
    write_ledger(
        tmp_path,
        [
            make_event(0, night=1, payload={"kind": "night_start", "track": "lora"}),
            make_event(1, night=1, payload={"kind": "night_closed", "track": "lora"}),
            make_event(2, night=1, payload={"kind": "night_start", "track": "harness"}),
        ],
    )
    assert run_in_progress(tmp_path) is True


def test_apply_refuses_and_leaves_no_side_effect_when_run_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PRAVRUDHI_NO_UPDATE", raising=False)
    write_ledger(tmp_path, [make_event(0, night=1, payload={"kind": "night_start", "track": "lora"})])
    before = snapshot(tmp_path)
    result = apply(tmp_path, channel="release")
    assert result.applied is False
    assert "run is in progress" in result.reason
    assert snapshot(tmp_path) == before


# --- safeguard 2: digest verification ---------------------------------------


def test_verify_digest_matching() -> None:
    data = b"hello world"
    digest = hashlib.sha256(data).hexdigest()
    assert verify_digest(data, "pkg.whl", f"{digest}  pkg.whl\n") is True


def test_verify_digest_mismatched() -> None:
    data = b"hello world"
    assert verify_digest(data, "pkg.whl", f"{'0' * 64}  pkg.whl\n") is False


def test_verify_digest_missing_filename() -> None:
    data = b"hello world"
    digest = hashlib.sha256(data).hexdigest()
    assert verify_digest(data, "pkg.whl", f"{digest}  other.whl\n") is False


def test_apply_release_refuses_on_digest_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRAVRUDHI_NO_UPDATE", raising=False)
    wheel_name = "pravrudhi-0.2.0-py3-none-any.whl"
    wheel_bytes = b"real wheel bytes"
    payload = make_release_payload("v0.2.0", wheel_name, "https://x/wheel", "https://x/sums")
    fetch = fake_fetch(
        {
            RELEASES_URL: json.dumps(payload).encode(),
            "https://x/wheel": wheel_bytes,
            "https://x/sums": f"{'0' * 64}  {wheel_name}\n".encode(),
        }
    )
    result = apply(tmp_path, channel="release", fetch=fetch, runner=success_runner)
    assert result.applied is False
    assert "SHA256 mismatch" in result.reason
    assert not (tmp_path / ".pravrudhi" / "releases").exists()


def test_apply_release_refuses_when_assets_missing(tmp_path: Path) -> None:
    payload = {"tag_name": "v0.2.0", "assets": []}
    fetch = fake_fetch({RELEASES_URL: json.dumps(payload).encode()})
    result = apply(tmp_path, channel="release", fetch=fetch, runner=success_runner)
    assert result.applied is False
    assert "wheel or SHA256SUMS" in result.reason
    assert not (tmp_path / ".pravrudhi" / "releases").exists()


# --- safeguard 3 & 4: fresh-directory install, gated by doctor --------------


def test_install_release_success(tmp_path: Path) -> None:
    release_dir = tmp_path / "rel"
    release_dir.mkdir()
    wheel_path = release_dir / "pkg.whl"
    wheel_path.write_bytes(b"x")
    ok, detail = install_release(release_dir, [wheel_path], success_runner)
    assert ok is True
    assert detail == ""


def test_install_release_failure_reports_detail(tmp_path: Path) -> None:
    release_dir = tmp_path / "rel"
    release_dir.mkdir()
    wheel_path = release_dir / "pkg.whl"
    wheel_path.write_bytes(b"x")

    def failing_runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no uv on PATH")

    ok, detail = install_release(release_dir, [wheel_path], failing_runner)
    assert ok is False
    assert "no uv on PATH" in detail


def test_doctor_passes_true_on_zero_exit(tmp_path: Path) -> None:
    release_dir = tmp_path / "rel"
    release_dir.mkdir()
    ok, _ = doctor_passes(release_dir, success_runner)
    assert ok is True


def test_doctor_passes_false_on_nonzero_exit(tmp_path: Path) -> None:
    release_dir = tmp_path / "rel"
    release_dir.mkdir()

    def failing_doctor(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="pools missing")

    ok, detail = doctor_passes(release_dir, failing_doctor)
    assert ok is False
    assert "pools missing" in detail


def test_apply_release_install_failure_rolls_back(tmp_path: Path) -> None:
    url_map, version = release_fixture("0.3.0")

    def failing_venv(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["uv", "venv"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="uv missing")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    result = apply(tmp_path, channel="release", fetch=fake_fetch(url_map), runner=failing_venv)
    assert result.applied is False
    assert result.rolled_back is True
    assert not (tmp_path / ".pravrudhi" / "releases" / version).exists()


def test_apply_release_doctor_failure_rolls_back_and_leaves_current_untouched(tmp_path: Path) -> None:
    url_map, version = release_fixture("0.4.0")

    def failing_doctor(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if cmd and cmd[-1] == "--version":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="doctor: pools missing")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    result = apply(tmp_path, channel="release", fetch=fake_fetch(url_map), runner=failing_doctor)
    assert result.applied is False
    assert result.rolled_back is True
    assert not (tmp_path / ".pravrudhi" / "releases" / version).exists()
    assert not (tmp_path / ".pravrudhi" / "releases" / "current").exists()


# --- release channel happy path, safeguard 5 (atomic switch + retention) ---


def test_apply_release_happy_path_switches_current(tmp_path: Path) -> None:
    url_map, version = release_fixture("0.2.0")
    result = apply(tmp_path, channel="release", fetch=fake_fetch(url_map), runner=success_runner)
    assert result.applied is True
    assert result.version == version
    assert result.reason == f"switched to {version}"
    current = tmp_path / ".pravrudhi" / "releases" / "current"
    assert current.is_symlink()
    assert current.resolve() == (tmp_path / ".pravrudhi" / "releases" / version).resolve()
    wheel_name = f"pravrudhi-{version}-py3-none-any.whl"
    assert (tmp_path / ".pravrudhi" / "releases" / version / wheel_name).read_bytes() == f"contents-{version}".encode()


def test_switch_current_is_atomic_and_replaceable(tmp_path: Path) -> None:
    first = tmp_path / ".pravrudhi" / "releases" / "1.0.0"
    first.mkdir(parents=True)
    switch_current(tmp_path, "1.0.0")
    current = tmp_path / ".pravrudhi" / "releases" / "current"
    assert current.resolve() == first.resolve()

    second = tmp_path / ".pravrudhi" / "releases" / "2.0.0"
    second.mkdir(parents=True)
    switch_current(tmp_path, "2.0.0")
    assert current.resolve() == second.resolve()


def test_prune_old_releases_keeps_current_and_n_previous(tmp_path: Path) -> None:
    releases_dir = tmp_path / ".pravrudhi" / "releases"
    for name in ["1.0.0", "2.0.0", "3.0.0"]:
        (releases_dir / name).mkdir(parents=True)
        time.sleep(0.01)
    switch_current(tmp_path, "3.0.0")
    prune_old_releases(tmp_path, keep_previous=1)
    remaining = {p.name for p in releases_dir.iterdir() if p.is_dir() and p.name != "current"}
    assert remaining == {"2.0.0", "3.0.0"}


def test_apply_release_prunes_across_successive_applies(tmp_path: Path) -> None:
    save_config(tmp_path, UpdateConfig(channel="release", keep_previous=1))
    for version in ["0.1.0", "0.2.0", "0.3.0"]:
        url_map, _ = release_fixture(version)
        result = apply(tmp_path, fetch=fake_fetch(url_map), runner=success_runner)
        assert result.applied is True
        time.sleep(0.01)
    releases_dir = tmp_path / ".pravrudhi" / "releases"
    remaining = {p.name for p in releases_dir.iterdir() if p.is_dir() and p.name != "current"}
    assert remaining == {"0.2.0", "0.3.0"}


def test_rollback_switches_to_previous_install(tmp_path: Path) -> None:
    save_config(tmp_path, UpdateConfig(channel="release", keep_previous=2))
    for version in ["0.1.0", "0.2.0"]:
        url_map, _ = release_fixture(version)
        assert apply(tmp_path, fetch=fake_fetch(url_map), runner=success_runner).applied is True
        time.sleep(0.01)
    result = rollback(tmp_path)
    assert result == ApplyResult(True, "0.1.0", "rolled back to 0.1.0", True)
    current = tmp_path / ".pravrudhi" / "releases" / "current"
    assert current.resolve() == (tmp_path / ".pravrudhi" / "releases" / "0.1.0").resolve()


def test_rollback_refuses_without_a_previous_install(tmp_path: Path) -> None:
    result = rollback(tmp_path)
    assert result.applied is False


# --- safeguard 6: nothing outside .pravrudhi/releases is touched -----------


def test_apply_release_does_not_touch_workspace_directories(tmp_path: Path) -> None:
    research = tmp_path / "research"
    research.mkdir()
    (research / "notes.md").write_text("keep me", encoding="utf-8")
    gates = tmp_path / "gates"
    gates.mkdir()
    (gates / "gate.yaml").write_text("keep me too", encoding="utf-8")
    objectives = tmp_path / "objectives"
    objectives.mkdir()
    (objectives / "obj.yaml").write_text("also keep", encoding="utf-8")
    touched = (research / "notes.md", gates / "gate.yaml", objectives / "obj.yaml")
    before = {p: p.read_text(encoding="utf-8") for p in touched}

    url_map, _ = release_fixture("0.5.0")
    result = apply(tmp_path, channel="release", fetch=fake_fetch(url_map), runner=success_runner)
    assert result.applied is True
    for path, content in before.items():
        assert path.read_text(encoding="utf-8") == content


# --- dev channel -------------------------------------------------------------


def test_apply_dev_refuses_on_dirty_tree_and_stops_early(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=" M dirty_file.py\n", stderr="")
        raise AssertionError(f"unexpected command after a dirty tree was found: {cmd}")

    result = apply(tmp_path, channel="dev", runner=runner)
    assert result.applied is False
    assert "dirty" in result.reason
    assert calls == [["git", "fetch", "origin", "main"], ["git", "status", "--porcelain"]]


def test_apply_dev_refuses_on_non_ff_pull(tmp_path: Path) -> None:
    def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["git", "fetch"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "status"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["git", "pull"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not possible to fast-forward")
        raise AssertionError(f"unexpected command: {cmd}")

    result = apply(tmp_path, channel="dev", runner=runner)
    assert result.applied is False
    assert "fast-forward" in result.reason


def test_apply_dev_refuses_on_red_smoke(tmp_path: Path) -> None:
    def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if cmd[:2] in (["git", "fetch"], ["git", "status"], ["git", "pull"], ["uv", "sync"]):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["make", "smoke"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="1 failed")
        raise AssertionError(f"unexpected command: {cmd}")

    result = apply(tmp_path, channel="dev", runner=runner)
    assert result.applied is False
    assert "smoke" in result.reason


def test_apply_dev_happy_path(tmp_path: Path) -> None:
    def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["git", "rev-parse", "--short"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="abc1234\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    result = apply(tmp_path, channel="dev", runner=runner)
    assert result.applied is True
    assert result.version == "abc1234"
    assert result.rolled_back is False


def test_apply_release_hands_the_installer_absolute_wheel_paths(tmp_path: Path, monkeypatch) -> None:
    """A relative root produced a wheel path relative to the workspace, which the install step, running from the
    release directory, could not find. Both end-user installs hit this on the first real release."""
    monkeypatch.chdir(tmp_path)
    version = "0.2.0"
    wheel_name = f"pravrudhi-{version}-py3-none-any.whl"
    data = b"wheel-bytes"
    digest = hashlib.sha256(data).hexdigest()
    url_map = {
        RELEASES_URL: json.dumps({
            "tag_name": f"v{version}",
            "assets": [
                {"name": wheel_name, "browser_download_url": "https://x/w"},
                {"name": "SHA256SUMS", "browser_download_url": "https://x/s"},
            ],
        }).encode(),
        "https://x/w": data,
        "https://x/s": f"{digest}  {wheel_name}\n".encode(),
    }
    seen: list[list[str]] = []

    def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="pravrudhi 0.2.0", stderr="")

    result = apply(Path("."), channel="release", fetch=fake_fetch(url_map), runner=runner)
    assert result.applied, result.reason
    install = next(c for c in seen if c[:3] == ["uv", "pip", "install"])
    assert all(Path(a).is_absolute() for a in install[4:]), install


def test_apply_release_is_idempotent_when_current_is_already_that_version(tmp_path: Path) -> None:
    version = "0.2.1"
    wheel_name = f"pravrudhi-{version}-py3-none-any.whl"
    data = b"wheel-bytes"
    digest = hashlib.sha256(data).hexdigest()
    url_map = {
        RELEASES_URL: json.dumps({
            "tag_name": f"v{version}",
            "assets": [
                {"name": wheel_name, "browser_download_url": "https://x/w"},
                {"name": "SHA256SUMS", "browser_download_url": "https://x/s"},
            ],
        }).encode(),
        "https://x/w": data,
        "https://x/s": f"{digest}  {wheel_name}\n".encode(),
    }
    calls: list[list[str]] = []

    def runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="pravrudhi 0.2.1", stderr="")

    first = apply(tmp_path, channel="release", fetch=fake_fetch(url_map), runner=runner)
    assert first.applied
    n = len(calls)
    second = apply(tmp_path, channel="release", fetch=fake_fetch(url_map), runner=runner)
    assert not second.applied and second.version == version and "already" in second.reason
    assert len(calls) == n, "a second apply must not reinstall"


def test_scheduler_wrapper_falls_back_when_the_flag_is_unknown(tmp_path: Path) -> None:
    """A scheduler must never depend on a flag newer than the engine it drives.

    `--if-due` shipped in 0.2.3; both end-user installs were running 0.2.1, so every scheduled run died on
    "No such option: --if-due" and neither machine could update itself again. The wrapper retries with the
    invocation that has always existed.
    """
    import os
    import subprocess as sp

    wrapper = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "release-update.sh"
    root = tmp_path / "install"
    (root / ".venv" / "bin").mkdir(parents=True)
    stub = root / ".venv" / "bin" / "pravrudhi"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'for a in "$@"; do if [ "$a" = "--if-due" ]; then echo "Error: No such option: --if-due"; exit 2; fi; done\n'
        'echo \'{"applied": false, "reason": "already at 0.2.1"}\'\n'
    )
    stub.chmod(0o755)
    out = sp.run(["bash", str(wrapper), str(root)], capture_output=True, text=True, env={**os.environ})
    assert out.returncode == 0
    assert "already at 0.2.1" in out.stdout
    assert "No such option" not in out.stdout.splitlines()[-1]


def test_the_scheduler_prefers_the_installed_release_over_the_bootstrap(tmp_path: Path) -> None:
    """The updater that runs must be the updater that was installed.

    The bootstrap venv only ever installs the first release; it is never upgraded. Calling it forever pinned the
    update logic to the version each machine started life with, so both end-user installs re-downloaded and
    re-installed the same release every thirty minutes: the bootstrap was 0.2.1 and the idempotency check
    arrived in 0.2.3.
    """
    import os
    import subprocess as sp

    wrapper = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "release-update.sh"
    root = tmp_path / "install"
    for rel, tag in ((Path(".venv"), "bootstrap"), (Path(".pravrudhi/releases/current/.venv"), "current")):
        d = root / rel / "bin"
        d.mkdir(parents=True)
        exe = d / "pravrudhi"
        exe.write_text(f"#!/usr/bin/env bash\necho '{{\"ran\": \"{tag}\"}}'\n")
        exe.chmod(0o755)

    out = sp.run(["bash", str(wrapper), str(root)], capture_output=True, text=True, env={**os.environ})
    assert out.returncode == 0
    assert '"ran": "current"' in out.stdout, out.stdout


def test_the_scheduler_falls_back_to_the_bootstrap_before_the_first_release(tmp_path: Path) -> None:
    import os
    import subprocess as sp

    wrapper = Path(__file__).resolve().parents[1] / "deploy" / "systemd" / "release-update.sh"
    root = tmp_path / "install"
    d = root / ".venv" / "bin"
    d.mkdir(parents=True)
    exe = d / "pravrudhi"
    exe.write_text('#!/usr/bin/env bash\necho \'{"ran": "bootstrap"}\'\n')
    exe.chmod(0o755)

    out = sp.run(["bash", str(wrapper), str(root)], capture_output=True, text=True, env={**os.environ})
    assert out.returncode == 0
    assert '"ran": "bootstrap"' in out.stdout, out.stdout


# --- Safeguard 5: the new engine must be able to read the work the user already has ------------------------


def test_a_workspace_with_no_ledger_is_nothing_to_break(tmp_path: Path) -> None:
    """A fresh install has no work yet, so there is nothing for an update to be incompatible with. The check
    must not refuse every first update on a machine that has never run a night."""
    from pravrudhi.application.update_apply import workspace_still_readable

    release_dir = tmp_path / "rel"
    release_dir.mkdir()

    def never_called(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        raise AssertionError("the new engine was asked to read a workspace that holds nothing")

    ok, detail = workspace_still_readable(release_dir, tmp_path / "empty", never_called)
    assert ok is True
    assert "no ledger" in detail


def test_a_ledger_the_new_engine_can_read_clears_the_safeguard(tmp_path: Path) -> None:
    from pravrudhi.application.update_apply import workspace_still_readable

    release_dir = tmp_path / "rel"
    release_dir.mkdir()
    root = tmp_path / "workspace"
    (root / "research").mkdir(parents=True)
    (root / "research" / "ledger.jsonl").write_text("{}\n")

    def reads_it(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout='{"chain_ok": true, "events": 12}', stderr="")

    ok, detail = workspace_still_readable(release_dir, root, reads_it)
    assert ok is True


def test_a_ledger_the_new_engine_cannot_read_refuses_the_update(tmp_path: Path) -> None:
    """The gap this closes: `doctor_passes` only proves the new install runs — it is handed a release directory
    with no workspace in it, by design. An engine that installs cleanly and cannot read the user's existing
    ledger passed every safeguard and broke their work on the next launch."""
    from pravrudhi.application.update_apply import workspace_still_readable

    release_dir = tmp_path / "rel"
    release_dir.mkdir()
    root = tmp_path / "workspace"
    (root / "research").mkdir(parents=True)
    (root / "research" / "ledger.jsonl").write_text("{}\n")

    def cannot_read_it(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unknown schema version 4")

    ok, detail = workspace_still_readable(release_dir, root, cannot_read_it)
    assert ok is False
    assert "unknown schema version 4" in detail


def test_a_broken_chain_refuses_rather_than_switching_onto_it(tmp_path: Path) -> None:
    """The new engine ran and answered, but says this workspace's chain does not verify. Switching to it would
    make the new version the one that appears to have broken the ledger."""
    from pravrudhi.application.update_apply import workspace_still_readable

    release_dir = tmp_path / "rel"
    release_dir.mkdir()
    root = tmp_path / "workspace"
    (root / "research").mkdir(parents=True)
    (root / "research" / "ledger.jsonl").write_text("{}\n")

    def chain_broken(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout='{"chain_ok": false, "events": 12}', stderr="")

    ok, detail = workspace_still_readable(release_dir, root, chain_broken)
    assert ok is False
    assert "chain" in detail.lower()


def test_output_that_is_not_json_is_refused_rather_than_assumed_fine(tmp_path: Path) -> None:
    from pravrudhi.application.update_apply import workspace_still_readable

    release_dir = tmp_path / "rel"
    release_dir.mkdir()
    root = tmp_path / "workspace"
    (root / "research").mkdir(parents=True)
    (root / "research" / "ledger.jsonl").write_text("{}\n")

    def gibberish(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="Traceback (most recent call last):", stderr="")

    ok, _ = workspace_still_readable(release_dir, root, gibberish)
    assert ok is False


def test_the_check_reads_and_never_writes(tmp_path: Path) -> None:
    """It runs before the switch, against the user's live workspace, so it must be a read. `status` is; an
    argument that made it write would put the new engine into the user's data before it was trusted."""
    from pravrudhi.application.update_apply import workspace_still_readable

    release_dir = tmp_path / "rel"
    release_dir.mkdir()
    root = tmp_path / "workspace"
    (root / "research").mkdir(parents=True)
    (root / "research" / "ledger.jsonl").write_text("{}\n")
    seen: list[list[str]] = []

    def record(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"chain_ok": true}', stderr="")

    workspace_still_readable(release_dir, root, record)
    assert seen and seen[0][1] == "status", f"expected a read-only status call, got {seen[0][1:]}"
    assert "--root" in seen[0] and str(root) in seen[0]


def test_apply_refuses_to_switch_when_the_new_version_cannot_read_the_workspace(tmp_path: Path) -> None:
    """End to end through `apply`, because a safeguard that is written and not wired protects nothing.

    The install succeeds, the new engine runs and imports — every earlier safeguard clears — and then it cannot
    read the ledger this machine has been filling. Before this the switch happened anyway and the user's work
    broke at the next launch.
    """
    url_map, version = release_fixture("0.5.0")
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    # An existing but empty ledger: enough for the safeguard to fire, and nothing for `run_in_progress` to
    # misread as a night still running.
    (tmp_path / "research" / "ledger.jsonl").write_text("")

    def cannot_read_the_workspace(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if "status" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unknown ledger schema version 4")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    result = apply(tmp_path, channel="release", fetch=fake_fetch(url_map), runner=cannot_read_the_workspace)

    assert result.applied is False
    assert result.rolled_back is True
    assert "unknown ledger schema version 4" in result.reason
    assert not (tmp_path / ".pravrudhi" / "releases" / version).exists()
    assert not (tmp_path / ".pravrudhi" / "releases" / "current").exists(), "the switch happened anyway"


def test_apply_switches_when_the_new_version_can_read_the_workspace(tmp_path: Path) -> None:
    """The other half: the safeguard must not be blocking every update on a machine that has a ledger."""
    url_map, version = release_fixture("0.6.0")
    (tmp_path / "research").mkdir(parents=True, exist_ok=True)
    (tmp_path / "research" / "ledger.jsonl").write_text("")

    def reads_the_workspace(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout='{"chain_ok": true, "events": 7}', stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    result = apply(tmp_path, channel="release", fetch=fake_fetch(url_map), runner=reads_the_workspace)

    assert result.applied is True, result.reason
    assert result.version == version
