"""Pravrudhi engine: targets, model backends, proposer, night orchestrator, CLI, API."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

from pravrudhi_kernel import __version__ as KERNEL_VERSION

try:
    __version__ = _installed_version("pravrudhi")
except PackageNotFoundError:  # pragma: no cover - only when running from an uninstalled source tree
    # Never raise on import: `application/updates.py`, `application/demo_export.py`, `api/server.py` and the CLI
    # all read this at import time, so an exception here would take the update channel, the published snapshot,
    # the API and `--version` down together.
    __version__ = "0+unknown"
"""Read from installed package metadata rather than typed in here.

This was the literal "0.4.0" while `pyproject.toml` said 0.4.2, tag v0.4.2 was cut, and the product install was
running `releases/0.4.2`. Four surfaces repeat whatever this says, so the live site published
`engine: "0.4.0"`, `pravrudhi --version` agreed with it, and the update channel's idea of what was installed
was two patch releases stale. A hand-maintained constant beside a packaged version is two sources of truth;
metadata is generated from `pyproject.toml` at build time and cannot drift from it."""

__all__ = ["KERNEL_VERSION", "__version__"]
