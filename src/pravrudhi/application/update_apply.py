"""Applying an update, not just noticing one.

`updates.py` only ever looks: it can tell an operator a newer release exists, but it never touches the checkout,
because an engine that can start GPU work must not replace its own code without the operator saying so. Once the
operator does say so - `auto_apply: true` in `.pravrudhi/update.yaml`, or a direct call - something has to actually
fetch the release, verify it, install it somewhere that cannot corrupt the running process, and only then switch
over. Every step here exists because skipping it was the failure mode: applying while a night is running would pull
the rug out from under a live subprocess; installing over the current interpreter would make failure unrecoverable;
trusting a downloaded wheel without checking its digest would run whatever GitHub - or a compromised release asset -
served; switching to a build that cannot pass its own doctor would swap a working engine for a broken one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from pravrudhi.application.updates import RELEASES_URL
from pravrudhi_kernel.ledger.verify import iter_events

Channel = Literal["dev", "release"]

FetchFn = Callable[[str], bytes]
RunnerFn = Callable[[list[str], Path], "subprocess.CompletedProcess[str]"]

FETCH_TIMEOUT_S = 10.0
_NIGHT_START = "night_start"
_NIGHT_CLOSED_KINDS = frozenset({"night_closed", "night_aborted"})


@dataclass(frozen=True)
class UpdateConfig:
    channel: Channel = "release"
    auto_apply: bool = False
    check_interval_min: int = 1440
    keep_previous: int = 2


@dataclass(frozen=True)
class ApplyResult:
    applied: bool
    version: str | None
    reason: str
    rolled_back: bool = False


def _config_path(root: Path) -> Path:
    return root / ".pravrudhi" / "update.yaml"


def _last_check_path(root: Path) -> Path:
    return root / ".pravrudhi" / "update-last-check"


def _releases_dir(root: Path) -> Path:
    return root / ".pravrudhi" / "releases"


def _release_dir(root: Path, version: str) -> Path:
    return _releases_dir(root) / version


def _current_symlink(root: Path) -> Path:
    return _releases_dir(root) / "current"


def _coerce_channel(value: Any) -> Channel:
    return value if value in ("dev", "release") else "release"


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_config(root: Path) -> UpdateConfig:
    """The operator's update policy, or the safe defaults (release channel, no auto-apply) if unset."""
    path = _config_path(root)
    if not path.is_file():
        return UpdateConfig()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    data = raw if isinstance(raw, dict) else {}
    return UpdateConfig(
        channel=_coerce_channel(data.get("channel")),
        auto_apply=bool(data.get("auto_apply", False)),
        check_interval_min=_coerce_int(data.get("check_interval_min"), 1440),
        keep_previous=_coerce_int(data.get("keep_previous"), 2),
    )


def save_config(root: Path, config: UpdateConfig) -> None:
    path = _config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "channel": config.channel,
        "auto_apply": config.auto_apply,
        "check_interval_min": config.check_interval_min,
        "keep_previous": config.keep_previous,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def should_check(root: Path) -> bool:
    """Whether check_interval_min has elapsed since the last time an update check actually ran."""
    path = _last_check_path(root)
    if not path.is_file():
        return True
    try:
        last = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    config = load_config(root)
    elapsed_minutes = (time.time() - last) / 60.0
    return elapsed_minutes >= config.check_interval_min


def _record_check(root: Path) -> None:
    path = _last_check_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")


def updates_disabled() -> bool:
    """Safeguard 7: the operator's kill switch. Checked before anything else touches the filesystem."""
    return os.environ.get("PRAVRUDHI_NO_UPDATE") == "1"


def run_in_progress(root: Path) -> bool:
    """Safeguard 1: refuse to apply while a night is still open for any track.

    update_apply runs as its own process (a CLI invocation, or a periodic background check) and never has a
    reference to the live `RunManager` a running API server might hold, so the only durable signal is the ledger
    it already writes to: a `night_start` audit event for a track with no closing event recorded after it.
    """
    ledger = root / "research" / "ledger.jsonl"
    if not ledger.exists():
        return False
    open_nights: dict[str, int | None] = {}
    for ev in iter_events(ledger):
        if ev.kind != "audit":
            continue
        payload = ev.payload
        kind = payload.get("kind")
        track = str(payload.get("track") or "lora")
        if kind == _NIGHT_START:
            open_nights[track] = ev.night
        elif kind in _NIGHT_CLOSED_KINDS and open_nights.get(track) == ev.night:
            open_nights[track] = None
    return any(night is not None for night in open_nights.values())


