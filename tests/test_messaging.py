"""A user's notifications must reach the user's own Telegram, or nobody's.

The engine's bot token lives in the environment of the operator's own service, and `notifications._reach` read
it unconditionally. Every caller happens to pass the engine's own root today, and every one of those routes is
operator-only, so nothing leaked — but the safety was a property of the call sites rather than of this code, and
the first user-facing route to emit a notification would have sent it to the operator's phone.

So the environment is consulted only when a caller names the engine's own root, and a workspace brings its own
credential or gets no delivery at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application.messaging import (
    MessagingError,
    clear_telegram,
    resolve_telegram,
    set_telegram,
    telegram_status,
)


class TestStoringAUsersOwnBot:
    def test_a_workspace_with_no_credential_has_nothing_configured(self, tmp_path: Path) -> None:
        status = telegram_status(tmp_path)
        assert status.configured is False and status.enabled is False and status.chat_id == ""

    def test_setting_a_bot_makes_it_resolvable_without_ever_returning_the_token(self, tmp_path: Path) -> None:
        set_telegram(tmp_path, token="123456:ABC-DEF", chat_id="8679892510")

        status = telegram_status(tmp_path)
        assert status.configured is True and status.enabled is True
        assert status.chat_id == "8679892510"
        assert "123456" not in repr(status), "the token reached a value a route could serialise"

        resolved = resolve_telegram(tmp_path, engine_root=None)
        assert resolved is not None
        assert resolved[0].value == "123456:ABC-DEF" and resolved[1] == "8679892510"

    def test_the_token_file_is_not_readable_by_anyone_else(self, tmp_path: Path) -> None:
        set_telegram(tmp_path, token="123456:ABC-DEF", chat_id="1")
        path = tmp_path / ".pravrudhi" / "messaging" / "telegram.token"
        assert path.stat().st_mode & 0o777 == 0o600

    def test_a_bot_can_be_turned_off_without_being_forgotten(self, tmp_path: Path) -> None:
        """Turning delivery off is not the same as deleting the credential: a user who silences notifications
        for an afternoon should not have to paste their token again."""
        set_telegram(tmp_path, token="123456:ABC-DEF", chat_id="1")
        set_telegram(tmp_path, enabled=False)

        assert telegram_status(tmp_path).configured is True
        assert telegram_status(tmp_path).enabled is False
        assert resolve_telegram(tmp_path, engine_root=None) is None

        set_telegram(tmp_path, enabled=True)
        assert resolve_telegram(tmp_path, engine_root=None) is not None

    def test_clearing_removes_the_credential(self, tmp_path: Path) -> None:
        set_telegram(tmp_path, token="123456:ABC-DEF", chat_id="1")
        assert clear_telegram(tmp_path) is True
        assert telegram_status(tmp_path).configured is False
        assert not (tmp_path / ".pravrudhi" / "messaging" / "telegram.token").exists()
        assert clear_telegram(tmp_path) is False

    def test_a_token_without_a_chat_id_is_refused(self, tmp_path: Path) -> None:
        """A token with nowhere to send is configuration that looks complete and delivers nothing."""
        with pytest.raises(MessagingError):
            set_telegram(tmp_path, token="123456:ABC-DEF", chat_id="")

    def test_clearing_the_chat_id_of_a_stored_bot_is_refused(self, tmp_path: Path) -> None:
        """The same failure as a token with nowhere to deliver, reached from the other side: settings that read
        as configured while nothing arrives. Removing the bot is the way to stop delivering."""
        set_telegram(tmp_path, token="123456:ABC-DEF", chat_id="1")

        with pytest.raises(MessagingError):
            set_telegram(tmp_path, chat_id="")

        assert telegram_status(tmp_path).chat_id == "1"
        assert resolve_telegram(tmp_path, engine_root=None) is not None

    def test_enabling_a_workspace_that_has_no_credential_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(MessagingError):
            set_telegram(tmp_path, enabled=True)


class TestTheOperatorsOwnEnvironment:
    def test_the_engines_root_may_use_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")

        resolved = resolve_telegram(tmp_path, engine_root=tmp_path)

        assert resolved is not None and resolved[0].value == "env-token" and resolved[1] == "env-chat"

    def test_the_engines_status_says_the_bot_comes_from_its_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Otherwise the settings page tells the operator "not configured" while Telegram is delivering, which
        is the surface disagreeing with the system — the failure this module exists to stop."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")

        status = telegram_status(tmp_path, engine_root=tmp_path)

        assert status.configured is True and status.enabled is True
        assert status.chat_id == "env-chat" and status.from_environment is True
        assert "env-token" not in repr(status)

    def test_a_workspace_is_told_it_has_no_bot_however_the_engine_is_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        workspace = tmp_path / "workspaces" / "someone"
        workspace.mkdir(parents=True)

        assert telegram_status(workspace, engine_root=tmp_path).configured is False

    def test_a_stored_bot_is_reported_as_this_roots_own_not_inherited(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        set_telegram(tmp_path, token="stored-token", chat_id="stored-chat")

        status = telegram_status(tmp_path, engine_root=tmp_path)
        assert status.from_environment is False and status.chat_id == "stored-chat"

    def test_a_workspace_never_reaches_the_operators_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The leak this module exists to prevent: a user's notification delivered to the operator's phone."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        workspace = tmp_path / "workspaces" / "someone"
        workspace.mkdir(parents=True)

        assert resolve_telegram(workspace, engine_root=tmp_path) is None

    def test_naming_no_engine_root_means_no_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The default a caller gets when it does not say whose engine this is, so the unsafe path is the one
        that has to be asked for by name."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")

        assert resolve_telegram(tmp_path, engine_root=None) is None

    def test_a_workspaces_own_bot_wins_over_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Even at the engine's own root: a credential someone deliberately stored is the one they meant."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        set_telegram(tmp_path, token="stored-token", chat_id="stored-chat")

        resolved = resolve_telegram(tmp_path, engine_root=tmp_path)

        assert resolved is not None and resolved[0].value == "stored-token"

    def test_a_path_that_only_looks_like_the_engine_root_is_not_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`engine/../engine` is the engine and `engine-2` is not; both are settled by resolving, not by string
        comparison — the same reason `credentials.store_for_project` resolves before it compares."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        engine = tmp_path / "engine"
        engine.mkdir()
        (tmp_path / "engine-2").mkdir()

        assert resolve_telegram(engine / ".." / "engine", engine_root=engine) is not None
        assert resolve_telegram(tmp_path / "engine-2", engine_root=engine) is None


class TestWhatEmitDelivers:
    def test_emit_does_not_reach_the_environment_unless_the_engine_root_is_named(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.application import notifications

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
        sent: list[object] = []
        monkeypatch.setattr(
            "pravrudhi.application.reach.send",
            lambda root, **kw: sent.append(kw) or None,  # type: ignore[func-returns-value]
        )

        notifications.emit(tmp_path, kind="run_finished", title="done")
        assert sent == [], "a caller that named no engine root still reached the operator's bot"

        notifications.emit(tmp_path, kind="run_finished", title="done", engine_root=tmp_path)
        assert len(sent) == 1 and sent[0]["chat_id"] == "env-chat"  # type: ignore[index]

    def test_the_record_is_written_even_when_nothing_can_be_delivered(self, tmp_path: Path) -> None:
        from pravrudhi.application import notifications

        note = notifications.emit(tmp_path, kind="run_finished", title="done")
        assert [n.id for n in notifications.recent(tmp_path)] == [note.id]
