"""Resolve the web interface in checkouts and installed wheels."""

from __future__ import annotations

from pathlib import Path

import pytest

from pravrudhi.application import app_serve


def test_checkout_preferred(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = tmp_path / "app" / "frontend" / "out"
    checkout.mkdir(parents=True)
    (checkout / "index.html").write_text("checkout")
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "index.html").write_text("packaged")
    monkeypatch.setattr(app_serve, "PACKAGED_FRONTEND", packaged)

    assert app_serve.frontend_dir(tmp_path) == checkout


def test_packaged_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "index.html").write_text("packaged")
    monkeypatch.setattr(app_serve, "PACKAGED_FRONTEND", packaged)

    assert app_serve.frontend_dir(tmp_path) == packaged


def test_neither_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_serve, "PACKAGED_FRONTEND", tmp_path / "packaged")

    assert app_serve.frontend_dir(tmp_path) is None


def test_a_host_named_frontend_wins_over_checkout_and_wheel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0049: the product repository ships its own interface and names it; the engine serves that one."""
    checkout = tmp_path / "app" / "frontend" / "out"
    checkout.mkdir(parents=True)
    (checkout / "index.html").write_text("checkout")
    named = tmp_path / "product-ui"
    named.mkdir()
    (named / "index.html").write_text("product")
    monkeypatch.setenv(app_serve.FRONTEND_ENV, str(named))

    assert app_serve.frontend_dir(tmp_path) == named


def test_a_named_frontend_without_an_index_is_refused_not_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    packaged = tmp_path / "packaged"
    packaged.mkdir()
    (packaged / "index.html").write_text("studio")
    monkeypatch.setattr(app_serve, "PACKAGED_FRONTEND", packaged)
    monkeypatch.setenv(app_serve.FRONTEND_ENV, str(tmp_path / "missing"))

    with pytest.raises(FileNotFoundError):
        app_serve.frontend_dir(tmp_path)
