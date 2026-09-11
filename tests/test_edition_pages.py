"""Every frontend page that reaches an ADMIN_ONLY route is classified for the product edition.

The operator opened both desktop editions on 2026-09-11 and saw "almost the same interface": the product's
sidebar filter (`palette.ts::STUDIO_ONLY_PAGES`) was a hand-kept list of seven ids, and parity, search, system,
requests, machines, tour and desktop had shipped since without joining it, so a product user saw the page shell
and a 404 when it fetched. This test derives the truth from `roles.py::ADMIN_ONLY` and the frontend source: a
page that reaches an admin-only route is either Studio-only (hidden and gated) or explicitly MIXED (kept, its
admin-only panel is Studio's), and a new page that reaches one without being classified fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

from pravrudhi.api.roles import ADMIN_ONLY

FRONTEND = Path(__file__).resolve().parents[1] / "app" / "frontend" / "src"
PALETTE = FRONTEND / "lib" / "palette.ts"
SIDEBAR = FRONTEND / "components" / "Sidebar.tsx"
API = FRONTEND / "lib" / "api.ts"
_ROUTE = re.compile(r'["`](/api/[A-Za-z0-9_\-/]*)')


def _ts_set(source: str, name: str) -> set[str]:
    m = re.search(name + r"[^=]*=\s*new Set\(\[(.*?)\]\)", source, re.S)
    assert m, f"{name} not found in palette.ts"
    return set(re.findall(r'"([a-z-]+)"', m.group(1)))


def _api_functions() -> dict[str, set[str]]:
    src = API.read_text()
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"export (?:async )?function (\w+)\s*\([^)]*\)[^{]*\{", src):
        start, depth, i = m.end(), 1, m.end()
        while depth and i < len(src):
            depth += src[i] == "{"
            depth -= src[i] == "}"
            i += 1
        out[m.group(1)] = set(_ROUTE.findall(src[start:i]))
    return out


def _resolve(spec: str) -> Path | None:
    base = FRONTEND / spec[2:]
    for cand in (base.with_suffix(".tsx"), base.with_suffix(".ts"), base / "index.tsx", base / "index.ts"):
        if cand.exists():
            return cand
    return None


def _closure(files: list[Path]) -> set[Path]:
    seen: set[Path] = set()
    stack = list(files)
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        for spec in re.findall(r'from "(@/[^"]+)"', f.read_text()):
            if spec.startswith("@/lib/api"):
                continue
            r = _resolve(spec)
            if r is not None and r not in seen:
                stack.append(r)
    return seen


def _is_admin(path: str) -> bool:
    return any(re.match("^" + re.sub(r"\{[^}]+\}", "[^/]+", a) + "$", path) for a in ADMIN_ONLY)


def admin_routes_by_page() -> dict[str, set[str]]:
    funcs = _api_functions()
    out: dict[str, set[str]] = {}
    for page in sorted(p for p in (FRONTEND / "app").iterdir() if p.is_dir()):
        src = "".join(f.read_text() for f in _closure(list(page.rglob("*.tsx"))))
        used = set(re.findall(r"\b(\w+)\(", src)) & set(funcs)
        paths = set(_ROUTE.findall(src)) | {p for f in used for p in funcs[f]}
        out[page.name] = {p for p in paths if _is_admin(p)}
    return out


def test_every_page_reaching_an_admin_only_route_is_classified() -> None:
    palette = PALETTE.read_text()
    studio_only = _ts_set(palette, "STUDIO_ONLY_PAGES")
    mixed = _ts_set(palette, "MIXED_EDITION_PAGES")
    assert not (studio_only & mixed), "a page cannot be both hidden from the product and kept for it"
    unclassified = {
        page for page, routes in admin_routes_by_page().items() if routes and page not in studio_only | mixed
    }
    assert not unclassified, (
        f"{sorted(unclassified)} reach ADMIN_ONLY routes but are neither STUDIO_ONLY_PAGES nor "
        "MIXED_EDITION_PAGES in palette.ts; a product user would see the page and a 404"
    )


def test_a_mixed_page_still_has_user_facing_work() -> None:
    """MIXED means "kept for the product with a Studio-only panel"; a page whose every route is admin-only is
    not mixed, it is Studio's, and calling it mixed would keep an empty page in the product."""
    palette = PALETTE.read_text()
    funcs = _api_functions()
    for page in _ts_set(palette, "MIXED_EDITION_PAGES"):
        src = "".join(f.read_text() for f in _closure(list((FRONTEND / "app" / page).rglob("*.tsx"))))
        used = set(re.findall(r"\b(\w+)\(", src)) & set(funcs)
        paths = set(_ROUTE.findall(src)) | {p for f in used for p in funcs[f]}
        assert any(not _is_admin(p) for p in paths), f"{page} reaches only admin routes; it is Studio-only, not mixed"


def test_every_sidebar_destination_is_a_palette_page_so_the_filter_can_see_it() -> None:
    """`isStudioOnlyHref` looks a href up in PALETTE_PAGES; a NAV entry missing there can never be hidden, which
    is exactly how parity, search, system and desktop leaked into the product."""
    nav = set(re.findall(r'href: "(/[a-z-]*)"', SIDEBAR.read_text()))
    palette = set(re.findall(r'href: "(/[a-z-]*)"', PALETTE.read_text()))
    assert nav <= palette, f"sidebar destinations absent from PALETTE_PAGES: {sorted(nav - palette)}"


def test_every_classified_page_exists() -> None:
    palette = PALETTE.read_text()
    pages = {p.name for p in (FRONTEND / "app").iterdir() if p.is_dir()}
    for name in _ts_set(palette, "STUDIO_ONLY_PAGES") | _ts_set(palette, "MIXED_EDITION_PAGES"):
        assert name in pages, f"{name} is classified but is not a page under app/frontend/src/app/"
