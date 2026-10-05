"""Which of the two products a caller is looking at.

The operator settled this on 2026-09-07: the builder and its end product need different names, or they are
confused for each other. Pravrudhi Studio is the engine that builds and improves Pravrudhi. Pravrudhi is what a
user installs to build and improve their own work — their app, their model, their agent. Both carry the whole
improvement loop; the difference is what it is pointed at, and the recursion is deliberate.

The edition is derived from the role rather than compiled into a build, because the same code genuinely is both.
Studio is this engine improving itself; the product is this engine improving somebody else's artifact. The role
split in `roles.py` already decides which surfaces a caller reaches, so one binary can introduce itself
correctly to whoever opened it.

Deriving it has a second benefit. An operator who signs in as an ordinary user, to see what their own product
feels like, is shown the product's name — which is the truth of what they are looking at. A compiled-in edition
would insist they were in Studio while showing them something else.
"""

from __future__ import annotations

from pravrudhi.api.identity import User
from pravrudhi.api.roles import ADMIN, role_of
from pravrudhi.deployment import EDITION_ENV, RELEASE_MARKER, resolved_edition
from pravrudhi.deployment import is_release_install as _is_release_install

PRODUCT = "Pravrudhi"
"""What a user installs. The shorter name goes to the thing more people will hold."""

STUDIO = "Pravrudhi Studio"
"""The edition that builds Pravrudhi itself."""

_TAGLINES = {
    STUDIO: "Improve Pravrudhi itself, on your own hardware, with the evidence in front of you.",
    PRODUCT: "Improve your own model, agent or app, on your own hardware, while you watch.",
}


"""`PRAVRUDHI_EDITION` (`pravrudhi.deployment.EDITION_ENV`) is set to `product` by a released build, which forces the
product edition whatever the role says.

Without it a released install running with authentication off would call itself Studio, because a local caller
with nobody to identify is the operator by construction — correct on the machine that builds this engine and
wrong on a machine that merely runs it. Studio is the operator's edition and is not released to anyone, so the
build says which it is and the role decides only within the operator's own checkout."""


def is_release_install() -> bool:
    """Whether this package is running from an installed release rather than a development checkout."""
    return _is_release_install()


def engine_edition() -> str:
    """Which product *this install* is, independent of who is asking.

    `edition_for` answers a different question — what to call this engine for a given caller, so an operator
    signing in as an ordinary user sees the product's name. That is right for a label and wrong for a surface:
    access decided by role alone meant the operator's own product install served every Studio surface, because
    with authentication off a local caller is the operator by construction. A product that shows the ledger,
    the nights and the promotion inbox is Studio with a different name on it.
    """
    # One resolved edition (`pravrudhi.deployment`), shared with the whole-surface Studio gate and the tenant-vendor
    # carve-out: an unlabelled container or release install is the product, never the Studio routes.
    return PRODUCT if resolved_edition() == "product" else STUDIO  # a dev checkout is where this engine improves itself


def is_studio_engine() -> bool:
    """Whether this install may serve the surfaces of Pravrudhi improving itself."""
    return engine_edition() == STUDIO


def edition_for(user: User | None) -> str:
    """The product name to show this caller."""
    if resolved_edition() == "product":
        return PRODUCT
    return STUDIO if role_of(user) is ADMIN else PRODUCT


def tagline_for(edition: str) -> str:
    """One sentence saying what this edition improves. Plain language: a user-facing surface carries none of
    this project's internal vocabulary, a rule that predates the split and still holds."""
    return _TAGLINES.get(edition, _TAGLINES[PRODUCT])


__all__ = [
    "EDITION_ENV", "PRODUCT", "RELEASE_MARKER", "STUDIO",
    "edition_for", "engine_edition", "is_release_install", "is_studio_engine", "tagline_for",
]
