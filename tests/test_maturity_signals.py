import json
from pathlib import Path

from pravrudhi.application.maturity_signals import maturity_signals


def _propose(cid: str, lineage: list[str]) -> str:
    return json.dumps({"kind": "propose", "candidate_id": cid, "payload": {"lineage": lineage}})


def test_a_fresh_root_with_no_real_ledger_or_routing_log_raises_no_signal(tmp_path: Path) -> None:
    assert maturity_signals(tmp_path) == ()


def test_an_immature_ledger_with_two_parents_raises_no_fold_signal(tmp_path: Path) -> None:
    ledger = tmp_path / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "\n".join([
            _propose("c-root-1", []),
            _propose("c-root-2", []),
            _propose("c-child-1", ["c-root-1"]),
            _propose("c-child-2", ["c-root-2"]),
        ]) + "\n"
    )

    assert maturity_signals(tmp_path) == ()


def test_a_ledger_that_no_longer_folds_to_two_parents_raises_the_signal(tmp_path: Path) -> None:
    """The exact shape the real ledger tripped on: a third distinct root appears. Mirrors
    `tests/test_archive.py::TestAgainstTheRealLedger::test_the_committed_ledger_folds_to_two_parents` -
    same underlying `ancestry_report`/`parent_map` call, reported instead of asserted."""
    ledger = tmp_path / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        "\n".join([
            _propose("c-root-1", []),
            _propose("c-root-2", []),
            _propose("c-root-3", []),
            _propose("c-child-1", ["c-root-1"]),
            _propose("c-child-2", ["c-root-2"]),
            _propose("c-child-3", ["c-root-3"]),
        ]) + "\n"
    )

    signals = maturity_signals(tmp_path)

    assert any("no longer folds to two parents" in s for s in signals)
    assert any("distinct_parents=3" in s for s in signals)


def test_an_empty_ledger_file_is_treated_as_no_ledger_not_a_fold_defect(tmp_path: Path) -> None:
    """2026-09-11's trap, mirrored: a zero-byte research/ledger.jsonl must never be read as a search with
    no parents (which would otherwise report distinct_parents=0 and raise a false signal)."""
    ledger = tmp_path / "research" / "ledger.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("")

    assert maturity_signals(tmp_path) == ()


def test_a_routing_log_with_enough_repeated_edges_raises_the_tiebreak_signal(tmp_path: Path) -> None:
    """Mirrors `tests/test_procedure_graph.py::test_the_live_routing_log_is_not_yet_ready_to_tie_break` -
    same `build_graph`/`tiebreak_readiness` call, reported instead of asserted. `TIEBREAK_MIN_COUNT` (5)
    observations of the same (from, to, reason) edge is what tips `ready` to True."""
    routing = tmp_path / ".pravrudhi" / "routing.jsonl"
    routing.parent.mkdir(parents=True)
    rows = []
    for i in range(6):
        task = f"t-{i}"
        rows.append({"tier": "s1", "route_id": "a", "task_id": task, "accepted": False,
                      "wall_s": 1.0, "at": f"2026-09-15T00:00:{i:02d}Z", "limited": True})
        rows.append({"tier": "s1", "route_id": "b", "task_id": task, "accepted": True,
                      "wall_s": 1.0, "at": f"2026-09-15T00:00:{i+10:02d}Z"})
    routing.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    signals = maturity_signals(tmp_path)

    assert any("routing log now supports a tie-breaker" in s for s in signals)


def test_a_thin_routing_log_raises_no_tiebreak_signal(tmp_path: Path) -> None:
    routing = tmp_path / ".pravrudhi" / "routing.jsonl"
    routing.parent.mkdir(parents=True)
    rows = [
        {"tier": "s1", "route_id": "a", "task_id": "t-0", "accepted": False, "wall_s": 1.0,
         "at": "2026-09-15T00:00:00Z", "limited": True},
        {"tier": "s1", "route_id": "b", "task_id": "t-0", "accepted": True, "wall_s": 1.0,
         "at": "2026-09-15T00:00:01Z"},
    ]
    routing.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    assert maturity_signals(tmp_path) == ()


def test_build_validate_deselects_exactly_the_three_tripwire_tests() -> None:
    """ADR-0056: these three, and no others, are off the integration gate. Any drift here (a test renamed,
    a fourth tripwire added without updating BUILD_VALIDATE) should fail loudly rather than silently either
    re-gating a design signal or leaving a stale --deselect pointing at nothing."""
    from pravrudhi.application.integrate import _MATURITY_SIGNAL_TESTS, BUILD_VALIDATE

    assert len(_MATURITY_SIGNAL_TESTS) == 3
    for node_id in _MATURITY_SIGNAL_TESTS:
        assert f"--deselect {node_id}" in BUILD_VALIDATE


def test_the_deselected_node_ids_still_resolve_to_real_tests() -> None:
    """Catches the failure mode a plain string constant cannot protect against on its own: one of these three
    tests renamed or removed, leaving BUILD_VALIDATE deselecting a node id that no longer exists (a silent
    no-op, not the intended skip) while the test itself quietly rejoins the integration gate."""
    import subprocess
    import sys

    from pravrudhi.application.integrate import _MATURITY_SIGNAL_TESTS

    for node_id in _MATURITY_SIGNAL_TESTS:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", node_id],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"{node_id} no longer collects: {result.stdout}\n{result.stderr}"
