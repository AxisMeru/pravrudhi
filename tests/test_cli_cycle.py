"""Tests for `pravrudhi cycle`: the unattended cycle's one entry point over `selfbuild.run_unattended_cycle`.

No model is ever called here: `swarm.run_wave` is faked, the same way `tests/test_heartbeat_build_mode.py`
fakes it for the heartbeat's own build dispatch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from pravrudhi.application.delegate import Verdict
from pravrudhi.cli.app import app

runner = CliRunner()


def _invoke(*args: str) -> Any:
    return runner.invoke(app, args)


def _write_delegation(root: Path, *, active: bool = True) -> None:
    cfg_dir = root / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "delegation.yaml").write_text(
        yaml.safe_dump(
            {
                "active": active,
                "granted": "2026-09-11",
                "instruction": "test delegation, not the operator's real one",
                "signature_identity": "agent-for-operator",
                "scope": {"gate_signoff": True},
                "conditions": {},
            }
        )
    )


def _fake_run_wave(*, accepted: bool, files: tuple[str, ...] = (), reasons: tuple[str, ...] = ()) -> Any:
    def run_wave(build_agent: Any, wave: list[Any], **kw: Any) -> list[Verdict]:
        task = wave[0]
        return [
            Verdict(task_id=task.spec.task_id, agent="fake", accepted=accepted, reasons=list(reasons), files=list(files))
        ]

    return run_wave


def test_dry_run_proposes_a_card_and_previews_without_dispatching(tmp_path: Path) -> None:
    result = _invoke("cycle", "--budget", "300", "--dry-run", "--root", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert "card proposed" in result.stdout
    cards = list((tmp_path / "contracts").glob("L1_*.md"))
    assert len(cards) == 1
    assert "preview" in result.stdout
    assert not (tmp_path / ".pravrudhi" / "selfbuild" / "runs.jsonl").exists()
    assert not (tmp_path / "gates").exists()


def test_a_passing_cycle_prints_the_card_the_run_and_the_closed_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_delegation(tmp_path)
    monkeypatch.setattr("pravrudhi.application.swarm.run_wave", _fake_run_wave(accepted=True, files=("README.md",)))

    result = _invoke("cycle", "--budget", "300", "--root", str(tmp_path))

    assert result.exit_code == 0, result.output
    assert "card proposed" in result.stdout
    assert "ACCEPT" in result.stdout
    assert "closed by agent-for-operator" in result.stdout

    [gate_path] = list((tmp_path / "gates").glob("gate_L1.json"))
    report = json.loads(gate_path.read_text())
    assert report["signoff"]["by"] == "agent-for-operator"


def test_no_active_delegation_leaves_the_gate_unsigned_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pravrudhi.application.swarm.run_wave", _fake_run_wave(accepted=True, files=("README.md",)))

    result = _invoke("cycle", "--budget", "300", "--root", str(tmp_path))

    assert result.exit_code != 0
    output = (result.stdout or "") + (result.stderr or "")
    assert "not closed" in output and "delegation" in output

    [gate_path] = list((tmp_path / "gates").glob("gate_L1.json"))
    assert json.loads(gate_path.read_text())["signoff"]["by"] is None


def test_a_rejected_run_leaves_the_gate_unsigned_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_delegation(tmp_path)
    monkeypatch.setattr(
        "pravrudhi.application.swarm.run_wave", _fake_run_wave(accepted=False, reasons=("validation failed",))
    )

    result = _invoke("cycle", "--budget", "300", "--root", str(tmp_path))

    assert result.exit_code != 0
    output = (result.stdout or "") + (result.stderr or "")
    assert "not closed" in output and "validation failed" in output
