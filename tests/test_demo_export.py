import json
import re
from pathlib import Path

from pravrudhi.application.demo_export import PAGES, build_demo
from pravrudhi.application.init import init_project
from pravrudhi.application.policies import POLICIES
from pravrudhi_kernel.schema import LedgerEvent
from pravrudhi_kernel.schema.common import Pramana


def _observe_event(seq: int, t: str, candidate_id: str, delta_in: float) -> str:
    ev = LedgerEvent(
        seq=seq, t=t, epoch=0, night=0, cycle=None, kind="observe",  # type: ignore[arg-type]
        actor="kernel", candidate_id=candidate_id, surface=None, bucket=None, provenance=Pramana.pratyaksha,
        kernel_release="0.1.0", payload={"delta_in": delta_in}, prev_hash="0" * 64, this_hash="0" * 64,
    )
    return ev.model_dump_json()


def _write_ledger(root: Path, lines: list[str]) -> None:
    ledger = root / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

SECRET_PATTERN = re.compile(
    r"sk-[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|(?:(?i:api[_-]?key|secret|token|password))[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{8,}"
)


def _leaf_strings(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _leaf_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _leaf_strings(v)
    elif isinstance(obj, str):
        yield obj


def _demo(tmp_path: Path) -> dict:
    init_project(tmp_path)
    return build_demo(tmp_path)


def test_version_block_has_the_four_fields(tmp_path: Path) -> None:
    demo = _demo(tmp_path)
    version = demo["version"]
    assert set(version) == {"engine", "kernel", "commit", "exported_at"}
    assert version["engine"] and version["kernel"]
    assert version["commit"] is None or re.fullmatch(r"[0-9a-f]{4,40}", version["commit"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", version["exported_at"])


def test_capabilities_block_has_the_five_fields(tmp_path: Path) -> None:
    demo = _demo(tmp_path)
    caps = demo["capabilities"]
    assert set(caps) == {"tools", "agents", "policies", "recipes", "pages"}

    assert caps["tools"] and all(set(t) == {"id", "kind", "available"} for t in caps["tools"])
    assert all(isinstance(t["available"], bool) for t in caps["tools"])

    assert caps["agents"] and all(set(a) == {"name", "available"} for a in caps["agents"])
    assert all(isinstance(a["available"], bool) for a in caps["agents"])

    assert caps["policies"] == list(POLICIES)
    assert isinstance(caps["recipes"], int) and caps["recipes"] >= 0
    assert caps["pages"] == list(PAGES) and all(p.startswith("/") for p in caps["pages"])


def test_existing_keys_are_unchanged(tmp_path: Path) -> None:
    demo = _demo(tmp_path)
    assert demo["recorded"] is True
    assert set(demo["engine"]) == {"version", "candidates"}
    assert demo["engine"]["version"] == demo["version"]["engine"]
    for key in (
        "status", "models", "external", "nights", "runs", "objectives", "recipes", "plans", "featured_run", "swarm",
    ):
        assert key in demo


def test_swarm_block_has_the_five_fields(tmp_path: Path) -> None:
    demo = _demo(tmp_path)
    swarm = demo["swarm"]
    assert set(swarm) == {"agents", "roster", "routing", "subagent_runs", "selfbuild_runs"}

    # `roster` carries what only the CLI could answer: relative cost, the measured record, and whether a route is
    # sitting out a vendor usage limit with the time it returns. Without it the published site could show where
    # work is routed but not that the cheapest seat was unavailable and a dearer one was absorbing its work.
    assert isinstance(swarm["roster"], list)
    for seat in swarm["roster"]:
        assert {"id", "agent", "relative_cost", "usable", "returns_at"} <= set(seat)
        assert isinstance(seat["usable"], bool)
        assert seat["returns_at"] is None or isinstance(seat["returns_at"], str)

    assert swarm["agents"] and all(set(a) == {"name", "available", "reason"} for a in swarm["agents"])
    assert all(isinstance(a["available"], bool) for a in swarm["agents"])

    assert isinstance(swarm["routing"], list)

    assert isinstance(swarm["subagent_runs"], list) and len(swarm["subagent_runs"]) <= 20
    assert isinstance(swarm["selfbuild_runs"], list) and len(swarm["selfbuild_runs"]) <= 20
    for run in (*swarm["subagent_runs"], *swarm["selfbuild_runs"]):
        assert isinstance(run, dict)
        assert isinstance(run["accepted"], bool)


def test_swarm_run_records_carry_no_absolute_path(tmp_path: Path) -> None:
    demo = _demo(tmp_path)
    swarm = demo["swarm"]
    abs_path = re.compile(r"/(?:[\w.\-]+/)+[\w.\-]+")
    for run in (*swarm["subagent_runs"], *swarm["selfbuild_runs"]):
        for value in _leaf_strings(run):
            assert not abs_path.search(value), value
            assert str(tmp_path) not in value


def test_no_field_carries_a_token_secret_or_key_pattern(tmp_path: Path) -> None:
    demo = _demo(tmp_path)
    for value in _leaf_strings(demo):
        assert not SECRET_PATTERN.search(value), value
    assert not SECRET_PATTERN.search(json.dumps(demo))


def test_snapshot_carries_the_heartbeat(tmp_path):
    assert _demo(tmp_path)["heartbeat"] == []


class TestObservationsComposeAcrossLoopRoots:
    """ADR-0055: a re-root under ADR-0053 splits one project's ledger across two roots that must never be
    merged on disk. `build_demo`'s `extra_ledger_roots` composes their `observe` events read-only, at export
    time only, tagged with which root each came from and ordered by timestamp rather than by `seq` -- `seq`
    only orders events within one chain, and treating the two chains as one continuous sequence would
    fabricate provenance (CHARTER §6)."""

    def test_composes_both_roots_tagged_and_ordered_by_timestamp_not_seq(self, tmp_path: Path) -> None:
        old_root, new_root = tmp_path / "old", tmp_path / "new"
        init_project(old_root)
        init_project(new_root)
        # The new root's single event has a LOWER seq (it started its own chain from 0) but a LATER timestamp
        # than the old root's - exactly the re-root shape. If composition ordered by seq, the new root's event
        # would sort first; ordering by timestamp is what this test actually proves.
        _write_ledger(old_root, [_observe_event(3679, "2026-09-12T11:00:00.000Z", "c-0001", 0.01)])
        _write_ledger(new_root, [_observe_event(0, "2026-09-12T20:44:00.000Z", "c-0002", 0.02)])

        demo = build_demo(old_root, extra_ledger_roots=[new_root])

        rows = demo["observations"]
        assert [r["candidate_id"] for r in rows] == ["c-0001", "c-0002"], "must be timestamp order, not seq order"
        assert rows[0]["seq"] == 3679, "the old root's own seq must survive unchanged, never renumbered"
        assert rows[1]["seq"] == 0, "the new root's own seq must survive unchanged, never renumbered"
        assert rows[0]["source_root"] == str(old_root)
        assert rows[1]["source_root"] == str(new_root)

    def test_never_writes_to_either_ledger(self, tmp_path: Path) -> None:
        old_root, new_root = tmp_path / "old", tmp_path / "new"
        init_project(old_root)
        init_project(new_root)
        _write_ledger(old_root, [_observe_event(3679, "2026-09-12T11:00:00.000Z", "c-0001", 0.01)])
        _write_ledger(new_root, [_observe_event(0, "2026-09-12T20:44:00.000Z", "c-0002", 0.02)])
        old_bytes = (old_root / "research" / "ledger.jsonl").read_bytes()
        new_bytes = (new_root / "research" / "ledger.jsonl").read_bytes()

        build_demo(old_root, extra_ledger_roots=[new_root])

        assert (old_root / "research" / "ledger.jsonl").read_bytes() == old_bytes
        assert (new_root / "research" / "ledger.jsonl").read_bytes() == new_bytes

    def test_a_missing_extra_root_ledger_is_skipped_not_an_error(self, tmp_path: Path) -> None:
        old_root, missing_root = tmp_path / "old", tmp_path / "never-run"
        init_project(old_root)
        missing_root.mkdir()
        _write_ledger(old_root, [_observe_event(0, "2026-09-12T11:00:00.000Z", "c-0001", 0.01)])

        demo = build_demo(old_root, extra_ledger_roots=[missing_root])

        assert [r["candidate_id"] for r in demo["observations"]] == ["c-0001"]

    def test_a_single_root_publish_is_unaffected(self, tmp_path: Path) -> None:
        """No extra_ledger_roots given (every call site before ADR-0055) must behave exactly as before."""
        root = tmp_path / "solo"
        init_project(root)
        _write_ledger(root, [_observe_event(0, "2026-09-12T11:00:00.000Z", "c-0001", 0.01)])

        demo = build_demo(root)

        assert demo["observations"] == [{
            "seq": 0, "night": 0, "candidate_id": "c-0001", "payload": {"delta_in": 0.01}, "source_root": str(root),
        }]
