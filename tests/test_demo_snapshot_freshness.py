"""The public site must not show a visitor the snapshot they saw last time.

The recorded snapshot is rewritten on every publish and its URL never changes. It was fetched with
`cache: "force-cache"`, which tells the browser to use any cached copy it still considers fresh without asking
the server. The effect was not a slightly stale page: a returning visitor kept whatever bundle they first loaded
and saw none of the pages added since.

It was caught on the deployed site rather than in a build. The network was serving a twenty-three section bundle
while the browser handed the app an eleven-section one, so a page reading a section added later rendered its
empty state and looked like a page with no data behind it. Every recorded-data page had the same defect; only the
newest one made it visible.

`no-cache` is the correct mode for a mutable file at a stable URL. It still uses the cached bytes when they are
current, because it revalidates rather than re-downloads, so an unchanged snapshot costs a 304 rather than a
fresh transfer. What it cannot do is serve a copy the server has replaced.
"""

from __future__ import annotations

import re
from pathlib import Path

DEMO_TS = Path("app/frontend/src/lib/demo.ts")


def _source() -> str:
    return DEMO_TS.read_text() if DEMO_TS.exists() else ""


def test_the_snapshot_is_never_fetched_from_a_cache_that_can_be_stale() -> None:
    src = _source()
    if not src:
        return  # the interface is not present in every checkout
    fetches = re.findall(r"fetch\(\s*`?\$?\{?[^)]*demo\.json[^)]*\)", src, re.DOTALL)
    assert fetches, "demo.ts should still fetch the snapshot; if it moved, move this guard with it"
    for call in fetches:
        assert "force-cache" not in call, (
            "force-cache lets a returning visitor keep the snapshot they first loaded, which hides every "
            "publish from them"
        )
        assert "only-if-cached" not in call


def test_the_snapshot_fetch_states_its_cache_mode() -> None:
    """Leaving the mode off inherits the browser default, which is also allowed to serve a stale copy."""
    src = _source()
    if not src:
        return
    assert 'cache: "no-cache"' in src or 'cache: "no-store"' in src, (
        "the snapshot fetch must name a revalidating cache mode rather than relying on the default"
    )
