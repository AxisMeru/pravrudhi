"""Tests for `pravrudhi.application.credentials`. Each non-negotiable security property gets its own test:
a key must never surface in a repr, a str, an exception message or a redacted log line; the credential file
must be 0600 and refused inside a git work tree; an unrecognized provider must be refused everywhere."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

from pravrudhi.application import credentials as creds
from pravrudhi.application.credentials import (
    CredentialBoundaryError,
    FileCredentialStore,
    GitTrackedPathError,
    Secret,
    UnknownProviderError,
    redact,
    store_for,
    store_for_project,
    validate,
)

FAKE_OPENAI_KEY = "sk-abcdefghijklmnopqrstuvwxyz012345"
FAKE_ANTHROPIC_KEY = "sk-ant-abcdefghijklmnopqrstuvwxyz012345"
FAKE_GOOGLE_KEY = "AIzaSyAbCdEfGhIjKlMnOpQrStUvWxYz012"
FAKE_HEX_KEY = "0123456789abcdef0123456789abcdef"


# ---------------------------------------------------------------------------
# Secret: never leaks the value through repr or str.
# ---------------------------------------------------------------------------


def test_secret_repr_never_contains_key() -> None:
    secret = Secret(provider="openai", value=FAKE_OPENAI_KEY)
    assert FAKE_OPENAI_KEY not in repr(secret)
    assert repr(secret) == f"Secret(provider=openai, len={len(FAKE_OPENAI_KEY)})"


def test_secret_str_never_contains_key() -> None:
    secret = Secret(provider="openai", value=FAKE_OPENAI_KEY)
    assert FAKE_OPENAI_KEY not in str(secret)


def test_secret_reveal_returns_the_actual_value() -> None:
    secret = Secret(provider="openai", value=FAKE_OPENAI_KEY)
    assert secret.reveal() == FAKE_OPENAI_KEY


# ---------------------------------------------------------------------------
# redact(): catches every key shape the spec names.
# ---------------------------------------------------------------------------


def test_redact_strips_openai_style_key() -> None:
    text = f"request failed with key {FAKE_OPENAI_KEY} attached"
    assert FAKE_OPENAI_KEY not in redact(text)


def test_redact_strips_anthropic_style_key() -> None:
    text = f"request failed with key {FAKE_ANTHROPIC_KEY} attached"
    assert FAKE_ANTHROPIC_KEY not in redact(text)


def test_redact_strips_google_style_key() -> None:
    text = f"request failed with key {FAKE_GOOGLE_KEY} attached"
    assert FAKE_GOOGLE_KEY not in redact(text)


def test_redact_strips_bare_hex_key() -> None:
    text = f"request failed with key {FAKE_HEX_KEY} attached"
    assert FAKE_HEX_KEY not in redact(text)


def test_redact_leaves_ordinary_text_alone() -> None:
    text = "the provider rejected the request with status 401"
    assert redact(text) == text


# ---------------------------------------------------------------------------
# FileCredentialStore: file mode, git-tree refusal, unknown-provider refusal,
# and a key never appearing in an exception message it raises.
# ---------------------------------------------------------------------------


def test_put_writes_a_0600_file(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    store.put("openai", FAKE_OPENAI_KEY)
    path = tmp_path / ".pravrudhi" / "credentials" / "openai.key"
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600


def test_get_put_delete_roundtrip(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    assert store.get("openai") is None
    store.put("openai", FAKE_OPENAI_KEY)
    secret = store.get("openai")
    assert secret is not None
    assert secret.reveal() == FAKE_OPENAI_KEY
    assert store.configured() == ["openai"]
    assert store.delete("openai") is True
    assert store.get("openai") is None
    assert store.delete("openai") is False


def test_put_refuses_unknown_provider(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    with pytest.raises(UnknownProviderError):
        store.put("not-a-real-provider", FAKE_OPENAI_KEY)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_put_refuses_inside_a_git_work_tree(tmp_path: Path) -> None:
    _git_init(tmp_path)
    store = FileCredentialStore(tmp_path)
    with pytest.raises(GitTrackedPathError):
        store.put("openai", FAKE_OPENAI_KEY)


def test_put_refuses_inside_a_git_work_tree_from_a_subdirectory(tmp_path: Path) -> None:
    _git_init(tmp_path)
    sub = tmp_path / "nested" / "workspace"
    sub.mkdir(parents=True)
    store = FileCredentialStore(sub)
    with pytest.raises(GitTrackedPathError):
        store.put("openai", FAKE_OPENAI_KEY)


def test_a_stray_empty_dot_git_directory_does_not_trigger_a_false_refusal(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()  # not a real repo — must not be mistaken for one
    store = FileCredentialStore(tmp_path)
    store.put("openai", FAKE_OPENAI_KEY)
    assert store.get("openai") is not None


def test_git_tree_refusal_message_never_contains_the_key(tmp_path: Path) -> None:
    _git_init(tmp_path)
    store = FileCredentialStore(tmp_path)
    with pytest.raises(GitTrackedPathError) as excinfo:
        store.put("openai", FAKE_OPENAI_KEY)
    assert FAKE_OPENAI_KEY not in str(excinfo.value)


def test_unknown_provider_message_never_contains_the_key(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    with pytest.raises(UnknownProviderError) as excinfo:
        store.put("not-a-real-provider", FAKE_OPENAI_KEY)
    assert FAKE_OPENAI_KEY not in str(excinfo.value)


def test_put_refuses_empty_key(tmp_path: Path) -> None:
    store = FileCredentialStore(tmp_path)
    with pytest.raises(ValueError):
        store.put("openai", "   ")


# ---------------------------------------------------------------------------
# validate(): a fake probe, ok/reason without the key ever appearing in it.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


def test_validate_ok_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_probe(url: str, headers: dict[str, str]) -> httpx.Response:
        return _FakeResponse(200)  # type: ignore[return-value]

    ok, reason = validate("openai", FAKE_OPENAI_KEY, probe=fake_probe)
    assert ok is True
    assert reason == "ok"


def test_validate_rejects_on_401_without_leaking_key() -> None:
    def fake_probe(url: str, headers: dict[str, str]) -> httpx.Response:
        assert FAKE_OPENAI_KEY in headers.get("Authorization", "")  # the real call does carry it
        return _FakeResponse(401)  # type: ignore[return-value]

    ok, reason = validate("openai", FAKE_OPENAI_KEY, probe=fake_probe)
    assert ok is False
    assert FAKE_OPENAI_KEY not in reason


def test_validate_probe_exception_never_leaks_key_in_reason() -> None:
    def raising_probe(url: str, headers: dict[str, str]) -> httpx.Response:
        raise RuntimeError(f"connection reset while sending {FAKE_OPENAI_KEY}")

    ok, reason = validate("openai", FAKE_OPENAI_KEY, probe=raising_probe)
    assert ok is False
    assert FAKE_OPENAI_KEY not in reason


def test_validate_google_key_in_url_never_leaks_through_a_failed_probe() -> None:
    def raising_probe(url: str, headers: dict[str, str]) -> httpx.Response:
        assert FAKE_GOOGLE_KEY in url  # Google auth is a query param, unlike the others
        raise RuntimeError(f"could not reach {url}")

    ok, reason = validate("google", FAKE_GOOGLE_KEY, probe=raising_probe)
    assert ok is False
    assert FAKE_GOOGLE_KEY not in reason


def test_validate_refuses_unknown_provider() -> None:
    ok, reason = validate("not-a-real-provider", FAKE_OPENAI_KEY)
    assert ok is False
    assert "unknown provider" in reason


def test_validate_generic_openai_compatible_needs_a_base_url() -> None:
    ok, reason = validate("openai-compatible", FAKE_OPENAI_KEY)
    assert ok is False
    assert "base_url" in reason


def test_validate_generic_openai_compatible_uses_supplied_base_url() -> None:
    seen: dict[str, str] = {}

    def fake_probe(url: str, headers: dict[str, str]) -> httpx.Response:
        seen["url"] = url
        return _FakeResponse(200)  # type: ignore[return-value]

    ok, _ = validate("openai-compatible", FAKE_OPENAI_KEY, base_url="https://example.com/v1", probe=fake_probe)
    assert ok is True
    assert seen["url"].startswith("https://example.com/v1")


# ---------------------------------------------------------------------------
# store_for(): file store with no user, NotImplementedError for a logged-in one.
# ---------------------------------------------------------------------------


def test_store_for_returns_file_store_with_no_user(tmp_path: Path) -> None:
    store = store_for(tmp_path, None)
    assert isinstance(store, FileCredentialStore)


def test_store_for_raises_for_a_logged_in_user(tmp_path: Path) -> None:
    from pravrudhi.api.identity import User

    user = User(id="u1", email="someone@example.com", role="authenticated")
    with pytest.raises(NotImplementedError):
        store_for(tmp_path, user)


# ---------------------------------------------------------------------------
# store_for_project(): the boundary between a user's own workspace and the
# engine's own project.
# ---------------------------------------------------------------------------


def test_store_for_project_returns_file_store_for_the_local_operator(tmp_path: Path) -> None:
    """No signed-in user at all: the single-operator path, where the engine's own root is the only project."""
    store = store_for_project(tmp_path, engine_root=tmp_path, user=None)
    assert isinstance(store, FileCredentialStore)


