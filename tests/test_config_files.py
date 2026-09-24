"""Config files a route reads must resolve inside an installed wheel, not only in a source checkout.

2026-09-24: engine 0.5.26's POST /api/v1/analyse-facts answered 503 in the container -- the image's root is
the /data volume, which has no configs/, so `configs/nyaya_agent.yaml` (and `configs/partner_api.yaml`) did
not exist. The wheel now ships both and `config_file` reads the release's own copy; a source checkout (no
packaged copy) reads `<root>/configs/<name>`; neither is a refusal (FileNotFoundError, a 503 at the route) --
never a silently invented default.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from pravrudhi.application import config_files

REPO = Path(__file__).resolve().parent.parent


def test_a_source_checkout_reads_its_own_configs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_files, "PACKAGED_CONFIG_DIR", tmp_path / "absent")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "nyaya_agent.yaml").write_text("tau: 0.5\n")
    assert config_files.config_file(tmp_path, "nyaya_agent.yaml") == tmp_path / "configs" / "nyaya_agent.yaml"


def test_an_installed_release_uses_its_own_copy_over_a_stale_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Studio's root is a working checkout on any branch; its configs/ must not override the release's."""
    packaged = tmp_path / "pkg"
    packaged.mkdir()
    (packaged / "nyaya_agent.yaml").write_text("tau: 0.6\n")
    monkeypatch.setattr(config_files, "PACKAGED_CONFIG_DIR", packaged)
    root = tmp_path / "data"
    (root / "configs").mkdir(parents=True)
    (root / "configs" / "nyaya_agent.yaml").write_text("tau: 0.1\n")
    assert config_files.config_file(root, "nyaya_agent.yaml") == packaged / "nyaya_agent.yaml"


def test_neither_present_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_files, "PACKAGED_CONFIG_DIR", tmp_path / "absent")
    with pytest.raises(FileNotFoundError, match="nyaya_agent.yaml"):
        config_files.config_file(tmp_path, "nyaya_agent.yaml")


def test_the_wheel_ships_every_config_a_route_reads() -> None:
    """pyproject's force-include must name each config `config_file` is asked for in src/."""
    pyproject = (REPO / "pyproject.toml").read_text()
    used = set(re.findall(r'config_file\([^,]+,\s*"([^"]+)"\)', "".join(
        p.read_text() for p in (REPO / "src" / "pravrudhi").rglob("*.py")
    )))
    assert used, "no config_file(...) call sites found -- the regex no longer matches the code"
    for name in sorted(used):
        assert f'"configs/{name}" = "pravrudhi/assets/configs/{name}"' in pyproject, name


def test_agent_config_pin_matches_the_dockerfile() -> None:
    """One source of truth for the score binary: the sha the agent enforces is the sha the image verifies."""
    agent = yaml.safe_load((REPO / "configs" / "nyaya_agent.yaml").read_text())
    docker = (REPO / "deploy" / "docker" / "Dockerfile").read_text()
    m = re.search(r"^ARG NYAYA_SCORE_SHA256=([0-9a-f]{64})$", docker, re.M)
    assert m, "Dockerfile has no ARG NYAYA_SCORE_SHA256=<64 hex>"
    assert agent["pinned_score_sha256"] == m.group(1)
