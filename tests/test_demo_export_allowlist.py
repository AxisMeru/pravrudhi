"""#563 (Lead-2, 6 Oct 2026): the public demo file is an ALLOWLIST of product-edition sections, and the team-vocabulary
markers are wider. `build_demo` still assembles everything (Studio's own tooling reads that shape); `write_demo`
publishes only `public_view`, and the pipeline refuses a snapshot that still carries a seat, track, Colab or
operator-directive row, or a LAN address."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pravrudhi.application.demo_export import (
    INTERNAL_TEXT_MARKER,
    PUBLIC_DEMO_SECTIONS,
    build_demo,
    demo_pipeline,
    drop_internal_text,
    private_markers_left,
    public_view,
    redact_for_demo,
)
from pravrudhi.application.init import init_project

STUDIO_SECTIONS = (
    "candidates", "observations", "swarm", "heartbeat", "requests", "fleet", "health", "update", "inbox", "appetite",
    "parity", "diffs", "search", "agent_trace", "product", "capabilities",
)


@pytest.fixture
def bundle(tmp_path: Path) -> dict:
    init_project(tmp_path)
    return build_demo(tmp_path)


def test_the_public_view_carries_only_the_listed_product_sections(bundle: dict) -> None:
    out = public_view(bundle)
    assert set(out) == PUBLIC_DEMO_SECTIONS & set(bundle)
    for section in STUDIO_SECTIONS:
        assert section not in out, f"{section} is Pravrudhi improving itself and must not be published"


def test_every_listed_section_is_really_in_the_bundle(bundle: dict) -> None:
    """A stale entry would let a renamed section slip back in under its old name without anyone noticing."""
    assert set(bundle) >= PUBLIC_DEMO_SECTIONS, sorted(PUBLIC_DEMO_SECTIONS - set(bundle))


def test_a_section_added_to_the_bundle_later_stays_private_until_it_is_listed(bundle: dict) -> None:
    bundle = {**bundle, "brand_new_internal_section": {"x": 1}}
    assert "brand_new_internal_section" not in public_view(bundle)


def test_the_studio_snapshot_still_builds_in_full(bundle: dict) -> None:
    """`build_demo` is unchanged: only what is published is reduced."""
    assert set(STUDIO_SECTIONS) <= set(bundle)


def test_the_published_text_parses_and_has_no_studio_section(bundle: dict) -> None:
    text = demo_pipeline(json.dumps(public_view(bundle), indent=2, sort_keys=True, default=str) + "\n")
    assert set(json.loads(text)) <= PUBLIC_DEMO_SECTIONS
    assert private_markers_left(text) == []


@pytest.mark.parametrize("value", [
    "Seat 1 reviewed this", "seat3 account", "run on seat-0", "Track A owns it", "Track-B finding", "Track C's draft",
    "tracka said", "the track-a lane", "TRACK B", "Track_c", "TrackA", "trackb finding",
    "the colab login", "Colab seat", "operator directive: stop", "the operator's decision", "Operator instruction 5",
])
def test_a_seat_track_colab_or_operator_directive_value_is_dropped_whole(value: str) -> None:
    out = json.loads(drop_internal_text(json.dumps({"k": f"before {value} after", "other": "plain text"})))
    assert out["k"] == INTERNAL_TEXT_MARKER and out["other"] == "plain text"


@pytest.mark.parametrize("value", [
    "to track a candidate over nights", "an elaborate setup", "seating plan", "collaborate on it", "the operator console",
    "a track record", "tracked", "Track record of the benchmark", "a trackbar widget", "trackage rights", "to track a run",
    "track b-tree growth",
])
def test_ordinary_prose_is_not_swept_up(value: str) -> None:
    out = json.loads(drop_internal_text(json.dumps({"k": value})))
    assert out["k"] == value


@pytest.mark.parametrize("text", ["Seat 2", "Track A", "colab", "operator directive"])
def test_a_snapshot_that_still_carries_one_is_flagged_for_refusal(text: str) -> None:
    assert private_markers_left(json.dumps({"k": text}))


def test_a_lan_address_never_survives_the_pipeline() -> None:
    raw = json.dumps({"k": "dev 32B at http://192.168.0.152:8132/v1", "j": "ssh 192.168.1.20"})
    assert "192.168." in raw and private_markers_left(raw)
    out = redact_for_demo(raw)
    assert "192.168." not in out and "<redacted:lan-ip>" in out
