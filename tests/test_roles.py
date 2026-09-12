"""Who is allowed to make this engine improve itself, and who is only allowed to use it.

The product has two audiences and they want opposite things. A user brings their own model, their own agent or
their own repository, and wants Pravrudhi to improve *that*. The operator builds Pravrudhi itself: the kernel,
the search, the nights that make the engine better. Those are different surfaces and until now there was no way
to tell the two callers apart, because the only identity the engine carried was Supabase's own "authenticated"
claim, which everyone who signs in has.

So a role is resolved from an allowlist the operator controls, never from anything the caller can assert. A token
cannot claim to be admin: the claim is compared against a list held on the machine. Everyone else is a user, and
a user is not a lesser account — it is the audience the product is for.

Being unable to decide is not the same as being denied. With authentication switched off entirely, which is how
a single-operator machine runs, there is nobody to deny and the local caller is the operator by construction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.api.identity import User
from pravrudhi.api.roles import ADMIN, USER, Role, admin_ids, require_admin, role_of


def _user(uid: str = "u-1", email: str | None = "someone@example.com") -> User:
    return User(id=uid, email=email, role="authenticated")


class TestTheRoleComesFromTheMachineNotTheToken:
    def test_an_account_on_the_allowlist_is_the_operator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        assert role_of(_user("u-1")) == ADMIN

    def test_an_account_that_is_not_listed_is_a_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        assert role_of(_user("u-2")) == USER

    def test_an_email_may_be_listed_instead_of_an_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Supabase ids are opaque, so an operator listing themselves reaches for their address."""
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "someone@example.com")
        assert role_of(_user("u-9", "someone@example.com")) == ADMIN

    def test_the_listed_email_is_matched_without_regard_to_case(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "Someone@Example.com")
        assert role_of(_user("u-9", "someone@example.com")) == ADMIN

    def test_a_token_claiming_to_be_admin_is_still_a_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The whole point of an allowlist: the caller does not get to assert this."""
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        forged = User(id="u-2", email="attacker@example.com", role="admin")
        assert role_of(forged) == USER

    def test_an_empty_allowlist_makes_everyone_a_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A machine that names no operator has no operator. Defaulting the other way would be a hole."""
        monkeypatch.delenv("PRAVRUDHI_ADMINS", raising=False)
        assert role_of(_user()) == USER

    def test_the_list_tolerates_spacing_and_blank_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", " u-1 , , u-3 ")
        assert admin_ids() == frozenset({"u-1", "u-3"})


