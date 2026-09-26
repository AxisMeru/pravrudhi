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
5. **red_team_negation_probes** (Lead-2, 2026-09-26, Gate 0 follow-up -- NEGATION-PROBES-2026-09-26.md): 3
   single-element probes (the hardest of 12 tested against the real judge(s), by smallest logit-distance
   from either judge's own tau), each a fact that explicitly NEGATES the element it's asked about. Asserts
   the real judge(s) never say `established` on any of them -- a P0 if one does. Single-element checks via
   `agent.judge.judge()`, not full contract runs (the property under test is the judge's own read of a
   negated fact, not contract completeness or the quote/Lean layers -- Gate 0's subject, not this check's).

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

Every check prints its outcome, reason, and `unavailable_second` regardless of pass/fail (Lead-2, 2026-09-26)
-- so a reader never has to re-derive from a bare PASS/FAIL whether a result reflects the judges' actual
answer or an infrastructure gap that happened to land on the right (or wrong) side. Check 1 specifically
labels a failure caused by the second judge being unavailable as `COLD/UNAVAILABLE` (most often a cold
endpoint mid-warm-up timing out inside `NYAYA_SECOND_JUDGE_TIMEOUT_S`), distinct from a genuine `FAIL` --
that failure mode is REFER-by-design, not evidence the fixture or the judges disagree with the expected
outcome.

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
from pravrudhi.application.nyaya_judges import AndGateJudge, JudgeRequest  # noqa: E402

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
    probes = data.get("red_team_negation_probes")
    if not isinstance(probes, list) or not probes:
        print("REFUSING: fixture file is missing a valid 'red_team_negation_probes' list", file=sys.stderr)
        raise SystemExit(2)
    for probe in probes:
        if not all(k in probe for k in ("id", "contract_id", "element", "statute", "fact", "narrative")):
            print(f"REFUSING: 'red_team_negation_probes' entry {probe!r} is missing a required field", file=sys.stderr)
            raise SystemExit(2)
    return data


def _config_from_env() -> AgentConfig:
    """`load_agent_config` already reads every NYAYA_SECOND_JUDGE_* / NYAYA_HOUSE_JUDGE_* override -- this
    smoke test never re-derives that logic, only calls it, per the runbook's own env table. `audit_dir` is
    overridden to a throwaway tmp dir (`_SMOKE_AUDIT_DIR`), never the production path from the yaml."""
    return dataclasses.replace(load_agent_config(ROOT), audit_dir=_SMOKE_AUDIT_DIR)


class _RunResult:
    """Everything a check needs to report: the outcome, the contract's own reason, and which elements (if
    any) the second judge was unavailable for. Printed for EVERY check, pass or fail (Lead-2, 2026-09-26) --
    so a reader never has to guess whether a given result reflects the judges' actual answer or an
    infrastructure gap that happened to land on the right side by luck."""

    def __init__(self, outcome: str, reason: str, unavailable_second: list[str]) -> None:
        self.outcome = outcome
        self.reason = reason
        self.unavailable_second = unavailable_second

    def __str__(self) -> str:
        return f"outcome={self.outcome} reason={self.reason} unavailable_second={self.unavailable_second}"


def _run(
    config: AgentConfig, facts: list[str], contract_id: str,
    narrative: str = "Smoke-test facts, not a real case.",
) -> _RunResult:
    agent = NyayaAgent.house(ROOT, config=config)
    run = agent.run(facts, narrative=narrative, contract_ids=[contract_id])
    c = run.contracts[0]
    return _RunResult(c.outcome, c.reason, c.unavailable_second)


def check_proof(fixtures: dict[str, Any]) -> tuple[bool, str, str]:
    fx = fixtures["proof"]
    r = _run(_config_from_env(), fx["facts"], fx["contract_id"])
    if r.outcome == "PROOF":
        return True, "PASS", f"PROOF as expected ({r})"
    if r.unavailable_second:
        # Lead-2, 2026-09-26: a FAIL here caused by the second judge being unavailable (most often a cold
        # endpoint mid-warm-up, timing out inside NYAYA_SECOND_JUDGE_TIMEOUT_S) is an infrastructure signal,
        # not a verdict failure -- REFER-by-design (second_judge_unavailable), not evidence the fixture or
        # the judges disagree with the expected PROOF. Reported distinctly so nobody has to re-derive this
        # from the reason string by hand every time.
        return False, "COLD/UNAVAILABLE", f"second judge unavailable, not a verdict failure ({r})"
    return False, "FAIL", f"expected PROOF, got {r.outcome} ({r})"


def check_negative_not_proved(fixtures: dict[str, Any]) -> tuple[bool, str, str]:
    """Passes on DENIAL, ABSTAIN, or REFER_TO_LAWYER -- fails ONLY on PROOF. The safety property is "no
    false proof," not any specific non-PROOF outcome. (A second-judge-unavailable REFER here is still a
    PASS -- "not proved" is the requirement either way -- but the reason/unavailable_second fields are still
    printed so a reader can tell the two apart.)"""
    fx = fixtures["negative_not_proved"]
    r = _run(_config_from_env(), fx["facts"], fx["contract_id"])
    if r.outcome != "PROOF":
        return True, "PASS", f"not proved ({r})"
    return False, "FAIL", f"FALSE PROOF: got PROOF on a known court-negative item ({r})"


def check_red_team_leading_narrative(fixtures: dict[str, Any]) -> tuple[bool, str, str]:
    """Standing red-team case (Lead-2, 2026-09-26, from the narrative-sensitivity investigation): a sealed
    court-negative item whose missing required element is asserted directly by a LEADING narrative (no new
    facts added), to check the AND-gate does not let free text substitute for a quotable fact. Passes on
    DENIAL, ABSTAIN, or REFER_TO_LAWYER -- fails ONLY on PROOF, same convention as `negative_not_proved`.
    Uses the fixture's OWN `narrative` field (not the shared placeholder) -- this is the one fixture where
    the narrative itself is the point of the test."""
    fx = fixtures["red_team_leading_narrative"]
    r = _run(_config_from_env(), fx["facts"], fx["contract_id"], narrative=fx["narrative"])
    if r.outcome != "PROOF":
        return True, "PASS", f"not proved ({r})"
    return False, "FAIL", f"FALSE PROOF: a leading narrative asserting the missing element produced PROOF ({r})"


def check_red_team_negation_probes(fixtures: dict[str, Any]) -> tuple[bool, str, str]:
    """Standing red-team case (Lead-2, 2026-09-26, Gate 0 follow-up -- NEGATION-PROBES-2026-09-26.md): does
    the judge itself fall for a fact that explicitly NEGATES the element it's asked about? Gate 0 found a
    false-PROOF gap on an INJECTED "established" assertion with a negating quote (neither the quote check nor
    Lean can see semantic negation) -- this checks whether the real judges independently manufacture that
    "established" assertion in the first place, which they must not. Runs 3 probes (the hardest of 12 tested,
    by smallest logit-distance from either judge's own tau -- see the fixture file's own provenance field)
    directly through `agent.judge.judge()` (the real AndGateJudge in default mode, the primary HouseJudge
    alone under --rollback) -- a single-element check, not a full contract run, since the property under
    test is the judge's read of a negated fact, not contract completeness or the quote/Lean layers. Fails if
    ANY probe comes back `established` -- that would be a P0 (an explicit negation getting proved)."""
    agent = NyayaAgent.house(ROOT, config=_config_from_env())
    established = []
    for probe in fixtures["red_team_negation_probes"]:
        req = JudgeRequest(
            contract_id=probe["contract_id"], element=probe["element"], is_denial=False,
            statute=probe["statute"], narrative=probe["narrative"], facts=(("F1", probe["fact"]),),
        )
        judgment = agent.judge.judge(req)
        if judgment.status == "established":
            established.append(f"{probe['id']} (p_established={judgment.p_established})")
    if established:
        return False, "FAIL", f"P0: negation probe(s) established: {established}"
    return True, "PASS", f"all {len(fixtures['red_team_negation_probes'])} negation probes correctly not established"


def check_second_judge_unavailable_refers(fixtures: dict[str, Any]) -> tuple[bool, str, str]:
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
            return False, "FAIL", "second_judge is not configured at all -- set NYAYA_SECOND_JUDGE_BASE_URL first"
        r = _run(config, fixtures["proof"]["facts"], fixtures["proof"]["contract_id"])
    finally:
        for env_var, saved in (("NYAYA_SECOND_JUDGE_BASE_URL", saved_url), ("NYAYA_SECOND_JUDGE_MODEL", saved_model)):
            if saved is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = saved
    if r.outcome == "REFER_TO_LAWYER":
        return True, "PASS", f"REFER_TO_LAWYER as expected ({r})"
    return False, "FAIL", f"expected REFER_TO_LAWYER, got {r.outcome} ({r})"


def run_default(fixtures: dict[str, Any]) -> int:
    if not os.environ.get("NYAYA_SECOND_JUDGE_BASE_URL"):
        print(
            "REFUSING: NYAYA_SECOND_JUDGE_BASE_URL is not set -- this smoke test needs config C fully "
            "configured per the runbook (checks 1-2 exercise the AND-gate; check 3 overrides the URL "
            "temporarily, it does not turn config C on from nothing). Use --rollback to verify config C off.",
            file=sys.stderr,
        )
        return 2

    checks: list[tuple[str, Callable[[], tuple[bool, str, str]]]] = [
        ("1: PROOF-able item", lambda: check_proof(fixtures)),
        ("2: negative_not_proved", lambda: check_negative_not_proved(fixtures)),
        ("3: second judge unavailable -> REFER", lambda: check_second_judge_unavailable_refers(fixtures)),
        ("4: red-team leading narrative -> not PROOF", lambda: check_red_team_leading_narrative(fixtures)),
        ("5: red-team negation probes -> not established", lambda: check_red_team_negation_probes(fixtures)),
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

    checks: list[tuple[str, Callable[[], tuple[bool, str, str]]]] = [
        ("1: PROOF-able item (primary alone)", lambda: check_proof(fixtures)),
        ("2: negative_not_proved (primary alone)", lambda: check_negative_not_proved(fixtures)),
        ("3: red-team leading narrative -> not PROOF (primary alone)", lambda: check_red_team_leading_narrative(fixtures)),
        ("4: red-team negation probes -> not established (primary alone)", lambda: check_red_team_negation_probes(fixtures)),
    ]
    return _run_checks(checks)


def _run_checks(checks: list[tuple[str, Callable[[], tuple[bool, str, str]]]]) -> int:
    """Each check returns (ok, label, detail). `label` is normally "PASS"/"FAIL", but a check may report a
    distinct label (e.g. check_proof's "COLD/UNAVAILABLE") for a failure that reflects an infrastructure gap
    rather than a genuine verdict disagreement -- `ok` still drives the exit code and the failed-list, only
    the printed label and the caller's read of WHY differ."""
    failed: list[str] = []
    for name, fn in checks:
        try:
            ok, label, detail = fn()
        except Exception as e:  # noqa: BLE001 -- a smoke test reports every failure, never crashes silently
            ok, label, detail = False, "FAIL", f"{type(e).__name__}: {e}"
        print(f"[{label}] {name}: {detail}")
        if not ok:
            failed.append(f"{name} ({label})")

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
