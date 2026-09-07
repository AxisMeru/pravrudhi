"""What an installed engine can say about itself, and about its neighbours on the same machine.

Two end-user installs exist to prove the update path works — one on this machine and one on a Mac — and until
now the only way to know their state was to SSH in and read `.pravrudhi/update.yaml` and `.pravrudhi/releases/`
by hand, which defeats the point of having them: an install worth trusting is one you can ask, not one you have
to visit. `hosts/fleet.py` already solved the adjacent problem for *machines* (name, transport, measured
capabilities); this module solves it for *installs on this host* — the developer checkout and the packaged
release directory an operator downloaded, both of which `update_apply.py` already knows how to read and write.

Everything here is a filesystem read of state `update_apply.py` already owns: which channel and auto-apply
policy is configured (`load_config`), which version `current` points at (`_current_symlink`), which versions are
sitting in `.pravrudhi/releases/` (`_releases_dir`), and whether the scheduler thinks it is due for another look
(`should_check`). Nothing here performs a check, fetches a release, or touches the network — an install that
cannot be reached does not become live evidence just because something asked about it.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.update_apply import (
    Channel,
    _current_symlink,
    _releases_dir,
    load_config,
    should_check,
)

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "fleet.yaml"


@dataclass(frozen=True)
class InstallState:
    """Everything a fleet view needs about one install: its policy, what it is running, and what it has."""

    root: str
    channel: Channel
    auto_apply: bool
    current_version: str | None
    available_versions: list[str]
    last_check: float | None
    last_result: str | None
    healthy: bool
    due: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _current_version(root: Path) -> str | None:
    """The version `current` points at, or None. Reads the raw link target the same way
    `update_apply._current_version` does, rather than resolving it, so a relative link is read identically."""
    link = _current_symlink(root)
    if not link.is_symlink():
        return None
    try:
        return Path(os.readlink(link)).name
    except OSError:
        return None


def _version_sort_key(version: str) -> tuple[int, tuple[int, ...], str]:
    """Numeric releases (`0.2.3`) sort as versions; anything else falls back to plain text, after the numeric
    ones, so a malformed directory name cannot crash the listing."""
    parts = version.split(".")
    try:
        return (0, tuple(int(p) for p in parts), version)
    except ValueError:
        return (1, (), version)


def _available_versions(root: Path) -> list[str]:
    """Every version directory under `.pravrudhi/releases/`, oldest first. `current` is a symlink alongside
    them, not a version, and a swap-in-progress temp link (`.current.tmp-*`) is not a finished install either."""
    releases_dir = _releases_dir(root)
    if not releases_dir.is_dir():
        return []
    names = [
        p.name
        for p in releases_dir.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith(".")
    ]
    return sorted(names, key=_version_sort_key)


def _last_check(root: Path) -> float | None:
    path = Path(root) / ".pravrudhi" / "update-last-check"
    if not path.is_file():
        return None
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _last_result(channel: Channel, current_version: str | None, available_versions: list[str]) -> str | None:
    """A one-line, honest summary of what is on disk. Never the text of a past apply attempt: nothing durable
    records that, so nothing here pretends to know it."""
    if channel == "dev":
        return "dev channel: tracks the git checkout directly, not a packaged release"
    if current_version is not None:
        return f"running {current_version}"
    if available_versions:
        return f"{len(available_versions)} release(s) downloaded, none switched in"
    return "no release installed yet"


def _healthy(channel: Channel, root: Path, current_version: str | None) -> bool:
    """A dev checkout has no release concept to be unhealthy about. A release install is healthy once `current`
    points at a directory that is actually there — a broken symlink or an unset `current` is not."""
    if channel == "dev":
        return True
    return current_version is not None and (_releases_dir(root) / current_version).is_dir()


def local_install(root: Path) -> InstallState:
    """One install's state, read entirely from its own workspace directory."""
    root = Path(root)
    config = load_config(root)
    current_version = _current_version(root)
    available_versions = _available_versions(root)
    return InstallState(
        root=str(root),
        channel=config.channel,
        auto_apply=config.auto_apply,
        current_version=current_version,
        available_versions=available_versions,
        last_check=_last_check(root),
        last_result=_last_result(config.channel, current_version, available_versions),
        healthy=_healthy(config.channel, root, current_version),
        due=should_check(root),
    )


def _configured_roots(root: Path) -> list[str]:
    """The install roots to inspect: an operator override at `.pravrudhi/fleet.yaml`, or the packaged defaults
    (this checkout, and the conventional end-user release path) until one is written."""
    override = Path(root) / ".pravrudhi" / "fleet.yaml"
    text = override.read_text(encoding="utf-8") if override.is_file() else PACKAGED_CONFIG.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    data = raw if isinstance(raw, dict) else {}
    roots = data.get("roots")
    return [str(entry) for entry in roots] if isinstance(roots, list) else []


def _resolve_root(root: Path, entry: str) -> Path:
    """`~` expands to the operator's home directory; anything else relative is read against the workspace root,
    never against whatever directory the process happens to have been launched from."""
    path = Path(entry).expanduser()
    return path if path.is_absolute() else Path(root) / path


def known_installs(root: Path) -> list[InstallState]:
    """Every install the configured roots name, that actually exists on this machine.

    Only the configured roots are ever touched: an entry naming a path this machine does not have is skipped
    silently rather than raising, since a config written for one machine may name an install another machine
    lacks, and nothing outside the named roots is read.
    """
    root = Path(root)
    states = []
    for entry in _configured_roots(root):
        resolved = _resolve_root(root, entry)
        if not resolved.is_dir():
            continue
        states.append(local_install(resolved))
    return states


__all__ = ["InstallState", "local_install", "known_installs", "PACKAGED_CONFIG"]