def test_store_for_project_refuses_a_signed_in_user_at_the_engine_root(tmp_path: Path) -> None:
    from pravrudhi.api.identity import User

    user = User(id="u-1", email="someone@example.com", role="authenticated")
    with pytest.raises(CredentialBoundaryError):
        store_for_project(tmp_path, engine_root=tmp_path, user=user)


def test_store_for_project_lets_a_signed_in_user_use_their_own_workspace(tmp_path: Path) -> None:
    from pravrudhi.api.identity import User

    engine_root = tmp_path / "engine"
    workspace = tmp_path / "workspace"
    engine_root.mkdir()
    workspace.mkdir()
    user = User(id="u-1", email="someone@example.com", role="authenticated")
    store = store_for_project(workspace, engine_root=engine_root, user=user)
    assert isinstance(store, FileCredentialStore)


def test_store_for_project_resolves_paths_before_comparing(tmp_path: Path) -> None:
    """`engine/../engine` is the engine, and a bare string comparison would let it through."""
    from pravrudhi.api.identity import User

    engine_root = tmp_path / "engine"
    engine_root.mkdir()
    aliased = engine_root / ".." / "engine"
    user = User(id="u-1", email="someone@example.com", role="authenticated")
    with pytest.raises(CredentialBoundaryError):
        store_for_project(aliased, engine_root=engine_root, user=user)


