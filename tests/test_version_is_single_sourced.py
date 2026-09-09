"""The engine must not misstate its own version, because four surfaces repeat whatever it says.

On 2026-09-09 `pravrudhi.__version__` was the string "0.4.0" while `pyproject.toml` said 0.4.2, tag v0.4.2 was
cut, and the product install was running `releases/0.4.2`. The engine had been two patch releases stale about
itself for as long as nobody bumped the literal.

It is not cosmetic. That constant is read by `application/updates.py:57` (the update channel's notion of what
is currently installed), `application/demo_export.py:24` (so the published snapshot said `engine: "0.4.0"` on
the live site), `api/server.py` (the API title and the health endpoint) and `cli/app.py:68` (`--version`). A
hand-maintained literal beside a packaged version is two sources of truth, and this is what that costs.

So the literal is gone: the version is read from installed package metadata, which is generated from
`pyproject.toml` at build time and cannot drift from it.
"""

from __future__ import annotations

import re
import tomllib
from importlib.metadata import version as _installed_version
from pathlib import Path

import pravrudhi


def _declared() -> str:
    """The version in pyproject.toml, the one source that a wheel is actually built from."""
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as fh:
        return str(tomllib.load(fh)["project"]["version"])


def test_the_engine_reports_the_version_it_was_built_as() -> None:
    assert pravrudhi.__version__ == _installed_version("pravrudhi")


def test_the_reported_version_matches_pyproject() -> None:
    """The check that would have caught the drift. Only meaningful from a source checkout, which is where
    someone bumps one of the two and forgets the other."""
    assert pravrudhi.__version__ == _declared()


def test_no_version_number_is_written_into_the_source() -> None:
    """A typed-in number is how the two got out of step, so its absence is the property worth asserting.

    The unparseable fallback sentinel is deliberately allowed: what must never reappear is something that looks
    like a release, because that is what silently disagrees with `pyproject.toml`.
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "pravrudhi" / "__init__.py").read_text()
    numbered = re.findall(r'__version__\s*=\s*"(\d+\.\d+[^"]*)"', source)
    assert not numbered, f"version number typed into the source: {numbered}"


def test_a_version_is_always_reported_even_uninstalled() -> None:
    """Every surface that repeats this must get a string, not an exception: an import that raises because the
    distribution is missing would take the CLI, the API and the exporter down with it."""
    assert isinstance(pravrudhi.__version__, str) and pravrudhi.__version__