class TestNobodySignedIn:
    def test_with_authentication_off_the_local_caller_is_the_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This is how a single-operator machine runs, and the engine already refuses that mode when deployed."""
        monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
        assert role_of(None) == ADMIN

    def test_with_authentication_on_an_anonymous_caller_is_not_the_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
        assert role_of(None) == USER


class TestRequireAdmin:
    def test_the_operator_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        assert require_admin(_user("u-1")).id == "u-1"

    def test_a_user_is_refused_with_a_reason_that_does_not_leak_the_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import HTTPException

        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        with pytest.raises(HTTPException) as caught:
            require_admin(_user("u-2"))
        assert caught.value.status_code == 403
        assert "u-1" not in str(caught.value.detail), "the refusal must not disclose who is on the list"


def test_the_two_roles_are_the_only_ones() -> None:
    """A third role is a design decision, not something that appears by accident in a string comparison."""
    assert set(Role) == {ADMIN, USER}


class TestEveryRouteIsClassified:
    """A route nobody classified must fail a test, not quietly become public.

    Sixty routes exist and the split between them is a product decision, not an implementation detail. Leaving
    the classification implicit — a prefix rule, or a default — means the next surface someone adds inherits
    whatever the default happened to be, and the one that matters most is exactly the one somebody adds in a
    hurry.
    """

    def _paths(self) -> set[str]:
        from pathlib import Path

        from fastapi.routing import APIRoute, APIRouter

        from pravrudhi.api.server import create_app

        def walk(routes):  # type: ignore[no-untyped-def]
            for route in routes:
                if isinstance(route, APIRoute):
                    yield route
                else:
                    sub = getattr(route, "original_router", None)
                    if isinstance(sub, APIRouter):
                        yield from walk(sub.routes)

        return {r.path for r in walk(create_app(Path(".")).routes)}

    def test_no_route_is_left_unclassified(self) -> None:
        from pravrudhi.api.roles import ADMIN_ONLY, USER_FACING

        missing = sorted(self._paths() - ADMIN_ONLY - USER_FACING)
        assert not missing, (
            f"these routes belong to neither audience; add each to ADMIN_ONLY or USER_FACING: {missing}"
        )

    def test_no_route_is_claimed_by_both(self) -> None:
        from pravrudhi.api.roles import ADMIN_ONLY, USER_FACING

        assert not (ADMIN_ONLY & USER_FACING)

    def test_the_lists_name_no_route_that_has_gone(self) -> None:
        """A stale entry is how a list stops describing the application it claims to describe."""
        from pravrudhi.api.roles import ADMIN_ONLY, USER_FACING

        stale = sorted((ADMIN_ONLY | USER_FACING) - self._paths())
        assert not stale, f"these are classified but no longer exist: {stale}"

    def test_the_engine_improving_itself_is_never_user_facing(self) -> None:
        """The specific thing this separation exists for."""
        from pravrudhi.api.roles import ADMIN_ONLY, USER_FACING

        for path in ("/api/nights", "/api/candidates", "/api/search", "/api/swarm", "/api/inbox"):
            assert path in ADMIN_ONLY and path not in USER_FACING, path


class TestTheGateActuallyRefuses:
    """Classifying a route and enforcing it are different things, and only one of them protects anything."""

    def _client(self, tmp_path, monkeypatch, *, admins: str = "u-1"):  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        from pravrudhi.api.server import create_app

        monkeypatch.setenv("PRAVRUDHI_AUTH", "optional")
        monkeypatch.setenv("PRAVRUDHI_ADMINS", admins)
        return TestClient(create_app(tmp_path), headers={"host": "127.0.0.1:8008"})

    def test_an_anonymous_caller_is_refused_an_operator_surface(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        with self._client(tmp_path, monkeypatch) as client:
            assert client.get("/api/nights").status_code == 403

    def test_a_write_route_on_the_same_surface_is_refused_the_same_way(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """r-e84f8a50 added `POST /api/requests` (originate an ask) to the same `/requests` path `GET`,
        `advance` and `evidence` already share; the gate applies per path, not per method, so a non-admin
        caller must be refused here too - past the local-token check (LocalGuard requires it for every
        state-changing method regardless of role), which is why one is supplied here and the anonymous GET
        test above needs none."""
        from pravrudhi.api.localguard import TOKEN_HEADER, app_token

        with self._client(tmp_path, monkeypatch) as client:
            resp = client.post(
                "/api/requests", json={"text": "an ask from someone who is not the operator"},
                headers={TOKEN_HEADER: app_token(tmp_path)},
            )
            assert resp.status_code == 403

    def test_the_same_caller_still_reaches_the_product(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """A refusal that also broke the user surfaces would be a worse bug than the one being fixed."""
        with self._client(tmp_path, monkeypatch) as client:
            assert client.get("/api/health").status_code == 200

    def test_with_authentication_off_the_local_operator_is_not_locked_out(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """How a single-operator machine runs. guard_boot already refuses this mode on a deployment platform."""
        from fastapi.testclient import TestClient

        from pravrudhi.api.server import create_app

        monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
        monkeypatch.delenv("PRAVRUDHI_ADMINS", raising=False)
        with TestClient(create_app(tmp_path), headers={"host": "127.0.0.1:8008"}, raise_server_exceptions=False) as client:
            # Not 200: this scratch directory holds no ledger, so the route itself has nothing to answer with.
            # What matters here is that the caller was not turned away at the door.
            assert client.get("/api/nights").status_code != 403


class TestAProductInstallServesNoStudioSurface:
    """Role decided access, and with authentication off a local caller is the operator — so the operator's own
    product install served the ledger, the nights, the promotion inbox, the swarm and the fleet. Those are the
    surfaces of Pravrudhi improving itself. A product whose claim is "improve your own work" carrying all of
    them is Studio with a different name on the window."""

    @staticmethod
    def _client(tmp_path: Path):
        from fastapi.testclient import TestClient

        from pravrudhi.api.server import create_app
        from pravrudhi.application.init import init_project

        init_project(tmp_path)
        return TestClient(create_app(tmp_path), base_url="http://127.0.0.1:8008")

    def test_a_studio_surface_is_refused_on_a_product_engine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
        client = self._client(tmp_path)

        for path in ("/api/candidates", "/api/nights", "/api/inbox", "/api/swarm"):
            answer = client.get(path)
            assert answer.status_code == 404, f"{path} was served by a product install ({answer.status_code})"

    def test_the_same_surface_is_served_on_a_studio_engine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The refusal must come from the edition, not from the surface being broken."""
        monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
        client = self._client(tmp_path)

        for path in ("/api/candidates", "/api/nights"):
            assert client.get(path).status_code != 404, f"{path} is missing on Studio too"

    def test_a_users_own_surfaces_are_untouched_on_a_product_engine(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The point is to remove what belongs to the engine's self-improvement, not to cripple the product."""
        monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
        client = self._client(tmp_path)

        for path in ("/api/health", "/api/objectives", "/api/memory", "/api/recipes"):
            assert client.get(path).status_code != 404, f"{path} vanished from the product"
