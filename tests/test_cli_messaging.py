"""The workspace's own Telegram bot (application/messaging.py), reachable from the CLI.

`pravrudhi messaging` is the CLI's door onto `messaging.py` -- the same store, status and clear the API's
`/api/messaging/telegram` routes already use, so a workspace with no browser handy still configures its own bot
rather than being limited to whatever the operator's engine environment carries.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from pravrudhi.cli.app import app

runner = CliRunner()


def test_status_reports_no_bot_for_a_fresh_workspace(tmp_path: Path) -> None:
    result = runner.invoke(app, ["messaging", "status", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "no bot configured" in result.stdout


def test_set_then_status_reports_the_bot_without_ever_printing_the_token(tmp_path: Path) -> None:
    set_result = runner.invoke(
        app, ["messaging", "set", "--token", "123456:ABC-DEF", "--chat-id", "8679892510", "--root", str(tmp_path)]
    )
    assert set_result.exit_code == 0
    assert "123456" not in set_result.stdout

    status = runner.invoke(app, ["messaging", "status", "--root", str(tmp_path)])
    assert status.exit_code == 0
    assert "8679892510" in status.stdout
    assert "123456" not in status.stdout
    assert "enabled" in status.stdout


def test_status_as_json_matches_the_api_shape_and_never_carries_the_token(tmp_path: Path) -> None:
    runner.invoke(app, ["messaging", "set", "--token", "123456:ABC-DEF", "--chat-id", "1", "--root", str(tmp_path)])
    result = runner.invoke(app, ["messaging", "status", "--json", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "configured": True, "enabled": True, "chat_id": "1", "from_environment": False,
    }


def test_set_can_disable_delivery_without_forgetting_the_credential(tmp_path: Path) -> None:
    runner.invoke(app, ["messaging", "set", "--token", "123456:ABC-DEF", "--chat-id", "1", "--root", str(tmp_path)])
    result = runner.invoke(app, ["messaging", "set", "--disabled", "--root", str(tmp_path)])
    assert result.exit_code == 0

    status = json.loads(runner.invoke(app, ["messaging", "status", "--json", "--root", str(tmp_path)]).stdout)
    assert status == {"configured": True, "enabled": False, "chat_id": "1", "from_environment": False}


def test_set_without_a_chat_id_is_refused(tmp_path: Path) -> None:
    result = runner.invoke(app, ["messaging", "set", "--token", "123456:ABC-DEF", "--root", str(tmp_path)])
    assert result.exit_code == 1


def test_clear_forgets_the_bot(tmp_path: Path) -> None:
    runner.invoke(app, ["messaging", "set", "--token", "123456:ABC-DEF", "--chat-id", "1", "--root", str(tmp_path)])
    result = runner.invoke(app, ["messaging", "clear", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "cleared" in result.stdout

    status = runner.invoke(app, ["messaging", "status", "--root", str(tmp_path)])
    assert "no bot configured" in status.stdout


def test_clear_on_a_workspace_with_no_bot_says_so(tmp_path: Path) -> None:
    result = runner.invoke(app, ["messaging", "clear", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "no bot" in result.stdout
