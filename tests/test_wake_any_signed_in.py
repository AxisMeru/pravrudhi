"""#1045: `PRAVRUDHI_WAKE_ANY_SIGNED_IN` (exactly "1"): any verified, non-anonymous account with an e-mail may ask for the
judges to be woken; the PRAVRUDHI_WAKERS allowlist stays the fallback. Invented ids and addresses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.api import identity
from pravrudhi.application import wake_policy as WP
from tests.test_cold_start_routes import STATUS, WAKE, Rig

NOT_ALLOWED = {"error": "judges_offline", "wake": "not_allowed"}
MEMBER = identity.User("u-member", "member@team.example.test", "authenticated")
LISTED = frozenset({"u-listed"})


def _ok(user: Any, *, wakers: frozenset[str] = LISTED, auth: bool = True, flag: bool = True) -> bool:
    return WP.may_wake(user, auth_enabled=auth, wakers=wakers, any_signed_in=flag)


def test_the_flag_is_exactly_the_string_one() -> None:
    assert WP.any_signed_in_enabled({"PRAVRUDHI_WAKE_ANY_SIGNED_IN": "1"}) is True
    for bad in ("true", "01", " 1", "1 ", "yes", "on", "0", ""):
        assert WP.any_signed_in_enabled({"PRAVRUDHI_WAKE_ANY_SIGNED_IN": bad}) is False, bad
    assert WP.any_signed_in_enabled({}) is False


def test_a_signed_in_member_not_on_the_list_wakes_only_with_the_flag() -> None:
    assert _ok(MEMBER, flag=True) is True
    assert _ok(MEMBER, flag=False) is False  # the allowlist is the only rule when the flag is off


def test_anonymous_accounts_never_wake_and_neither_does_a_missing_or_blank_email() -> None:
    assert _ok(identity.User("u-anon", "x@team.example.test", "authenticated", is_anonymous=True)) is False
    assert _ok(identity.User("u-phone", None, "authenticated")) is False
    assert _ok(identity.User("u-blank", "   ", "authenticated")) is False
    assert (
        _ok(identity.User("u-listed", "x@team.example.test", "authenticated", is_anonymous=True)) is False
    )  # not even a listed id


def test_signed_out_auth_off_blank_id_and_odd_objects_never_wake() -> None:
    assert _ok(None) is False
    assert _ok(MEMBER, auth=False) is False
    assert _ok(identity.User("  ", "x@team.example.test", "authenticated")) is False
    assert _ok(object()) is False


def test_the_allowlist_fallback_still_works_with_the_flag_on_and_off() -> None:
    listed = identity.User("u-listed", None, "authenticated")  # no e-mail: only the list can let this one in
    assert _ok(listed, flag=True) is True and _ok(listed, flag=False) is True
    assert _ok(identity.User("u-x", None, "authenticated"), flag=True) is False


def test_the_role_claim_and_the_admin_list_never_grant_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-admin")
    assert _ok(identity.User("u-admin", None, "admin"), wakers=frozenset()) is False


def test_is_anonymous_has_a_default_so_existing_constructors_keep_working() -> None:
    assert identity.User("u", "e@x.test", "authenticated").is_anonymous is False


def test_is_anonymous_is_read_from_the_verified_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    monkeypatch.setattr(
        identity, "verify_token", lambda token: {"sub": "u-anon", "email": None, "role": "authenticated", "is_anonymous": True}
    )
    assert identity.user_from_headers({"authorization": "Bearer t"}).is_anonymous is True  # type: ignore[union-attr]
    monkeypatch.setattr(identity, "verify_token", lambda token: {"sub": "u-m", "email": "m@x.test", "role": "authenticated"})
    assert identity.user_from_headers({"authorization": "Bearer t"}).is_anonymous is False  # type: ignore[union-attr]
    monkeypatch.setattr(identity, "verify_token", lambda token: {"sub": "u-m", "email": "m@x.test", "is_anonymous": "true"})
    assert (
        identity.user_from_headers({"authorization": "Bearer t"}).is_anonymous is False
    )  # only a real True counts as set; the project setting keeps anon off


def test_is_anonymous_is_read_from_the_introspection_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://proj.example.test")
    identity._INTROSPECT_CACHE.clear()

    class _Resp:
        status_code = 200

        def __init__(self, body: dict[str, Any]) -> None:
            self._b = body

        def json(self) -> dict[str, Any]:
            return self._b

    claims = identity._introspect(
        "tok-anon", lambda url, headers=None: _Resp({"id": "u-anon", "email": "", "is_anonymous": True})
    )  # type: ignore[arg-type]
    assert claims["is_anonymous"] is True
    claims = identity._introspect("tok-m", lambda url, headers=None: _Resp({"id": "u-m", "email": "m@x.test"}))  # type: ignore[arg-type]
    assert claims["is_anonymous"] is False
    identity._INTROSPECT_CACHE.clear()


# ---- through the real routes ------------------------------------------------------------------------------------------------


def test_with_the_flag_a_member_who_is_not_listed_can_ask_for_a_wake_and_without_it_cannot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    off = Rig(tmp_path, monkeypatch, user=MEMBER)
    assert off.c.post(WAKE).json() == NOT_ALLOWED
    monkeypatch.setenv("PRAVRUDHI_WAKE_ANY_SIGNED_IN", "1")
    on = Rig(tmp_path, monkeypatch, user=MEMBER)
    for res in (on.c.post(WAKE), on.c.get(STATUS)):
        assert res.json() != NOT_ALLOWED, res.text


@pytest.mark.parametrize("value", ["true", "01", " 1", "yes", ""])
def test_a_flag_that_is_not_exactly_one_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_WAKE_ANY_SIGNED_IN", value)
    r = Rig(tmp_path, monkeypatch, user=MEMBER)
    assert r.c.post(WAKE).json() == NOT_ALLOWED


def test_the_flag_is_read_once_at_startup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_WAKE_ANY_SIGNED_IN", "1")
    r = Rig(tmp_path, monkeypatch, user=MEMBER)
    monkeypatch.setenv("PRAVRUDHI_WAKE_ANY_SIGNED_IN", "0")  # a later change does nothing
    assert r.c.post(WAKE).json() != NOT_ALLOWED
    monkeypatch.delenv("PRAVRUDHI_WAKE_ANY_SIGNED_IN")
    off = Rig(tmp_path, monkeypatch, user=MEMBER)
    monkeypatch.setenv("PRAVRUDHI_WAKE_ANY_SIGNED_IN", "1")  # nor does a later turn-on
    assert off.c.post(WAKE).json() == NOT_ALLOWED


def test_anonymous_and_no_email_accounts_are_refused_by_the_routes_with_the_flag_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAVRUDHI_WAKE_ANY_SIGNED_IN", "1")
    for u in (
        identity.User("u-anon", "a@x.test", "authenticated", is_anonymous=True),
        identity.User("u-nomail", None, "authenticated"),
    ):
        r = Rig(tmp_path, monkeypatch, user=u)
        assert r.c.post(WAKE).json() == NOT_ALLOWED and r.c.get(STATUS).json() == NOT_ALLOWED
    assert Rig(tmp_path, monkeypatch, user=None).c.post(WAKE).json() == NOT_ALLOWED  # signed out


def test_with_auth_disabled_nobody_wakes_even_with_the_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_WAKE_ANY_SIGNED_IN", "1")
    r = Rig(tmp_path, monkeypatch, user=MEMBER)
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    assert r.c.post(WAKE).json() == NOT_ALLOWED
