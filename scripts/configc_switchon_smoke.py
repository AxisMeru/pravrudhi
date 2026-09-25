"""Config C production switch-on smoke test (Track-C, 2026-09-25), for L2A.

Three checks against a FULLY-CONFIGURED `NyayaAgent.house()` (real judges, real Lean binary -- this script
makes live calls when run, it is not itself a live traffic change; it is code that L2A runs deliberately):

1. A PROOF-able item -- illustrative fixture, NOT YET VERIFIED against the real deployed judges (Track-C has
   no live model access in this $0, code-only task). L2A's first run of this script is also the first
   confirmation this fixture is correct.
2. A DENIAL item -- same caveat.
3. Second judge unavailable -> REFER_TO_LAWYER (`second_judge_unavailable`). Fully deterministic: this ONE
   check points NYAYA_SECOND_JUDGE_BASE_URL at an unreachable local port regardless of the real endpoint
   env, so it never depends on any judge's actual answer quality -- only on the AndGateJudge/NyayaAgent
   wiring merged in #10 (commit 078fea2, on main). This is the check Track-C is fully confident is correct
   before any live run.

Run with the runbook's full environment already set (CONFIGC-PRODUCTION-SWITCHON-RUNBOOK-2026-09-25.md).
Exits non-zero and prints which check(s) failed if any expectation doesn't match.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pravrudhi.application.nyaya_agent import AgentConfig, NyayaAgent, load_agent_config  # noqa: E402

#: A smoke run's audit trail goes to a throwaway tmp dir, not `configs/nyaya_agent.yaml`'s real
#: `audit_dir` -- `load_agent_config` has no env override for that key, so this script overrides the
#: loaded config's field directly (a frozen dataclass; `dataclasses.replace`, never a config file edit).
_SMOKE_AUDIT_DIR = Path(tempfile.mkdtemp(prefix="configc_smoke_audit_"))

# -- illustrative fixtures (NOT YET VERIFIED against real judges -- see module docstring) -------------------

PROOF_FACTS = [
    "The complainant states that the accused had been in a relationship with her and had repeatedly promised "
    "to marry her, most recently in a message sent the week before the incident.",
    "The complainant states that at the time the accused made this promise, he had already decided never to "
    "marry her and had made the same promise to another woman in the preceding month.",
    "The complainant states that relying on the promise, she had sexual intercourse with the accused on the "
    "date named in the complaint.",
]
DENIAL_FACTS = [
    "The complainant states that the accused forced himself upon her without her consent, using physical "
    "force to overcome her resistance, in the incident described in the complaint.",
    "The complainant states that she did not consent at any point before, during, or after the act.",
    "The complainant states that she reported the incident to the police the same night.",
]
CONTRACT_ID = "bns69"


def _config_from_env() -> AgentConfig:
    """`load_agent_config` already reads every NYAYA_SECOND_JUDGE_* / NYAYA_HOUSE_JUDGE_* override -- this
    smoke test never re-derives that logic, only calls it, per the runbook's own env table. `audit_dir` is
    overridden to a throwaway tmp dir (`_SMOKE_AUDIT_DIR`), never the production path from the yaml."""
    return dataclasses.replace(load_agent_config(ROOT), audit_dir=_SMOKE_AUDIT_DIR)


def _run(config: AgentConfig, facts: list[str]) -> str:
    agent = NyayaAgent.house(ROOT, config=config)
    run = agent.run(facts, narrative="Smoke-test facts, not a real case.", contract_ids=[CONTRACT_ID])
    return run.contracts[0].outcome


def check_proof() -> tuple[bool, str]:
    outcome = _run(_config_from_env(), PROOF_FACTS)
    ok = outcome == "PROOF"
    return ok, f"expected PROOF, got {outcome}" if not ok else "PROOF as expected"


def check_denial() -> tuple[bool, str]:
    outcome = _run(_config_from_env(), DENIAL_FACTS)
    ok = outcome == "DENIAL"
    return ok, f"expected DENIAL, got {outcome}" if not ok else "DENIAL as expected"


def check_second_judge_unavailable_refers() -> tuple[bool, str]:
    """Deterministic: NYAYA_SECOND_JUDGE_BASE_URL is overridden to an unreachable local port for THIS check
    only, regardless of whatever real endpoint the rest of this script's environment points at. Every other
    second_judge env var (tau, timeout, delta) is left as configured, since only reachability matters here.

    To confirm ROLLBACK (config C fully off) instead, unset NYAYA_SECOND_JUDGE_BASE_URL entirely before
    running this script -- `load_agent_config` then returns `second_judge=None`, `NyayaAgent.house` builds a
    plain `HouseJudge`, and this check's own premise (a second judge to be unavailable) does not apply; a
    config-C-off run is not expected to hit this code path at all."""
    saved = os.environ.get("NYAYA_SECOND_JUDGE_BASE_URL")
    os.environ["NYAYA_SECOND_JUDGE_BASE_URL"] = "http://127.0.0.1:1/v1"  # port 1: always refused, never bound
    try:
        config = _config_from_env()
        if config.second_judge is None:
            return False, "second_judge is not configured at all -- set NYAYA_SECOND_JUDGE_BASE_URL first"
        outcome = _run(config, PROOF_FACTS)
    finally:
        if saved is None:
            os.environ.pop("NYAYA_SECOND_JUDGE_BASE_URL", None)
        else:
            os.environ["NYAYA_SECOND_JUDGE_BASE_URL"] = saved
    ok = outcome == "REFER_TO_LAWYER"
    return ok, f"expected REFER_TO_LAWYER, got {outcome}" if not ok else "REFER_TO_LAWYER as expected"


def main() -> int:
    if not os.environ.get("NYAYA_SECOND_JUDGE_BASE_URL"):
        print(
            "REFUSING: NYAYA_SECOND_JUDGE_BASE_URL is not set -- this smoke test needs config C fully "
            "configured per the runbook (checks 1-2 exercise the AND-gate; check 3 overrides the URL "
            "temporarily, it does not turn config C on from nothing)",
            file=sys.stderr,
        )
        return 2
    checks = [
        ("1: PROOF-able item", check_proof),
        ("2: DENIAL item", check_denial),
        ("3: second judge unavailable -> REFER", check_second_judge_unavailable_refers),
    ]
    failed = []
    for name, fn in checks:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 -- a smoke test reports every failure, never crashes silently
            ok, detail = False, f"{type(e).__name__}: {e}"
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            failed.append(name)

    if failed:
        print(f"\n{len(failed)}/{len(checks)} checks failed: {failed}", file=sys.stderr)
        return 1
    print(f"\nAll {len(checks)} checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
