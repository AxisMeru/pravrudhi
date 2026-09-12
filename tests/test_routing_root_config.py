"""`configs/routing.yaml` is the table this engine's own admin core loads its routing from; the packaged copy
under `src/pravrudhi/assets/configs/routing.yaml` is what `load_table()` falls back to with no path given. The
Alibaba Lite Plan seats (`qwen-lite-flash`, `qwen-lite-max` on `opencode:alibaba-plan`) were verified live and
admitted into the packaged table on 2026-09-07, but `configs/routing.yaml` was never brought up to date, so the
engine's own admin routing could not reach a plan that is in and tested.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application import routing

ROOT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "routing.yaml"


def test_root_config_declares_the_lite_plan_seats() -> None:
    table = routing.load_table(ROOT_CONFIG)
    for route_id in ("qwen-lite-flash", "qwen-lite-max"):
        assert route_id in table.routes, route_id
        assert table.routes[route_id].agent == "opencode:alibaba-plan"


def test_root_config_permits_the_same_routes_as_the_packaged_table() -> None:
    """Same seats, same tiers: the admin core and the packaged default must not silently diverge."""
    root_table = routing.load_table(ROOT_CONFIG)
    packaged_table = routing.load_table()
    for tier in ("mechanical", "standard", "design", "critical"):
        root_ids = {r.id for r in root_table.permitted(tier)}
        packaged_ids = {r.id for r in packaged_table.permitted(tier)}
        assert root_ids == packaged_ids, tier
