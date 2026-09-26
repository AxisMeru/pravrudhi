"""Config C production switch-on smoke test (Track-C, 2026-09-25), for L2A.

Default mode -- three checks against a FULLY-CONFIGURED `NyayaAgent.house()` (real judges, real Lean binary
-- this script makes live calls when run, it is not itself a live traffic change; it is code that L2A runs
deliberately):

1. A PROOF-able item.
2. **negative_not_proved**: a known court-negative / should_refuse item must NOT come out as PROOF. The
   safety property this checks is "no false proof," not "DENIAL" specifically -- DENIAL, ABSTAIN, and
   REFER_TO_LAWYER all pass; only PROOF fails loudly. (An earlier draft tried to force a specific DENIAL
   outcome by hand-tuning facts -- Lead-2, 2026-09-25: that's fishing, and the smoke shouldn't depend on it.
   The item here was instead chosen MECHANICALLY: the lowest item_id among negatives whose served_C WS-B
   verdict is CORRECT_ABSTAIN in the sealed served_C verdicts, confirmed with one live call. See the
   fixture file's own provenance field.)
3. Second judge unavailable -> REFER_TO_LAWYER (`second_judge_unavailable`). Fully deterministic: this ONE
   check points NYAYA_SECOND_JUDGE_BASE_URL at an unreachable local port for THIS check only, with
   NYAYA_SECOND_JUDGE_MODEL pinned to a fixed string (never left to resolve via `/v1/models`, which would
   itself raise at `NyayaAgent.house()` construction time -- before `AndGateJudge`'s own try/except around
   the per-request call ever runs; found while validating this script against the real local judge, 2026-09-25).
   This check never depends on any judge's actual answer quality -- only on the AndGateJudge/NyayaAgent
   wiring merged in #10 (commit 078fea2, on main).
4. **red_team_leading_narrative** (Lead-2, 2026-09-26, standing regression from the narrative-sensitivity
   investigation): a sealed court-negative item whose missing required element has NO supporting fact, with
   a LEADING narrative that directly asserts that element (no new facts). Passes on anything but PROOF, same
   convention as check 2. Unlike every other fixture, this one carries its OWN `narrative` field rather than
   the shared placeholder -- the narrative itself is what's being tested.

`--rollback` mode -- R1's finding, 2026-09-25: the default mode's own guard REFUSES when
NYAYA_SECOND_JUDGE_BASE_URL is unset, and check 3 always overwrites the URL for its one check, so there was
no path that ever exercised "config C fully off" -- the second_judge-is-None branch was dead code. This mode
fixes that: it REQUIRES the second-judge env to be UNSET (refuses if set, the opposite of the default mode's
guard), asserts `load_agent_config(ROOT).second_judge is None` and that `NyayaAgent.house` built a plain
`HouseJudge` (not an `AndGateJudge`), then runs checks 1 and 2 against the primary alone -- confirming
rollback actually reverts to today's single-judge behavior, not just that the env got unset.

**Fixtures load from `PRAVRUDHI_CONFIGC_SMOKE_FIXTURES` (required, no default), never from this file.** The
negative_not_proved fixture's facts are drawn from the constructed 1519-element eval set -- committing them
here would leak eval-set text into the public pravrudhi repo. The fixture file itself lives in tmp_scratch,
outside this repo; see its own `_provenance` field for exactly where each fixture came from.

Run with the runbook's full environment already set (CONFIGC-PRODUCTION-SWITCHON-RUNBOOK-2026-09-25.md).
Exits non-zero and prints which check(s) failed if any expectation doesn't match.
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_agent import AgentConfig, NyayaAgent, load_agent_config  # noqa: E402
from pravrudhi.application.nyaya_judges import AndGateJudge  # noqa: E402

FIXTURES_ENV = "PRAVRUDHI_CONFIGC_SMOKE_FIXTURES"

#: A smoke run's audit trail goes to a throwaway tmp dir, not `configs/nyaya_agent.yaml`'s real
#: `audit_dir` -- `load_agent_config` has no env override for that key, so this script overrides the
#: loaded config's field directly (a frozen dataclass; `dataclasses.replace`, never a config file edit).
_SMOKE_AUDIT_DIR = Path(tempfile.mkdtemp(prefix="configc_smoke_audit_"))


def _load_fixtures() -> dict[str, Any]:
    path = os.environ.get(FIXTURES_ENV)
    if not path:
        print(f"REFUSING: {FIXTURES_ENV} is not set (no default -- fixtures never live in this repo)", file=sys.stderr)
        raise SystemExit(2)
    p = Path(path)
    if not p.exists():
        print(f"REFUSING: {FIXTURES_ENV}={path} does not exist", file=sys.stderr)
        raise SystemExit(2)
    data: dict[str, Any] = json.loads(p.read_text())
    for key in ("proof", "negative_not_proved", "red_team_leading_narrative"):
        if key not in data or "facts" not in data[key] or "contract_id" not in data[key]:
            print(f"REFUSING: fixture file is missing a valid '{key}' entry (facts + contract_id)", file=sys.stderr)
            raise SystemExit(2)
    if "narrative" not in data["red_team_leading_narrative"]:
        print("REFUSING: 'red_team_leading_narrative' fixture is missing its own 'narrative' field", file=sys.stderr)
        raise SystemExit(2)
    return data


def _config_from_env() -> AgentConfig:
    """`load_agent_config` already reads every NYAYA_SECOND_JUDGE_* / NYAYA_HOUSE_JUDGE_* override -- this
    smoke test never re-derives that logic, only calls it, per the runbook's own env table. `audit_dir` is
    overridden to a throwaway tmp dir (`_SMOKE_AUDIT_DIR`), never the production path from the yaml."""
    return dataclasses.replace(load_agent_config(ROOT), audit_dir=_SMOKE_AUDIT_DIR)


def _run(config: AgentConfig, facts: list[str], contract_id: str, narrative: str = "Smoke-test facts, not a real case.") -> str:
    agent = NyayaAgent.house(ROOT, config=config)
    run = agent.run(facts, narrative=narrative, contract_ids=[contract_id])
    return run.contracts[0].outcome


def check_proof(fixtures: dict[str, Any]) -> tuple[bool, str]:
    fx = fixtures["proof"]
    outcome = _run(_config_from_env(), fx["facts"], fx["contract_id"])
    ok = outcome == "PROOF"
    return ok, f"expected PROOF, got {outcome}" if not ok else "PROOF as expected"


def check_negative_not_proved(fixtures: dict[str, Any]) -> tuple[bool, str]:
    """Passes on DENIAL, ABSTAIN, or REFER_TO_LAWYER -- fails ONLY on PROOF. The safety property is "no
    false proof," not any specific non-PROOF outcome."""
    fx = fixtures["negative_not_proved"]
    outcome = _run(_config_from_env(), fx["facts"], fx["contract_id"])
    ok = outcome != "PROOF"
    return ok, "FALSE PROOF: got PROOF on a known court-negative item" if not ok else f"not proved (outcome={outcome})"


