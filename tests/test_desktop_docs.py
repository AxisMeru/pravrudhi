"""The desktop credential rule has two halves, and only one of them was ever written down.

The operator's instruction of 2026-09-07 (already the docstring of `tests/test_byok_boundary.py`) is: a product
user brings their own provider key, but an admin caller (`roles.role_of` returning `ADMIN`) on the Studio edition
reaches the engine's own core credentials and the Lite Plan routing table (`configs/routing.yaml`) directly, with
no bring-your-own step. `docs/DESKTOP.md` carried neither half before this test existed. It fails if either one
falls back out.
"""

from __future__ import annotations

from pathlib import Path

DESKTOP_DOC = Path(__file__).resolve().parent.parent / "docs" / "DESKTOP.md"


def _text() -> str:
    return DESKTOP_DOC.read_text()


def test_states_the_products_bring_your_own_rule() -> None:
    text = _text()
    assert "bring" in text.lower() and "own" in text.lower(), "the product's bring-your-own rule is not stated"
    assert "/api/providers" in text, "the route that lets a user bring and configure their own key is not named"


def test_states_the_admin_studio_core_credentials_rule() -> None:
    text = _text()
    assert "role_of" in text, "the admin caller is not identified by roles.role_of"
    assert "ADMIN" in text
    assert "Lite Plan" in text, "the Lite Plan routing table is not named"
    assert "configs/routing.yaml" in text
    assert "without" in text.lower() and "bring" in text.lower(), (
        "the doc does not say the admin/Studio path skips the bring-your-own step"
    )
