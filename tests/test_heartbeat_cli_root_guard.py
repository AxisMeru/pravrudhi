"""cli-lead, 2026-09-13: a hand-run `pravrudhi heartbeat` with no `--root` defaults to the current directory
(`ROOT_OPT = typer.Option(Path("."), ...)`). Both systemd units pin an absolute `--root`, so the unattended
loops are never exposed - but a session moving between several repositories, which every session in this
fleet does constantly, can hand-run a beat from the wrong directory and dispatch into whatever tree the shell
happens to be sitting in. That is the same failure shape as the stray-repo dispatch this guard was added
alongside. The guard fires only when `--root` is left at its default and the directory does not look
initialised (no `.pravrudhi/config.yaml`, the same file `doctor.run_doctor`'s `initialised` check keys on);
a caller that passes `--root` explicitly, initialised or not, is never touched by it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pravrudhi.application import heartbeat
from pravrudhi.cli.app import app

runner = CliRunner()


def _fake_beat(root: Path, *, dispatch: object = None, probe: object = None, now: object = None) -> heartbeat.BeatRecord:
    return heartbeat.BeatRecord(at="2026-09-13T00:00:00Z", looked_at=(), chose={}, reason="fake beat ran")


def test_a_defaulted_root_with_no_config_refuses_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    r = runner.invoke(app, ["heartbeat"])

    assert r.exit_code != 0
    assert ".pravrudhi/config.yaml" in r.output
    assert "--root" in r.output


def test_a_defaulted_root_that_is_initialised_proceeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".pravrudhi").mkdir()
    (tmp_path / ".pravrudhi" / "config.yaml").write_text("{}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(heartbeat, "beat", _fake_beat)

    r = runner.invoke(app, ["heartbeat", "--json"])

    assert r.exit_code == 0, r.output
    assert "fake beat ran" in r.output


def test_an_explicit_root_is_never_refused_even_when_not_initialised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard only ever second-guesses a defaulted root - a caller that names `--root` explicitly, even to
    an uninitialised directory, gets exactly the behaviour it asked for, unchanged."""
    monkeypatch.setattr(heartbeat, "beat", _fake_beat)

    r = runner.invoke(app, ["heartbeat", "--root", str(tmp_path), "--json"])

    assert r.exit_code == 0, r.output
    assert "fake beat ran" in r.output
