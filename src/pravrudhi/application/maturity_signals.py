"""ADR-0056: three tests (`tests/test_archive.py`, `tests/test_procedure_graph.py`) are deliberate tripwires
against THIS MACHINE's own real, accumulated `research/ledger.jsonl` and `.pravrudhi/routing.jsonl` -- not
fixtures. They detect when a loop's real operational history crosses a design-revisit threshold: the
ancestry search no longer folds to two parents, selection pressure starts binding regularly, or the routing
log now carries enough per-edge observations to tie-break a route choice (card M6.4).

Until 2026-09-15 they ran inside `BUILD_VALIDATE`, so a long-running loop's own maturing ledger could block
an unrelated, correctly-built criterion from integrating -- a design-revisit signal was gating work that had
nothing to do with it. ADR-0056 moves them off that gate. This module is the other half: the SAME
computation the three tests use (`ancestry_report`, `parent_map`, `selection_pressure`, `build_graph`,
`tiebreak_readiness` -- nothing here is a re-derivation), reported through `heartbeat.beat`'s own record
instead of asserted through pytest, so the signal still surfaces without blocking anything.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application.archive import ancestry_report, parent_map, selection_pressure
from pravrudhi.application.procedure_graph import build_graph, tiebreak_readiness

#: Nights 19 and 20 bound for a reason unrelated to selection getting harder (a bench filter added
#: 2026-09-10 fixed retroactively-inflated pools on those two nights only) -- see
#: `tests/test_archive.py::TestSelectionPressure::test_the_real_ledger_shows_the_budget_rarely_bound`,
#: whose exact bound this mirrors, byte for byte, so the signal and the pinned test never drift apart.
_KNOWN_INFLATED_NIGHTS = {19, 20}


def _real_ledger(root: Path) -> Path | None:
    """Mirrors `tests/test_archive.py`'s own `_real_ledger`: an EMPTY file is none, not a search with no
    parents, so this never reports a fold defect from a worktree that simply has no local ledger yet."""
    path = Path(root) / "research" / "ledger.jsonl"
    if not path.exists() or path.stat().st_size == 0:
        return None
    return path


def maturity_signals(root: Path) -> tuple[str, ...]:
    """Non-blocking design-revisit signals for this root's real ledger/routing log, reusing the exact
    computation the three ADR-0056 tripwire tests assert on. Empty when nothing has matured -- the common
    case for a fresh worktree, a CI checkout, or a loop root still short of the thresholds."""
    signals: list[str] = []
    ledger = _real_ledger(root)
    if ledger is not None:
        report = ancestry_report(parent_map(ledger))
        if report.distinct_parents != 2 or report.max_depth > 2:
            signals.append(
                "ADR-0056: the real ledger no longer folds to two parents "
                f"(distinct_parents={report.distinct_parents}, max_depth={report.max_depth}) -- "
                "the ancestry-fold assumption is due for a revisit"
            )
        recent = [p for p in selection_pressure(ledger) if p.night >= 7]
        binding = sum(1 for p in recent if p.binding and p.night not in _KNOWN_INFLATED_NIGHTS)
        if binding > 2:
            signals.append(
                f"ADR-0056: selection has started binding regularly ({binding} nights beyond the two known "
                "inflated ones) -- the measurement and the plan built on it are due for a revisit"
            )
    readiness = tiebreak_readiness(build_graph(Path(root)))
    if readiness.get("ready"):
        signals.append(f"ADR-0056/M6.4: the routing log now supports a tie-breaker: {readiness.get('why')}")
    return tuple(signals)


__all__ = ["maturity_signals"]
