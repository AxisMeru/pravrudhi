"""The published snapshot must say whether the PRODUCT loop is moving, not only Studio's.

`pravrudhi-publish.service` runs `publish --root /home/ss/projects/pravrudhi`, the Studio root alone. Verified
2026-09-09: the product's own requests -- including `r-3981d7e0`, the one its loop was parked on -- appear in
none of Studio's 49, because they live in a different workspace with a different ledger. The published snapshot
is the only surface a cloud watcher can reach, so the product loop was unobservable, and the stall-watch
routine could only report it "UNTRACEABLE -- not confirmed stalled, not confirmed healthy". A feedback signal
nothing can read is not a feedback signal.

Spec: docs/superpowers/specs/2026-09-09-prabhasa-nyaya-measurement-design.md, gap G1 and card N2.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application import requests
from pravrudhi.application.demo_export import build_demo
from pravrudhi.application.init import init_project


def test_no_product_install_is_absent_rather_than_an_error(tmp_path: Path) -> None:
    """Most checkouts have no product install beside them, and that is not a fault."""
    init_project(tmp_path)
    snap = build_demo(tmp_path, product_root=tmp_path / "nonexistent")

    assert "product" in snap, "the key must always be present, so a reader can tell absent from broken"
    assert snap["product"] is None


def test_a_product_install_contributes_its_own_requests(tmp_path: Path) -> None:
    studio, product = tmp_path / "studio", tmp_path / "product"
    init_project(studio)
    init_project(product)
    requests.capture(product, "the product's own ask")

    snap = build_demo(studio, product_root=product)

    assert snap["product"] is not None
    assert snap["product"]["requests"]["total"] == 1
    assert snap["product"]["root"].endswith("product")


def test_the_two_workspaces_do_not_bleed_into_each_other(tmp_path: Path) -> None:
    """Two workspaces, two ledgers. Studio's own section must not absorb the product's asks, or the snapshot
    would report one loop's work as the other's."""
    studio, product = tmp_path / "studio", tmp_path / "product"
    init_project(studio)
    init_project(product)
    requests.capture(product, "the product's own ask")
    requests.capture(studio, "studio's own ask")

    snap = build_demo(studio, product_root=product)

    assert snap["requests"]["total"] == 1
    assert snap["product"]["requests"]["total"] == 1
    studio_texts = [r["text"] for r in snap["requests"]["requests"]]
    assert "the product's own ask" not in studio_texts


def test_a_product_whose_state_is_unreadable_still_reports_its_root(tmp_path: Path) -> None:
    """Partial is better than absent: knowing an install exists but cannot be read is itself the finding."""
    studio, product = tmp_path / "studio", tmp_path / "product"
    init_project(studio)
    (product / ".pravrudhi").mkdir(parents=True)  # a marker, but no initialised state behind it

    snap = build_demo(studio, product_root=product)

    assert snap["product"] is not None
    assert snap["product"]["root"].endswith("product")
