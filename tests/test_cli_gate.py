from pathlib import Path

import yaml
from typer.testing import CliRunner

from pravrudhi.cli.app import app
from tests.test_gate import CARD, EVIDENCE

runner = CliRunner()


def test_cli_emit_check_contract(tmp_path: Path) -> None:
    (tmp_path / "contracts").mkdir()
    (tmp_path / "gates").mkdir()
    (tmp_path / "contracts" / "L0_scaffold.md").write_text(CARD)
    ev = tmp_path / "gates" / "L0.evidence.yaml"
    ev.write_text(yaml.safe_dump(EVIDENCE))
    r = runner.invoke(app, ["gate", "emit", "L0", "--evidence", str(ev), "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    gate = tmp_path / "gates" / "gate_L0.json"
    r = runner.invoke(app, ["gate", "check", str(gate), "--root", str(tmp_path)])
    assert r.exit_code == 0 and "pass" in r.output
    r = runner.invoke(app, ["contract", "check", str(gate), "--root", str(tmp_path)])
    assert r.exit_code == 0


def test_cli_version() -> None:
    r = runner.invoke(app, ["--version"])
    # Not a hardcoded number: this said "pravrudhi 0.4.0" while the shipped release was 0.4.2, so the test
    # enforced the stale literal instead of noticing it.
    from pravrudhi import __version__

    assert r.exit_code == 0 and f"pravrudhi {__version__}" in r.output
