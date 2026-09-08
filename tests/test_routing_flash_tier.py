"""The flagship should not be doing mechanical work.

The Alibaba Lite Plan's one-week quota was exhausted inside a day. The cause was not a single expensive call: it
was `qwen3.8-max`, the vendor's flagship, being the declared first choice at BOTH the mechanical and standard
tiers, driving agent loops whose steps cost about 20,000 tokens each — one measured step of a trivial task showed
20,419 total with 19,584 read from cache — dispatched hourly by the heartbeat.

`qwen3.8-flash` is the same plan's cheap high-throughput model. Mechanical work is exactly what it is for. The
two share one quota, so this does not buy capacity; it lowers the burn rate for the work that never needed the
flagship, which is most of it.
"""

from __future__ import annotations

from pravrudhi.application import routing


def test_the_flagship_is_not_the_first_choice_for_mechanical_work() -> None:
    table = routing.load_table()
    mechanical = [r.id for r in table.permitted("mechanical")]
    assert "qwen-lite-flash" in mechanical, mechanical
    flash = table.routes["qwen-lite-flash"]
    flagship = table.routes["qwen-lite-max"]
    assert flash.relative_cost < flagship.relative_cost
    assert "mechanical" not in flagship.tiers, (
        "the flagship keeps the standard tier and gives up mechanical; a model that costs more per token should "
        "not be the default for the tier that runs most often"
    )


def test_the_flash_route_serves_only_the_tier_it_has_evidence_for() -> None:
    """Admitted where the plan's other model was admitted, and no wider. A cheaper model is not a better one."""
    flash = routing.load_table().routes["qwen-lite-flash"]
    assert flash.tiers == ("mechanical",), flash.tiers
    assert flash.agent == "opencode:alibaba-plan"
