"""One place that opens the A2 matter store for every slice that reads through A2, and refuses to run on an unconfigured location.

A2's default store folder is `<engine root>/matter_spans`, which in a container is the container's own disk: papers stored there
are gone at the next recycle. So with A2 on, a store location must be CONFIGURED: the environment variable
`PRAVRUDHI_MATTER_SPANS_DIR` (a mounted volume), an object store named in the environment (`matter_objectstore`), or the yaml key
`matter_spans_dir`. With none of them every route that reads or writes the store answers 503 `store_unavailable` with a plain
reason and writes nothing.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

from pravrudhi.application.matter_spans import MatterError, MatterStore
from pravrudhi.application.matter_spans_config import DIR_ENV, MatterSpansConfig, _body, store_dir

UNCONFIGURED = "Stored papers are not available on this service yet: no place to keep them has been set up."


def location_configured(root: Path, environ: dict[str, str] | None = None) -> bool:
    """True when the environment names a spans folder or an object store, or the config names a folder. Pure; creates nothing."""
    env = os.environ if environ is None else environ
    if env.get(DIR_ENV, "").strip():
        return True
    if env.get("PRAVRUDHI_MATTER_OBJECT_ENDPOINT", "").strip() and env.get("PRAVRUDHI_MATTER_OBJECT_BUCKET", "").strip():
        return True
    body = _body(root)
    raw = body.get("matter_spans_dir") if isinstance(body, dict) else None
    return isinstance(raw, str) and bool(raw.strip())


class UnconfiguredStore:
    """Every operation is a 503 and nothing is read, written or created."""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)

        def refuse(*_a: Any, **_k: Any) -> Any:
            raise MatterError("store_unavailable", UNCONFIGURED, 503)

        return refuse


_CACHE: dict[tuple[Any, ...], Any] = {}
_CACHE_LOCK = threading.Lock()
_ENV_KEYS = (DIR_ENV, "PRAVRUDHI_MATTER_OBJECT_ENDPOINT", "PRAVRUDHI_MATTER_OBJECT_BUCKET", "PRAVRUDHI_MATTER_OBJECT_PREFIX",
             "PRAVRUDHI_MATTER_OBJECT_REGION")  # fmt: skip


def open_store(
    root: Path, cfg: MatterSpansConfig, environ: dict[str, str] | None = None, delete_only: bool = False
) -> Any:
    """ONE store per process for one (root, caps, location, mode): built on first use and reused, so an object-backed store keeps
    its once-per-process conditional-write probe and its per-matter thread locks across requests instead of rebuilding them on
    every read. The key holds the location settings (never a credential), so a changed location gives a fresh store."""
    env = os.environ if environ is None else environ
    folder_there = store_dir(root).is_dir() if delete_only else None
    key = (str(root), cfg, delete_only, folder_there, *(env.get(k, "") for k in _ENV_KEYS))
    with _CACHE_LOCK:
        got = _CACHE.get(key)
        if got is None:
            got = _CACHE[key] = _build(root, cfg, environ, delete_only)
        return got


def clear_cache() -> None:
    """For tests and for a deliberate re-read of the environment."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _build(
    root: Path, cfg: MatterSpansConfig, environ: dict[str, str] | None = None, delete_only: bool = False
) -> Any:
    """The matter store for this process: object-backed when an object store is named, the folder store when a folder is
    configured, else the refusing store. The folder is `store_dir(root)`. `delete_only` (A2 off: only the delete route exists)
    also opens an EXISTING default folder, so data stored there earlier stays deletable; it never creates one."""
    if not location_configured(root, environ) and not (delete_only and store_dir(root).is_dir()):
        return UnconfiguredStore()
    from pravrudhi.application import matter_objectstore as obj

    settings = obj.object_store_settings(environ)
    if settings is not None:
        return obj.make_store(store_dir(root), cfg, settings, environ)
    return MatterStore(store_dir(root), cfg)
