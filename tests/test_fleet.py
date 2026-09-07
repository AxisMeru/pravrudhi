"""fleet.py reads install state purely off the filesystem: a missing root must be skipped, not raise, and only
the roots a fleet config actually names may ever be touched."""

from __future__ import annotations

import time
from pathlib import Path

from pravrudhi.application.fleet import known_installs, local_install
from pravrudhi.application.update_apply import UpdateConfig, save_config


def make_release_tree(
    root: Path,
    *,
    versions: tuple[str, ...] = ("0.1.0", "0.2.0"),
    current: str | None = "0.2.0",
    channel: str = "release",
    auto_apply: bool = False,
    check_interval_min: int = 60,
    last_check_age_s: float | None = None,
) -> None:
    releases_dir = root / ".pravrudhi" / "releases"
    for version in versions:
        (releases_dir / version).mkdir(parents=True)
    if current is not None:
        (releases_dir / "current").symlink_to(releases_dir / current, target_is_directory=True)
    save_config(
        root,
        UpdateConfig(
            channel=channel,  # type: ignore[arg-type]
            auto_apply=auto_apply,
            check_interval_min=check_interval_min,
            keep_previous=2,
        ),
    )
    if last_check_age_s is not None:
        last_check_path = root / ".pravrudhi" / "update-last-check"
        last_check_path.write_text(str(time.time() - last_check_age_s), encoding="utf-8")


def write_fleet_config(root: Path, roots: list[str]) -> None:
    path = root / ".pravrudhi" / "fleet.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "roots:\n" + "".join(f'  - "{r}"\n' for r in roots)
    path.write_text(body, encoding="utf-8")


def test_local_install_reads_a_release_tree(tmp_path: Path) -> None:
    root = tmp_path / "release-install"
    root.mkdir()
    make_release_tree(
        root,
        versions=("0.1.0", "0.2.0"),
        current="0.2.0",
        channel="release",
        auto_apply=True,
        check_interval_min=60,
        last_check_age_s=10_000,  # long past the interval: a check is due
    )

    state = local_install(root)

    assert state.root == str(root)
    assert state.channel == "release"
    assert state.auto_apply is True
    assert state.current_version == "0.2.0"
    assert state.available_versions == ["0.1.0", "0.2.0"]
    assert state.last_result == "running 0.2.0"
    assert state.healthy is True
    assert state.due is True
    assert state.last_check is not None and state.last_check > 0


def test_local_install_recent_check_is_not_due(tmp_path: Path) -> None:
    root = tmp_path / "release-install"
    root.mkdir()
    make_release_tree(root, current="0.2.0", check_interval_min=1440, last_check_age_s=5.0)

    state = local_install(root)

    assert state.due is False


def test_local_install_broken_symlink_is_unhealthy(tmp_path: Path) -> None:
    root = tmp_path / "release-install"
    root.mkdir()
    releases_dir = root / ".pravrudhi" / "releases"
    releases_dir.mkdir(parents=True)
    (releases_dir / "0.1.0").mkdir()
    # `current` points at a version that was never actually installed (e.g. pruned from under it).
    (releases_dir / "current").symlink_to(releases_dir / "0.9.9", target_is_directory=True)
    save_config(root, UpdateConfig(channel="release"))

    state = local_install(root)

    assert state.current_version == "0.9.9"
    assert state.available_versions == ["0.1.0"]
    assert state.healthy is False


def test_local_install_with_no_releases_at_all(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    root.mkdir()

    state = local_install(root)

    assert state.current_version is None
    assert state.available_versions == []
    assert state.last_result == "no release installed yet"
    assert state.healthy is False


def test_local_install_dev_channel_has_no_release_concept(tmp_path: Path) -> None:
    root = tmp_path / "dev-checkout"
    root.mkdir()
    save_config(root, UpdateConfig(channel="dev"))

    state = local_install(root)

    assert state.channel == "dev"
    assert state.current_version is None
    assert "dev channel" in (state.last_result or "")
    assert state.healthy is True


def test_known_installs_skips_a_missing_root_without_raising(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    present = workspace / "present-install"
    present.mkdir()
    make_release_tree(present, current="0.1.0", versions=("0.1.0",))
    write_fleet_config(workspace, ["present-install", "absent-install"])

    installs = known_installs(workspace)

    assert [i.root for i in installs] == [str(present)]


def test_known_installs_never_reads_outside_the_configured_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    allowed = workspace / "allowed-install"
    allowed.mkdir()
    make_release_tree(allowed, current="0.3.0", versions=("0.3.0",))
    write_fleet_config(workspace, ["allowed-install"])

    # A second, fully valid install sits right next to the workspace but is never named in its fleet config.
    decoy = tmp_path / "decoy-install"
    decoy.mkdir()
    make_release_tree(decoy, current="9.9.9", versions=("9.9.9",))

    installs = known_installs(workspace)

    assert len(installs) == 1
    assert installs[0].root == str(allowed)
    assert all(i.root != str(decoy) for i in installs)
    assert all("9.9.9" not in (i.current_version or "") for i in installs)


def test_known_installs_default_config_includes_this_workspace(tmp_path: Path) -> None:
    # No .pravrudhi/fleet.yaml override: the packaged default lists "." (this workspace) among its roots.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    save_config(workspace, UpdateConfig(channel="dev"))

    installs = known_installs(workspace)

    assert any(i.root == str(workspace) for i in installs)
