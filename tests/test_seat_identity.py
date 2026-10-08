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


def test_the_panel_refuses_to_verify_against_nothing(local_file, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "loop"
    home.mkdir()
    (home / ".credentials.json").write_text("{}")
    monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(home))

    def real_seam() -> dict[str, str]:  # the real account.claude_env(live=True), with no scripted identity configured
        return account.claude_env(live=True)

    monkeypatch.setattr(panel, "_verified_seat_env", real_seam)
    with pytest.raises(si.SeatIdentityMissing):
        panel._assert_claude_seat({"CLAUDE_CONFIG_DIR": str(home)})
    assert panel.claude_cli_expected_email(required=False) is None


def test_the_committed_config_holds_no_real_seat_address() -> None:
    root = Path(__file__).resolve().parent.parent
    text = (root / "configs" / "seats.yaml").read_text()
    assert "gmail.com" not in text and "example.invalid" in text


def test_the_team_login_dir_can_never_be_chosen_for_a_scripted_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    admin = tmp_path / "claude-admin"
    admin.mkdir()
    (admin / ".credentials.json").write_text("{}")
    monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(admin))
    with pytest.raises(account.AdminSeatRefused):
        account.scripted_claude_home()
    with pytest.raises(account.AdminSeatRefused):
        account.claude_env(require=False)


def test_the_registry_never_offers_the_team_login_whatever_it_is_called(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "seats.yaml").write_text(
        yaml.safe_dump({"version": 1, "seats": [
            {"id": "primary", "email": "a@seats.test", "config_dir": str(tmp_path / "p")},
            {"id": "fallback", "email": "b@seats.test", "config_dir": str(tmp_path / "claude-admin")},
            {"id": "reserve", "email": "c@seats.test", "config_dir": "~/.config/pravrudhi/claude-admin"}]})
    )
    assert [s.id for s in account.seats(tmp_path)] == ["primary"]
    assert all(not account.is_forbidden_seat_dir(s.config_dir) for s in account.seats(tmp_path))


def test_a_pinned_directory_that_is_the_team_login_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(account.HOME_ENV, str(tmp_path / "claude-admin"))
    assert not any(s.id == "pinned" for s in account.seats(tmp_path))


def test_the_committed_registry_has_no_fallback_tied_to_the_team_login() -> None:
    text = (Path(__file__).resolve().parent.parent / "configs" / "seats.yaml").read_text()
    entries = yaml.safe_load(text)["seats"]
    assert [e["id"] for e in entries] == ["primary"]
    assert not any("claude-admin" in str(e.get("config_dir", "")) for e in entries)


def _seat2_home(tmp_path: Path, name: str, email: str) -> Path:
    import json

    home = tmp_path / name
    home.mkdir()
    (home / ".credentials.json").write_text("{}")
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": email}}))
    return home


def test_the_real_guard_refuses_when_the_verified_dir_is_not_the_calls_dir(
    tmp_path: Path, local_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conftest seam passes the dir-equality check by construction; this runs the REAL `claude_env(live=True)` seam."""
    local_file({"scripted_claude_email": "seat@seats.test"})
    verified = _seat2_home(tmp_path, "loop", "seat@seats.test")
    other = tmp_path / "somewhere-else"
    other.mkdir()
    monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(verified))
    monkeypatch.setattr(account, "_auth_status", lambda d: {"loggedIn": True, "email": "seat@seats.test"})
    monkeypatch.setattr(panel, "_verified_seat_env", lambda: account.claude_env(live=True))
    panel._assert_claude_seat({"CLAUDE_CONFIG_DIR": str(verified)})  # the same dir passes
    with pytest.raises(panel.ClaudeCliNotProvisioned, match="is not the config dir this call uses"):
        panel._assert_claude_seat({"CLAUDE_CONFIG_DIR": str(other)})


def test_the_team_login_dir_becomes_claude_cli_not_provisioned_in_the_panel(
    tmp_path: Path, local_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_file({"scripted_claude_email": "seat@seats.test"})
    admin = _seat2_home(tmp_path, "claude-admin", "admin@axismeru.com")
    monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(admin))
    monkeypatch.setattr(panel, "_verified_seat_env", lambda: account.claude_env(live=True))
    with pytest.raises(panel.ClaudeCliNotProvisioned, match="refusing"):
        panel._assert_claude_seat({"CLAUDE_CONFIG_DIR": str(admin)})


def test_an_unreadable_auth_status_and_a_wrong_email_have_distinct_messages(
    tmp_path: Path, local_file, monkeypatch: pytest.MonkeyPatch
) -> None:
    local_file({"scripted_claude_email": "seat@seats.test"})
    home = _seat2_home(tmp_path, "loop", "seat@seats.test")
    monkeypatch.setenv(account.SCRIPTED_CLAUDE_HOME_ENV, str(home))
    monkeypatch.setattr(account, "_auth_status", lambda d: None)
    with pytest.raises(account.ScriptedSeatMismatch, match="could not be read") as unreadable:
        account.claude_env(live=True)
    monkeypatch.setattr(account, "_auth_status", lambda d: {"loggedIn": True, "email": "wrong@seats.test"})
    with pytest.raises(account.ScriptedSeatMismatch, match="shows 'wrong@seats.test'") as wrong:
        account.claude_env(live=True)
    assert "auth status" in str(unreadable.value) and "auth status" in str(wrong.value)
    assert "could not be read" not in str(wrong.value)
