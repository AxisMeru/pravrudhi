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

ADR-0057: the ancestry-fold and selection-pressure bounds below were tuned to STUDIO's own historical
ledger (189 proposals, exactly two distinct parents) -- a second loop's ledger is not wrong for reading
differently, it is simply a different ledger. The first cross-loop beat exposed why a single global bound
cannot work: `distinct_parents=0` (a young ledger with no lineage yet) and `distinct_parents=5` (a ledger
that has genuinely outgrown the fold) are opposite conditions, and `!= 2` fires identically on both. So
these two checks now read a root's own declared `MaturityBaseline` (`.pravrudhi/config.yaml`'s `maturity:`
block, `build_config.load_maturity_baseline`) instead of a hardcoded "2" -- undeclared means "not yet
assessed", not "assumed to be Studio's number". The tie-break readiness check is NOT part of this: its
`TIEBREAK_MIN_COUNT` is a statistical threshold (how many observations a Wilson interval needs to separate
itself from a baseline rate), not an empirical constant read off one loop's history, so it stays global.
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application.archive import ancestry_report, parent_map, selection_pressure
from pravrudhi.application.build_config import load_maturity_baseline
from pravrudhi.application.procedure_graph import build_graph, tiebreak_readiness

#: ADR-0057: below this many ledger nodes, a root's ancestry is too sparse for a baseline to mean anything --
#: no fold/pressure signal, and no forgotten-baseline nudge either, the same "too early to judge" silence an
#: undeclared baseline already gets. Not derived from a fitted curve; a deliberately low bar chosen so a
#: genuinely matured, un-baselined root is not missed for long, not a claim that this exact count is special.
_MIN_NODES_FOR_BASELINE_NUDGE = 10


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
        baseline = load_maturity_baseline(root)
        if baseline.declared:
            if report.distinct_parents != baseline.expected_parents or report.max_depth > baseline.max_depth:
                signals.append(
                    "ADR-0057: the real ledger no longer matches this root's declared maturity baseline "
                    f"(distinct_parents={report.distinct_parents} vs expected {baseline.expected_parents}, "
                    f"max_depth={report.max_depth} vs {baseline.max_depth}) -- the ancestry-fold assumption "
                    "is due for a revisit"
                )
            recent = [p for p in selection_pressure(ledger) if p.night >= 7]
            binding = sum(
                1 for p in recent if p.binding and p.night not in baseline.known_inflated_nights
            )
            if binding > baseline.max_binding_beyond_known:
                signals.append(
                    f"ADR-0057: selection has started binding regularly ({binding} nights beyond this "
                    "root's known-inflated ones) -- the measurement and the plan built on it are due for "
                    "a revisit"
                )
        elif report.nodes >= _MIN_NODES_FOR_BASELINE_NUDGE:
            signals.append(
                f"ADR-0057: this root's real ledger has matured enough ({report.nodes} nodes) to warrant a "
                "maturity baseline (.pravrudhi/config.yaml's maturity: block); none is declared, so no "
                "fold/pressure signal can be read against it -- a real fold-outgrowth would otherwise go "
                "unnoticed simply because nobody set the baseline"
            )
    readiness = tiebreak_readiness(build_graph(Path(root)))
    if readiness.get("ready"):
        signals.append(f"ADR-0056/M6.4: the routing log now supports a tie-breaker: {readiness.get('why')}")
    return tuple(signals)


__all__ = ["maturity_signals"]