def check_red_team_leading_narrative(fixtures: dict[str, Any]) -> tuple[bool, str]:
    """Standing red-team case (Lead-2, 2026-09-26, from the narrative-sensitivity investigation): a sealed
    court-negative item whose missing required element is asserted directly by a LEADING narrative (no new
    facts added), to check the AND-gate does not let free text substitute for a quotable fact. Passes on
    DENIAL, ABSTAIN, or REFER_TO_LAWYER -- fails ONLY on PROOF, same convention as `negative_not_proved`.
    Uses the fixture's OWN `narrative` field (not the shared placeholder) -- this is the one fixture where
    the narrative itself is the point of the test."""
    fx = fixtures["red_team_leading_narrative"]
    outcome = _run(_config_from_env(), fx["facts"], fx["contract_id"], narrative=fx["narrative"])
    ok = outcome != "PROOF"
    if ok:
        return True, f"not proved (outcome={outcome})"
    return False, "FALSE PROOF: a leading narrative asserting the missing element produced PROOF"


def check_second_judge_unavailable_refers(fixtures: dict[str, Any]) -> tuple[bool, str]:
    """Deterministic: NYAYA_SECOND_JUDGE_BASE_URL is overridden to an unreachable local port, and
    NYAYA_SECOND_JUDGE_MODEL is pinned to a fixed string, for THIS check only -- both together, regardless
    of whatever real endpoint the rest of this script's environment points at. Pinning the model is required:
    leaving it unset (None) makes `HouseJudge.__init__` try to resolve it via a live `/v1/models` call at
    AGENT-CONSTRUCTION time, which raises before `AndGateJudge`'s own try/except around the per-REQUEST call
    ever gets a chance to convert the failure into a fail-closed REFER (found while validating this script
    against the real local judge, 2026-09-25 -- an earlier version of this check crashed with an unhandled
    URLError instead of exercising the REFER path at all). Every other second_judge env var (tau, timeout,
    delta) is left as configured. Reuses the `proof` fixture's facts/contract -- any fixture works here,
    since the point is the second judge never answering, not what the primary alone would decide.

    Rollback (config C fully off) is NOT this check with the URL restored -- see `--rollback` mode, which
    exercises that path directly rather than as an implicit branch here."""
    saved_url = os.environ.get("NYAYA_SECOND_JUDGE_BASE_URL")
    saved_model = os.environ.get("NYAYA_SECOND_JUDGE_MODEL")
    os.environ["NYAYA_SECOND_JUDGE_BASE_URL"] = "http://127.0.0.1:1/v1"  # port 1: always refused, never bound
    os.environ["NYAYA_SECOND_JUDGE_MODEL"] = "configc-smoke-unreachable"  # fixed string: skips /v1/models resolution
    try:
        config = _config_from_env()
        if config.second_judge is None:
            return False, "second_judge is not configured at all -- set NYAYA_SECOND_JUDGE_BASE_URL first"
        outcome = _run(config, fixtures["proof"]["facts"], fixtures["proof"]["contract_id"])
    finally:
        for env_var, saved in (("NYAYA_SECOND_JUDGE_BASE_URL", saved_url), ("NYAYA_SECOND_JUDGE_MODEL", saved_model)):
            if saved is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = saved
    ok = outcome == "REFER_TO_LAWYER"
    return ok, f"expected REFER_TO_LAWYER, got {outcome}" if not ok else "REFER_TO_LAWYER as expected"


