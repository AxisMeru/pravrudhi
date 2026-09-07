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

import os
from pathlib import Path

from pravrudhi.api.identity import User
from pravrudhi.api.roles import ADMIN, role_of

PRODUCT = "Pravrudhi"
"""What a user installs. The shorter name goes to the thing more people will hold."""

STUDIO = "Pravrudhi Studio"
"""The edition that builds Pravrudhi itself."""

_TAGLINES = {
    STUDIO: "Improve Pravrudhi itself, on your own hardware, with the evidence in front of you.",
    PRODUCT: "Improve your own model, agent or app, on your own hardware, while you watch.",
}


EDITION_ENV = "PRAVRUDHI_EDITION"
"""Set to `product` by a released build, which forces the product edition whatever the role says.

Without it a released install running with authentication off would call itself Studio, because a local caller
with nobody to identify is the operator by construction — correct on the machine that builds this engine and
wrong on a machine that merely runs it. Studio is the operator's edition and is not released to anyone, so the
build says which it is and the role decides only within the operator's own checkout."""


RELEASE_MARKER = ".pravrudhi/releases/"
"""How an installed release recognises itself, without the installer having to be changed.

A release is unpacked into `<root>/.pravrudhi/releases/<version>/.venv/...`, so the package's own location says
whether it is an installed release or the checkout this engine is developed in. That works for the installs
already on the operator's two machines rather than only for the next one, and it cannot be forgotten the way a
flag written by an installer can.
"""


def is_release_install() -> bool:
    """Whether this package is running from an installed release rather than a development checkout."""
    return RELEASE_MARKER in Path(__file__).resolve().as_posix()


def edition_for(user: User | None) -> str:
    """The product name to show this caller."""
    declared = os.environ.get(EDITION_ENV, "").strip().lower()
    if declared == "product" or (declared != "studio" and is_release_install()):
        return PRODUCT
    return STUDIO if role_of(user) is ADMIN else PRODUCT


def tagline_for(edition: str) -> str:
    """One sentence saying what this edition improves. Plain language: a user-facing surface carries none of
    this project's internal vocabulary, a rule that predates the split and still holds."""
    return _TAGLINES.get(edition, _TAGLINES[PRODUCT])


__all__ = [
    "EDITION_ENV", "PRODUCT", "RELEASE_MARKER", "STUDIO",
    "edition_for", "is_release_install", "tagline_for",
]
