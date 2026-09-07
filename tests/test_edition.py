"""Two products, one engine, and a name that says which one you are looking at.

The operator's instruction on 2026-09-07: the builder and its end product need different names, or they will be
confused for each other. They settled it as Pravrudhi Studio for the engine that builds and improves Pravrudhi,
and Pravrudhi for what a user installs to build and improve their own work. Both carry the whole loop; the
difference is what it is pointed at.

That is a role, not a build. The same code serves both because the same code *is* both — Studio is this engine
improving itself, Pravrudhi is this engine improving a user's artifact — and the role split already decides
which surfaces a caller reaches. So the edition is derived from the role rather than compiled in, and one binary
introduces itself correctly to whoever opened it.

Deriving it has a second benefit worth the test: an operator who signs in as a user, to see what their own
product feels like, is shown the product's name. If the edition were compiled in, they would be told they were
in Studio while looking at something else.
"""

from __future__ import annotations

import pytest

from pravrudhi.api.edition import PRODUCT, STUDIO, edition_for, tagline_for
from pravrudhi.api.identity import User


def _user(uid: str = "u-2") -> User:
    return User(id=uid, email="someone@example.com", role="authenticated")


class TestWhichEditionAmI:
    def test_the_operator_is_in_studio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        assert edition_for(_user("u-1")) == STUDIO

    def test_a_user_is_in_the_product(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        assert edition_for(_user("u-2")) == PRODUCT

    def test_a_local_machine_with_no_authentication_is_studio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """How the operator runs it on their own hardware, which is where the building happens."""
        monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
        assert edition_for(None) == STUDIO

    def test_an_anonymous_caller_with_authentication_on_sees_the_product(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
        assert edition_for(None) == PRODUCT


class TestTheNamesThemselves:
    def test_they_are_different(self) -> None:
        """The whole point: two names, because one name for both is what confused things."""
        assert STUDIO != PRODUCT

    def test_the_product_is_the_shorter_name(self) -> None:
        """A user installs Pravrudhi. Studio is the edition that builds Pravrudhi, and says so."""
        assert PRODUCT == "Pravrudhi"
        assert STUDIO.startswith(PRODUCT) and STUDIO.endswith("Studio")

    def test_each_says_what_it_improves(self) -> None:
        """Studio improves this engine; the product improves the user's work. The recursion is the product."""
        assert "Pravrudhi" in tagline_for(STUDIO)
        assert tagline_for(PRODUCT) != tagline_for(STUDIO)
        assert tagline_for(PRODUCT)

    def test_neither_tagline_uses_the_internal_vocabulary(self) -> None:
        """A user-facing surface carries no Sanskrit terms; that rule predates this split and still holds."""
        for name in (STUDIO, PRODUCT):
            lowered = tagline_for(name).lower()
            for term in ("pramana", "sakshi", "citta", "agama", "anumana", "pratyaksha"):
                assert term not in lowered
