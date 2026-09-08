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


class TestStudioIsNeverReleased:
    """The operator's instruction on 2026-09-07: Studio is for the operator, not for any other user and not for
    a public release.

    The role rule alone does not achieve that. A local caller with authentication off is the operator by
    construction — right on the machine that builds this engine, wrong on a machine that merely runs a release
    of it, where the same rule would have every user greeted by Studio. So a released build says which edition
    it is, and the role decides only inside the operator's own checkout.
    """

    def test_a_released_build_is_the_product_even_with_no_authentication(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
        monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
        assert edition_for(None) == PRODUCT

    def test_a_released_build_is_the_product_even_for_a_listed_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator running the release is using the product, and it should say so."""
        monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "u-1")
        assert edition_for(_user("u-1")) == PRODUCT

    def test_the_development_checkout_still_shows_studio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PRAVRUDHI_EDITION", raising=False)
        monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
        assert edition_for(None) == STUDIO

    def test_an_unrecognised_value_does_not_silently_force_the_product(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the exact marker a release sets counts; a typo must not quietly change what this is."""
        monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
        monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
        assert edition_for(None) == STUDIO


class TestARunningReleaseKnowsItIsOne:
    """A release recognises itself from where it is unpacked, so the installs already on the operator's two
    machines behave correctly rather than only the next one, and nobody has to remember to set a flag."""

    def test_a_release_path_is_recognised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pravrudhi.api import edition as mod

        monkeypatch.setattr(
            mod, "is_release_install",
            lambda: True,
        )
        monkeypatch.delenv("PRAVRUDHI_EDITION", raising=False)
        monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
        assert mod.edition_for(None) == PRODUCT

    def test_the_marker_matches_the_real_install_layout(self) -> None:
        """The path an update actually unpacks into, on both of the operator's machines."""
        from pravrudhi.api.edition import RELEASE_MARKER

        installed = (
            "/Users/sharath/pravrudhi-release/.pravrudhi/releases/current/.venv/"
            "lib/python3.13/site-packages/pravrudhi/api/edition.py"
        )
        assert RELEASE_MARKER in installed

    def test_this_checkout_is_not_a_release(self) -> None:
        from pravrudhi.api.edition import is_release_install

        assert not is_release_install(), "the development checkout must not be mistaken for an install"

    def test_an_explicit_studio_marker_still_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """So the operator can run a release as Studio deliberately, rather than being locked out of their own."""
        from pravrudhi.api import edition as mod

        monkeypatch.setattr(mod, "is_release_install", lambda: True)
        monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
        monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
        assert mod.edition_for(None) == STUDIO


class TestTheProductIsNotStudioWithADifferentName:
    """The two editions differed by name and nothing a user would notice.

    Surface access was gated on role alone, and with authentication off a local caller is the operator by
    construction — so the operator's own product install served every Studio surface: the ledger, the nights,
    the promotion inbox, the swarm, the fleet. Those are the surfaces of Pravrudhi improving *itself*, and a
    product whose whole claim is "improve your own work" should not carry them whoever is signed in.
    """

    def test_a_product_engine_serves_no_studio_surface_even_to_the_operator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pravrudhi.api.edition import engine_edition, is_studio_engine

        monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
        assert engine_edition() == "Pravrudhi"
        assert is_studio_engine() is False

    def test_a_studio_engine_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pravrudhi.api.edition import engine_edition, is_studio_engine

        monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
        assert engine_edition() == "Pravrudhi Studio"
        assert is_studio_engine() is True

    def test_the_engines_edition_does_not_depend_on_who_is_asking(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`edition_for` answers "what should this caller be shown"; this answers "what is this install". A
        surface has to be decided by the second, or an admin turns a product install into Studio by signing in.
        """
        from pravrudhi.api.edition import engine_edition

        monkeypatch.setenv("PRAVRUDHI_EDITION", "product")
        before = engine_edition()
        monkeypatch.setenv("PRAVRUDHI_ADMINS", "operator@example.com")
        assert engine_edition() == before

    def test_an_unlabelled_development_checkout_is_studio(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The engine's own tree is where Pravrudhi improves itself, so its surfaces belong there."""
        from pravrudhi.api.edition import engine_edition, is_studio_engine

        monkeypatch.delenv("PRAVRUDHI_EDITION", raising=False)
        from pravrudhi.api.edition import is_release_install

        if is_release_install():
            pytest.skip("this checkout is an installed release")
        assert engine_edition() == "Pravrudhi Studio" and is_studio_engine() is True
