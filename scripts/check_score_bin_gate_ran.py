"""CI guard (Lead-2, 2026-09-26): CI never sets `PRABHASA_NYAYA_SCORE_BIN` (grepped every workflow under
`.github/workflows/`, zero hits), so every test marked `requires_score_bin` (a real, built prabhasa-nyaya
Lean `score` binary) has been silently skipping in every CI run -- the registry/pin tests these gate have
executed ZERO times in CI, for as long as CI has existed.

A full fix (build the real binary in CI) needs a new credential, pending the operator's approval; until that
exists, this makes the gap a real, visible CI failure instead of a silent 100% skip rate nobody is watching
for. Runs pytest scoped to `-m requires_score_bin` and fails if not one of the collected tests actually
executed (passed or failed) -- printing every skip reason so the failure is diagnosable, not a bare red X.

This is EXPECTED to fail on any host without `PRABHASA_NYAYA_SCORE_BIN` set to a real binary -- that is the
intended, informative signal (every PR touching the registry or pins must carry a local run against the
real binary, pasted into the PR description, until the real fix lands), not a bug in this script.
"""

from __future__ import annotations

import sys

import pytest


class _OutcomeCollector:
    """A pytest plugin, not a subprocess-output parser: reads real per-test outcomes off pytest's own hook,
    so this never has to guess at pytest's text-summary formatting.

    `collected` is deliberately NOT read off `pytest_collection_modifyitems` -- that hook fires with the
    FULL, unfiltered item list, before pytest's own built-in `-m` keyword-marker deselection has run (hook
    ordering between plugins is not something this script controls), so it would report every test in the
    suite, not just the ones this run actually selected. Every test that survives `-m` filtering reaches
    `pytest_runtest_logreport` instead (as executed, or as skipped), so summing those two is the correct
    "how many requires_score_bin tests did this run even look at" count."""

    def __init__(self) -> None:
        self.executed_passed = 0
        self.executed_failed = 0
        self.skipped: list[tuple[str, str]] = []

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.outcome == "skipped":
            self.skipped.append((report.nodeid, str(report.longrepr)))
        elif report.when == "call":
            if report.outcome == "passed":
                self.executed_passed += 1
            elif report.outcome == "failed":
                self.executed_failed += 1

    @property
    def collected(self) -> int:
        return self.executed_passed + self.executed_failed + len(self.skipped)


def main() -> int:
    collector = _OutcomeCollector()
    pytest.main(["-m", "requires_score_bin", "-q", "--no-header"], plugins=[collector])
    executed = collector.executed_passed + collector.executed_failed

    print(
        f"requires_score_bin gate: {collector.collected} collected, {executed} executed "
        f"({collector.executed_passed} passed, {collector.executed_failed} failed), "
        f"{len(collector.skipped)} skipped"
    )

    if collector.collected == 0:
        print(
            "ERROR: 0 tests collected under -m requires_score_bin -- the marker itself may be broken "
            "(a rename, a missing @pytest.mark.requires_score_bin on a test that should carry it), not a "
            "PRABHASA_NYAYA_SCORE_BIN problem. Fix the marker, don't just re-run this."
        )
        return 1

    if executed == 0:
        print(
            "FAIL: 0 of the requires_score_bin-gated tests actually executed -- every one of them skipped. "
            "This means PRABHASA_NYAYA_SCORE_BIN is not set to a real, built prabhasa-nyaya Lean `score` "
            "binary in this environment, so every registry/pin test it gates ran ZERO times here.\n"
            "Skip reasons:"
        )
        for nodeid, reason in collector.skipped:
            print(f"  {nodeid}: {reason}")
        print(
            "\nUntil the real binary is built in CI (option a, pending a new credential's approval), this "
            "check staying red is the EXPECTED state -- see docs/decisions (Gate 1 / pin-regression PRs) for "
            "the interim rule: a local run against the real binary, with the pass line pasted into the PR "
            "description, is required for any PR touching the registry or pins."
        )
        return 1

    print("OK: at least one requires_score_bin test actually ran.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
