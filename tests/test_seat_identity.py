"""Seat addresses come from LOCAL configuration (environment or ~/.config/pravrudhi/seats.local.yaml), never the
repository; absence refuses."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pravrudhi.agents import account
from pravrudhi.agents import seat_identity as si
from pravrudhi.application import panel


@pytest.fixture
def local_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "seats.local.yaml"
    monkeypatch.setenv(si.LOCAL_FILE_ENV, str(path))
    monkeypatch.delenv(si.SCRIPTED_EMAIL_ENV, raising=False)
    monkeypatch.delenv(si.CLI_EXPECTED_EMAIL_ENV, raising=False)

    def write(data: dict) -> None:
        path.write_text(yaml.safe_dump(data))

    return write


def test_scripted_email_prefers_the_environment_then_the_local_file(local_file, monkeypatch: pytest.MonkeyPatch) -> None:
    local_file({"scripted_claude_email": "from-file@seats.test"})
    assert si.scripted_claude_email() == "from-file@seats.test"
    monkeypatch.setenv(si.SCRIPTED_EMAIL_ENV, "from-env@seats.test")
    assert si.scripted_claude_email() == "from-env@seats.test"


def test_a_missing_identity_refuses_where_it_is_needed(local_file) -> None:
    with pytest.raises(si.SeatIdentityMissing, match="not configured"):
        si.scripted_claude_email()
    assert si.scripted_claude_email(required=False) is None


def test_cli_expected_email_defaults_to_the_scripted_seat(local_file) -> None:
    local_file({"scripted_claude_email": "seat@seats.test"})
    assert si.claude_cli_expected_email() == "seat@seats.test"
    local_file({"scripted_claude_email": "seat@seats.test", "claude_cli_expected_email": "cli@seats.test"})
    assert si.claude_cli_expected_email() == "cli@seats.test"


def test_a_committed_placeholder_is_never_an_identity_in_the_registry(tmp_path: Path, local_file) -> None:
    cfg = tmp_path / "configs"
    cfg.mkdir()
    (cfg / "seats.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "seats": [
                    {"id": "primary", "email": "seat-a@example.invalid", "config_dir": str(tmp_path / "p")},
                    {"id": "fallback", "email": "team@axismeru.com", "config_dir": str(tmp_path / "f")},
                ],
            }
        )
    )
    by_id = {s.id: s for s in account.seats(tmp_path)}
    assert by_id["primary"].email == "" and by_id["fallback"].email == "team@axismeru.com"
    local_file({"seats": {"primary": "real-seat@seats.test"}})
    assert {s.id: s.email for s in account.seats(tmp_path)}["primary"] == "real-seat@seats.test"


def test_claude_env_refuses_a_provisioned_seat_when_no_identity_is_configured(
    tmp_path: Path, local_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "loop"
    home.mkdir()
    (home / ".credentials.json").write_text("{}")
    monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(home))
    with pytest.raises(si.SeatIdentityMissing):
        account.claude_env()


def test_the_panel_refuses_to_verify_against_nothing(local_file, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(panel, "_claude_auth_email", lambda env: "whoever@seats.test")
    with pytest.raises(si.SeatIdentityMissing):
        panel._assert_claude_seat({"CLAUDE_CONFIG_DIR": "/x"})
    assert panel.claude_cli_expected_email(required=False) is None


def test_the_committed_config_holds_no_real_seat_address() -> None:
    root = Path(__file__).resolve().parent.parent
    text = (root / "configs" / "seats.yaml").read_text()
    assert "gmail.com" not in text and "example.invalid" in text
