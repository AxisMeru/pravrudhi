"""`pravrudhi app`: the product, served locally by the engine.

One process, one port. The engine's API and the built web app are served together, so a user runs a single command
and opens a browser; there is no account, no cloud and no second service to start. The same frontend build is what
the hosted site deploys, pointed at a demo backend instead of this one, so the local app and the public app cannot
drift apart.

Route order matters: the API is registered first, the static frontend is mounted last as a catch-all, and any
client-side route (a browser refresh on /runs) falls back to index.html the way a static export expects.
"""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pravrudhi.api.runs import build_router
from pravrudhi.api.server import create_app

DEFAULT_PORT = 8008
PACKAGED_FRONTEND = Path(__file__).resolve().parents[1] / "assets" / "frontend"


FRONTEND_ENV = "PRAVRUDHI_FRONTEND_DIR"
"""A host that ships its own interface names it here (ADR-0049: the product repository serves the product's
frontend through the same engine; the wheel's packaged interface is Studio's). A named directory that has no
index.html is refused rather than silently replaced by Studio's interface — a product that lost its frontend
must say so, not show the operator's."""


def frontend_dir(root: Path) -> Path | None:
    """The static export, if it has been built. Absent means API-only, which is still a working engine."""
    named = os.environ.get(FRONTEND_ENV, "").strip()
    if named:
        out = Path(named)
        if (out / "index.html").exists():
            return out
        raise FileNotFoundError(f"{FRONTEND_ENV}={named} has no index.html; refusing to serve another edition's interface")
    for out in (Path(root) / "app" / "frontend" / "out", PACKAGED_FRONTEND):
        if (out / "index.html").exists():
            return out
    return None


class _SpaStatic(StaticFiles):
    """Static files for a Next.js static export.

    The export writes one file per route as `<route>.html` — `runs.html`, `machines.html` — alongside a directory of
    the same name holding the route's data payloads. Starlette maps neither: a request for `/runs` finds the
    directory, looks for an `index.html` inside it, does not find one, and (because `html=True` and the export also
    ships a `404.html`) answers 404. Every route but `/` was therefore either a 404 or the home page when the engine
    served the interface itself. The public site did not show this because Vercel does the `.html` mapping for you.

    So: try the path as given, then `<path>.html`, and only then fall back to the shell.
    """

    async def get_response(self, path: str, scope: Any) -> Any:
        candidates = [path]
        stripped = path.rstrip("/")
        if stripped and not stripped.endswith(".html"):
            candidates.append(f"{stripped}.html")
        for candidate in candidates:
            try:
                response = await super().get_response(candidate, scope)
            except Exception:  # noqa: BLE001 - StaticFiles raises HTTPException for a missing file
                continue
            if getattr(response, "status_code", 500) < 400:
                return response
        index = Path(self.directory) / "index.html"  # type: ignore[arg-type]
        if index.exists():
            return FileResponse(index)
        return await super().get_response(path, scope)


def build_app(root: Path) -> FastAPI:
    root = Path(root)
    app = create_app(root)
    app.include_router(build_router(root))
    fe = frontend_dir(root)
    if fe is not None:
        app.mount("/", _SpaStatic(directory=str(fe), html=True), name="frontend")
    return app


def serve(root: Path, *, host: str = "127.0.0.1", port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    import uvicorn

    app = build_app(root)
    if open_browser:
        webbrowser.open(f"http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port, log_level="info")