def verify_digest(data: bytes, filename: str, sha256sums_text: str) -> bool:
    """Safeguard 2 (core check): does `data`'s digest match the line for `filename` in a SHA256SUMS listing."""
    digest = hashlib.sha256(data).hexdigest()
    for line in sha256sums_text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        recorded_digest, name = parts
        if name.lstrip("*").strip() == filename:
            return recorded_digest.lower() == digest.lower()
    return False


def _default_fetch(url: str) -> bytes:
    # The same token the check uses. Authenticating one of the two callers was worse than neither: the status
    # check reported the new release while the apply that downloads it kept answering 403, so the engine could
    # see an update it could never install.
    from pravrudhi.application.updates import _github_token, github_opener

    headers = {"Accept": "application/vnd.github+json", "User-Agent": "pravrudhi-update-apply"}
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    # This function downloads release assets, and an asset URL always redirects to a CDN host — so following
    # redirects with a credential attached would hand the operator's token to whatever served the bytes. The
    # opener drops it the moment the host stops being GitHub's.
    with github_opener().open(request, timeout=FETCH_TIMEOUT_S) as response:  # noqa: S310 (fixed GitHub hosts)
        result: bytes = response.read()
        return result


def _default_runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _pick_assets(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Every wheel the release carries, and its SHA256SUMS.

    A release is two wheels, not one: the engine depends on `pravrudhi-kernel`, a workspace member that is not on
    PyPI, so an install of the engine wheel alone fails to resolve. Picking only the first `.whl` made the
    safeguard refuse every real release.
    """
    assets = payload.get("assets")
    if not isinstance(assets, list):
        return [], None
    wheels = [a for a in assets if isinstance(a, dict) and str(a.get("name", "")).endswith(".whl")]
    sums = next((a for a in assets if isinstance(a, dict) and str(a.get("name", "")) == "SHA256SUMS"), None)
    return wheels, sums


def _version_from_tag(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def install_release(release_dir: Path, wheel_paths: list[Path], runner: RunnerFn) -> tuple[bool, str]:
    """Safeguard 3: a fresh venv for this version, never the interpreter currently running this process."""
    venv_dir = release_dir / ".venv"
    result = runner(["uv", "venv", str(venv_dir)], release_dir)
    if result.returncode != 0:
        return False, f"uv venv failed: {(result.stderr or result.stdout).strip()}"
    python = venv_dir / "bin" / "python"
    result = runner(["uv", "pip", "install", "--python", str(python), *[str(w) for w in wheel_paths]], release_dir)
    if result.returncode != 0:
        return False, f"uv pip install failed: {(result.stderr or result.stdout).strip()}"
    return True, ""


def doctor_passes(release_dir: Path, runner: RunnerFn) -> tuple[bool, str]:
    """Safeguard 4: the new install must answer for itself before we ever switch to it.

    `pravrudhi doctor` judges a workspace (ledger, pools, pre-registration), and a freshly installed release has
    none of those, so it failed on every good install. What the switch needs to know is that the install runs:
    the console script starts and the app surface imports. Both are checked from the new venv, nothing else.
    """
    bin_dir = release_dir / ".venv" / "bin"
    result = runner([str(bin_dir / "pravrudhi"), "--version"], release_dir)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    version_line = result.stdout.strip()
    result = runner([str(bin_dir / "python"), "-c", "import pravrudhi.api.server, pravrudhi.cli.app"], release_dir)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout).strip()
    return True, version_line


def workspace_still_readable(release_dir: Path, root: Path, runner: RunnerFn) -> tuple[bool, str]:
    """Safeguard 5: the new engine must be able to read the work this machine already has.

    `doctor_passes` proves the new install *runs* — its console script starts and its modules import — and it is
    handed a release directory with no workspace in it, by design. Nothing checked that the new version could
    still read the ledger the user has been filling. An engine that installed cleanly and could not open that
    ledger would clear every safeguard, switch, and break the user's work at the next launch. That is precisely
    what an unattended update must not do: the point of updating a product is that what it is building keeps
    building.

    So the new engine is pointed at the real workspace before the switch, and must both run and report a
    verifying chain. `status` is a read: it opens the ledger, folds it and prints. Nothing here writes, because
    this happens while the old version is still the installed one and the new version is not yet trusted.

    A workspace with no ledger is not a refusal. A machine that has never run a night has nothing an update
    could be incompatible with, and refusing there would block every first update.
    """
    ledger = Path(root) / "research" / "ledger.jsonl"
    if not ledger.exists():
        return True, "no ledger yet: nothing this update could be incompatible with"

    result = runner([str(release_dir / ".venv" / "bin" / "pravrudhi"), "status", "--root", str(root)], release_dir)
    if result.returncode != 0:
        return False, f"the new version could not read this workspace: {(result.stderr or result.stdout).strip()}"
    try:
        reported = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False, f"the new version answered unreadably: {result.stdout.strip()[:200]}"
    if reported.get("chain_ok") is not True:
        return False, "the new version reports this workspace's ledger chain does not verify"
    return True, f"the new version reads this workspace: {reported.get('events', 0)} events"


def _current_version(root: Path) -> str | None:
    """The version `current` points at, or None.

    Without this, every scheduled check re-downloaded and re-installed the release it was already running: the
    end-user installs on both machines reported "switched to 0.2.1" every thirty minutes.
    """
    link = _current_symlink(root)
    if not link.is_symlink():
        return None
    return Path(os.readlink(link)).name


def switch_current(root: Path, version: str) -> None:
    """Safeguard 5: an atomic symlink swap, so `current` never points at a half-written directory."""
    releases_dir = _releases_dir(root)
    releases_dir.mkdir(parents=True, exist_ok=True)
    target = _release_dir(root, version)
    tmp_link = releases_dir / f".current.tmp-{uuid.uuid4().hex}"
    tmp_link.symlink_to(target, target_is_directory=True)
    os.replace(tmp_link, _current_symlink(root))


def prune_old_releases(root: Path, keep_previous: int) -> None:
    """Safeguard 5 (retention): drop installs beyond `current` plus the `keep_previous` most recent others."""
    releases_dir = _releases_dir(root)
    if not releases_dir.is_dir():
        return
    current_link = _current_symlink(root)
    current_target = current_link.resolve() if current_link.is_symlink() else None
    others = sorted(
        (p for p in releases_dir.iterdir() if p.is_dir() and p.name != "current" and p.resolve() != current_target),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in others[keep_previous:]:
        shutil.rmtree(stale, ignore_errors=True)


def rollback(root: Path) -> ApplyResult:
    root = Path(root).resolve()
    """Switch `current` back to the most recent previous install that `prune_old_releases` kept around."""
    releases_dir = _releases_dir(root)
    if not releases_dir.is_dir():
        return ApplyResult(False, None, "no releases directory: nothing to roll back to", False)
    current_link = _current_symlink(root)
    current_target = current_link.resolve() if current_link.is_symlink() else None
    candidates = sorted(
        (p for p in releases_dir.iterdir() if p.is_dir() and p.name != "current" and p.resolve() != current_target),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return ApplyResult(False, None, "no previous release available to roll back to", False)
    previous = candidates[0]
    switch_current(root, previous.name)
    return ApplyResult(True, previous.name, f"rolled back to {previous.name}", True)


def _apply_release(root: Path, config: UpdateConfig, fetch: FetchFn, runner: RunnerFn) -> ApplyResult:
    _record_check(root)
    try:
        payload = json.loads(fetch(RELEASES_URL).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 (a failed check must refuse, never crash the caller)
        return ApplyResult(False, None, f"could not reach the GitHub releases API: {exc}", False)
    if not isinstance(payload, dict):
        return ApplyResult(False, None, "malformed release payload from GitHub", False)
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag:
        return ApplyResult(False, None, "release payload is missing tag_name", False)
    version = _version_from_tag(tag)
    if _current_version(root) == version:
        return ApplyResult(False, version, f"already at {version}", False)

    wheel_assets, sums_asset = _pick_assets(payload)
    if not wheel_assets or sums_asset is None:
        return ApplyResult(False, None, f"release {tag} is missing a wheel or SHA256SUMS asset", False)

    try:
        sums_text = fetch(sums_asset["browser_download_url"]).decode("utf-8")
        wheels = [(str(a["name"]), fetch(a["browser_download_url"])) for a in wheel_assets]
    except Exception as exc:  # noqa: BLE001 (same: refuse, don't crash)
        return ApplyResult(False, None, f"could not download release assets: {exc}", False)

    for wheel_name, wheel_bytes in wheels:
        if not verify_digest(wheel_bytes, wheel_name, sums_text):
            return ApplyResult(False, None, f"SHA256 mismatch for {wheel_name}: refusing to install", False)

    release_dir = _release_dir(root, version)
    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True)
    wheel_paths: list[Path] = []
    for wheel_name, wheel_bytes in wheels:
        (release_dir / wheel_name).write_bytes(wheel_bytes)
        wheel_paths.append(release_dir / wheel_name)

    installed, install_detail = install_release(release_dir, wheel_paths, runner)
    if not installed:
        shutil.rmtree(release_dir, ignore_errors=True)
        return ApplyResult(False, None, f"install failed, rolled back: {install_detail}", True)

    doctor_ok, doctor_detail = doctor_passes(release_dir, runner)
    if not doctor_ok:
        shutil.rmtree(release_dir, ignore_errors=True)
        return ApplyResult(False, None, f"doctor failed on the new install, refusing to switch: {doctor_detail}", True)

    readable, readable_detail = workspace_still_readable(release_dir, root, runner)
    if not readable:
        shutil.rmtree(release_dir, ignore_errors=True)
        return ApplyResult(False, None, f"refusing to switch: {readable_detail}", True)

    switch_current(root, version)
    prune_old_releases(root, config.keep_previous)
    return ApplyResult(True, version, f"switched to {version}", False)


def _apply_dev(root: Path, runner: RunnerFn) -> ApplyResult:
    _record_check(root)
    fetched = runner(["git", "fetch", "origin", "main"], root)
    if fetched.returncode != 0:
        return ApplyResult(False, None, f"git fetch failed: {(fetched.stderr or fetched.stdout).strip()}", False)

    status = runner(["git", "status", "--porcelain"], root)
    if status.returncode != 0:
        return ApplyResult(False, None, f"git status failed: {(status.stderr or status.stdout).strip()}", False)
    if status.stdout.strip():
        return ApplyResult(False, None, "refusing to update: working tree is dirty", False)

    pulled = runner(["git", "pull", "--ff-only", "origin", "main"], root)
    if pulled.returncode != 0:
        detail = (pulled.stderr or pulled.stdout).strip()
        return ApplyResult(False, None, f"refusing to update: pull was not fast-forward ({detail})", False)

    synced = runner(["uv", "sync"], root)
    if synced.returncode != 0:
        return ApplyResult(False, None, f"uv sync failed: {(synced.stderr or synced.stdout).strip()}", False)

    smoked = runner(["make", "smoke"], root)
    if smoked.returncode != 0:
        detail = (smoked.stderr or smoked.stdout).strip()
        return ApplyResult(False, None, f"make smoke failed, refusing to update: {detail}", False)

    revision = runner(["git", "rev-parse", "--short", "HEAD"], root)
    version = revision.stdout.strip() if revision.returncode == 0 else None
    return ApplyResult(True, version, "dev checkout updated to latest main", False)


def apply(
    root: Path, *, channel: Channel | None = None, fetch: FetchFn | None = None, runner: RunnerFn | None = None
) -> ApplyResult:
    """Apply an update if every safeguard clears; otherwise refuse and leave the checkout untouched.

    The root is resolved first. With the default root `.` every path under it was relative, and the install
    step, which runs `uv pip install` from inside the release directory, was handed a wheel path relative to the
    workspace and looked for `.pravrudhi/releases/<v>/.pravrudhi/releases/<v>/…`. The first real release-channel
    apply on both end-user installs failed that way; the safeguard rolled back, as it should, but nothing could
    ever have been applied.
    """
    root = Path(root).resolve()
    if updates_disabled():
        return ApplyResult(False, None, "PRAVRUDHI_NO_UPDATE=1: automatic updates are disabled", False)
    if run_in_progress(root):
        return ApplyResult(False, None, "refusing to update: a run is in progress", False)

    config = load_config(root)
    use_channel = channel or config.channel
    runner = runner or _default_runner
    if use_channel == "dev":
        return _apply_dev(root, runner)
    return _apply_release(root, config, fetch or _default_fetch, runner)