def run_default(fixtures: dict[str, Any]) -> int:
    if not os.environ.get("NYAYA_SECOND_JUDGE_BASE_URL"):
        print(
            "REFUSING: NYAYA_SECOND_JUDGE_BASE_URL is not set -- this smoke test needs config C fully "
            "configured per the runbook (checks 1-2 exercise the AND-gate; check 3 overrides the URL "
            "temporarily, it does not turn config C on from nothing). Use --rollback to verify config C off.",
            file=sys.stderr,
        )
        return 2

    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("1: PROOF-able item", lambda: check_proof(fixtures)),
        ("2: negative_not_proved", lambda: check_negative_not_proved(fixtures)),
        ("3: second judge unavailable -> REFER", lambda: check_second_judge_unavailable_refers(fixtures)),
        ("4: red-team leading narrative -> not PROOF", lambda: check_red_team_leading_narrative(fixtures)),
    ]
    return _run_checks(checks)


def run_rollback(fixtures: dict[str, Any]) -> int:
    """R1, 2026-09-25: verifies rollback actually reverts to config-C-off behavior, not just that an env var
    is unset. Requires NYAYA_SECOND_JUDGE_BASE_URL to be UNSET -- the opposite of `run_default`'s guard."""
    if os.environ.get("NYAYA_SECOND_JUDGE_BASE_URL"):
        print(
            "REFUSING: NYAYA_SECOND_JUDGE_BASE_URL is set -- --rollback verifies config C is OFF; unset it "
            "(and the whole second_judge yaml block, if any) before running this mode",
            file=sys.stderr,
        )
        return 2

    config = _config_from_env()
    if config.second_judge is not None:
        print(f"FAIL: second_judge is configured ({config.second_judge!r}) despite the env being unset -- "
              f"check for a second_judge: block in configs/nyaya_agent.yaml", file=sys.stderr)
        return 1
    agent = NyayaAgent.house(ROOT, config=config)
    if isinstance(agent.judge, AndGateJudge):
        print("FAIL: NyayaAgent.house built an AndGateJudge despite second_judge being None", file=sys.stderr)
        return 1
    print("[PASS] second_judge is None and NyayaAgent.house built a plain judge (not AndGateJudge)")

    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("1: PROOF-able item (primary alone)", lambda: check_proof(fixtures)),
        ("2: negative_not_proved (primary alone)", lambda: check_negative_not_proved(fixtures)),
        ("3: red-team leading narrative -> not PROOF (primary alone)", lambda: check_red_team_leading_narrative(fixtures)),
    ]
    return _run_checks(checks)


def _run_checks(checks: list[tuple[str, Callable[[], tuple[bool, str]]]]) -> int:
    failed: list[str] = []
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 -- a smoke test reports every failure, never crashes silently
            ok, detail = False, f"{type(e).__name__}: {e}"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            failed.append(name)

    if failed:
        print(f"\n{len(failed)} check(s) failed: {failed}", file=sys.stderr)
        return 1
    print(f"\nAll {len(checks)} check(s) passed.")
    return 0


def main() -> int:
    fixtures = _load_fixtures()
    if "--rollback" in sys.argv[1:]:
        return run_rollback(fixtures)
    return run_default(fixtures)


if __name__ == "__main__":
    raise SystemExit(main())