# ---------------------------------------------------------------------------
# store_for_session(): the same boundary, labeled with `roles.role_of` — proving that
# label never changes which store comes back. This is the property that lets the
# operator's own desktop session stand in for a BYOK user's: if being ADMIN bought a
# way past the boundary above, testing as admin would not test what a user hits.
# ---------------------------------------------------------------------------


def test_store_for_session_gives_the_admin_no_bypass_at_the_engine_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pravrudhi.api.identity import User
    from pravrudhi.api.roles import ADMIN, role_of

    monkeypatch.setenv("PRAVRUDHI_ADMINS", "op-1")
    admin = User(id="op-1", email=None, role="authenticated")
    assert role_of(admin) == ADMIN  # sanity: this really is the operator's own account

    with pytest.raises(CredentialBoundaryError):
        creds.store_for_session(tmp_path, engine_root=tmp_path, user=admin)


def test_store_for_session_resolves_the_same_store_for_admin_and_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different labels, identical access: the admin session is a valid proxy for a BYOK user's precisely
    because nothing here treats it differently once the boundary check has passed."""
    from pravrudhi.api.identity import User
    from pravrudhi.api.roles import ADMIN, USER, role_of

    engine_root = tmp_path / "engine"
    workspace = tmp_path / "workspace"
    engine_root.mkdir()
    workspace.mkdir()

    monkeypatch.setenv("PRAVRUDHI_ADMINS", "op-1")
    admin = User(id="op-1", email=None, role="authenticated")
    plain = User(id="u-2", email=None, role="authenticated")
    assert role_of(admin) == ADMIN
    assert role_of(plain) == USER

    admin_session = creds.store_for_session(workspace, engine_root=engine_root, user=admin)
    plain_session = creds.store_for_session(workspace, engine_root=engine_root, user=plain)
    assert admin_session.role == ADMIN
    assert plain_session.role == USER

    admin_session.store.put("openai", FAKE_OPENAI_KEY)
    stored = plain_session.store.get("openai")
    assert stored is not None and stored.reveal() == FAKE_OPENAI_KEY, (
        "admin and user sessions must resolve to the same workspace store, not different ones"
    )


# ---------------------------------------------------------------------------
# PROVIDERS registry sanity.
# ---------------------------------------------------------------------------


def test_providers_registry_covers_required_providers() -> None:
    for provider_id in ("openai", "anthropic", "google", "alibaba", "openai-compatible"):
        assert provider_id in creds.PROVIDERS


class TestSecretCannotBeSerialised:
    """The routes a careless caller takes, each of which leaked the raw key before.

    `Secret` was a frozen dataclass and `dataclasses.asdict` walked straight past its `__repr__` and returned the
    value. This codebase serialises records with `asdict` in objectives, memory, subagents and the routing log, so
    a Secret reaching any of them would have written a live key into a JSON line on disk. The tests below are the
    adversarial probe that found it, kept so the leak cannot come back.
    """

    KEY = "sk-proj-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJKKKK"

    def _secret(self):
        from pravrudhi.application.credentials import Secret

        return Secret("openai", self.KEY)

    def test_asdict_refuses_rather_than_returning_the_key(self) -> None:
        import dataclasses

        with pytest.raises(TypeError):
            dataclasses.asdict(self._secret())

    def test_pickling_refuses(self) -> None:
        import pickle

        with pytest.raises(TypeError):
            pickle.dumps(self._secret())

    def test_there_is_no_dunder_dict_to_dump(self) -> None:
        with pytest.raises(AttributeError):
            _ = self._secret().__dict__

    def test_no_string_conversion_shows_the_key(self) -> None:
        s = self._secret()
        for rendered in (repr(s), str(s), f"{s}", f"{s}", f"{s!r}"):
            assert self.KEY not in rendered
            assert "openai" in rendered

    def test_it_is_immutable(self) -> None:
        with pytest.raises(AttributeError):
            self._secret().provider = "anthropic"  # type: ignore[misc]

    def test_reveal_is_the_one_deliberate_escape_hatch(self) -> None:
        s = self._secret()
        assert s.reveal() == self.KEY
        assert s.value == self.KEY
