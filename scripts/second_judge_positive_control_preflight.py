"""Issue #44: the live preflight CLI for the standing second-judge positive control.

Run before any worker takes traffic (deploy-time preflight) and on a schedule -- never through the public
analyse-facts path. Calls the REAL second-judge endpoint with the production prompt/template (via
`HouseJudge`, the same class the served `AndGateJudge` uses for its second slot), scores both sealed control
sets, and prints the verdict. The caller -- deploy pipeline or scheduler -- is responsible for actually
flipping the deployed second_judge to a state AndGateJudge's existing second_judge_unavailable path will
catch; this script only decides, it does not itself mutate the running deployment.

EXIT CODES. Issue #100, finding 2: this script used to exit 0 when `second_judge:` was unconfigured -- the
state the repo ships in -- so a caller reading only the exit code could not tell "the control ran and
passed" from "the control never ran", which is the one failure a positive control exists to prevent.

  0 -- EXIT_AVAILABLE:     the control RAN, over the full pinned sealed sets, and passed every gate.
  1 -- EXIT_UNAVAILABLE:   the control RAN and failed a gate (or the endpoint was unreachable).
  2 -- EXIT_NOT_EVALUATED: the control DID NOT RUN. No second judge configured, private control data
                           absent, or a sealed set that could not be verified against its pin. A
                           `NOT_EVALUATED:` line names which, and the verdict printed is still
                           UNAVAILABLE (fail closed). Never 0: absence of evaluation is not a pass.

Both 1 and 2 are non-zero, so a caller that only checks `!= 0` fails closed on all three of issue #100's
required behaviours without having to understand the distinction.

Needs PRAVRUDHI_EDITION=dev, or NYAYA_HOUSE_JUDGE_MODEL (and NYAYA_SECOND_JUDGE_MODEL) set: it loads the agent
config, which refuses an unnamed judge model in every edition but an explicit development one (#237).

Cross-repo: the sealed control sets and their source eval_items.jsonl live in prabhasa-nyaya.
  PRAVRUDHI_ROOT (default: this repo's root) -- for configs/nyaya_agent.yaml.
  PRABHASA_NYAYA_ROOT (required) -- for research/gates/P2b/{element_judgment_v1/eval_items.jsonl,
    configC/second_judge_positive_control/{established_200,ne_discrimination_71}.json}.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("PRAVRUDHI_ROOT", Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(REPO_ROOT / "src"))

from pravrudhi.application.nyaya_agent import load_agent_config  # noqa: E402
from pravrudhi.application.nyaya_judges import HouseJudge, JudgeOutputError, JudgeRequest  # noqa: E402
from pravrudhi.application.second_judge_positive_control import (  # noqa: E402
    ControlElement,
    PrivateControlDataUnavailable,
    SealedControlVerificationError,
    decide_availability,
    load_sealed_manifest,
    load_verified_control_sets,
    resolve_private_root,
    run_live_check,
    write_record,
)

EXIT_AVAILABLE = 0
EXIT_UNAVAILABLE = 1
EXIT_NOT_EVALUATED = 2

# `load_control_elements` used to live here: it read both sealed JSONs and accepted `{"ids": []}` with no
# count or digest assertion (issue #100, finding 1). It is gone rather than fixed in place, so there is no
# unverified loader left for a caller to reach for; `load_verified_control_sets` in
# src/pravrudhi/application/second_judge_positive_control.py replaces it, verifies each file against
# configs/sealed_control_manifest.yaml first, and is the only route to the PinnedCounts that
# `decide_availability` now requires. Living in `src` also puts it under CI's `mypy src`, which this
# `scripts/` file is not covered by.


def load_request_context(nyaya_root: Path) -> dict[tuple[str, str], dict]:
    """(item_id, element_id) -> {statute, narrative, facts, element_desc}, needed to build the JudgeRequest
    the production prompt actually uses -- the sealed control-set JSONs only carry ids/gold_status/sealed
    score, not the request content itself."""
    eval_items_path = nyaya_root / "research" / "gates" / "P2b" / "element_judgment_v1" / "eval_items.jsonl"
    context = {}
    for line in eval_items_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        context[(row["item_id"], row["element_id"])] = row
    return context


def build_score_fn(cfg, context: dict[tuple[str, str], dict]):
    judge = HouseJudge.from_config(cfg.second_judge, tau=float(cfg.second_judge["tau"]),
                                    api_key_env="NYAYA_SECOND_JUDGE_API_KEY")

    def score(element: ControlElement) -> float:
        row = context[(element.item_id, element.element_id)]
        statute = cfg.judge_statute_text.get(row["contract_id"], row.get("statute", ""))
        facts = tuple((f["id"], f["text"]) for f in row["facts"] if f["id"] != "F_narrative")
        narrative = next((f["text"] for f in row["facts"] if f["id"] == "F_narrative"), "")
        request = JudgeRequest(
            contract_id=row["contract_id"], element=row["element_desc"], is_denial=False,
            statute=statute, narrative=narrative, facts=facts,
        )
        try:
            judgment = judge.judge(request)
        except JudgeOutputError as e:
            raise RuntimeError(f"malformed reply from second judge for {element.item_id}__"
                                f"{element.element_id}: {e}") from e
        return judgment.p_established

    return score


def not_evaluated(why: str) -> int:
    """Prints the NOT_EVALUATED state and a fail-closed verdict, and returns EXIT_NOT_EVALUATED.

    Both lines are printed on purpose. A caller scraping `VERDICT:` and a caller reading the exit code have
    to reach the same conclusion, and neither may read "the control did not run" as a pass."""
    print(f"NOT_EVALUATED: {why}")
    print("VERDICT: UNAVAILABLE (fail closed)")
    print(f"  reason: not_evaluated -- {why}")
    return EXIT_NOT_EVALUATED


def main() -> int:
    try:
        nyaya_root = resolve_private_root()
    except PrivateControlDataUnavailable as e:
        return not_evaluated(f"private control data unavailable: {e}")
    cfg = load_agent_config(REPO_ROOT)
    if not cfg.second_judge:
        # Issue #100, finding 2. This branch used to `return 0`. Config-off genuinely is today's default
        # single-judge behaviour, but "nothing was checked" and "the check passed" must not share an exit
        # code: a scheduler or deploy pipeline reading only the code cannot tell them apart, and the state
        # it would read as a pass is the state the repo ships in.
        return not_evaluated(
            "no second_judge configured in configs/nyaya_agent.yaml, so zero elements were evaluated. "
            "Config-off is today's default single-judge behaviour and is not itself a control failure, "
            "but it is not a control PASS either -- exiting non-zero so no caller can read the absence "
            "of evaluation as a verified second judge."
        )
    try:
        manifest = load_sealed_manifest(REPO_ROOT)
        sets = load_verified_control_sets(nyaya_root, manifest)
    except SealedControlVerificationError as e:
        return not_evaluated(f"sealed control set could not be verified against its pin: {e}")

    import yaml

    body_path = REPO_ROOT / "configs" / "nyaya_agent.yaml"
    control_cfg = yaml.safe_load(body_path.read_text())["second_judge_positive_control"]

    context = load_request_context(nyaya_root)
    score_fn = build_score_fn(cfg, context)

    result = run_live_check(sets.established, sets.ne, score_fn=score_fn,
                             parity_tau=float(control_cfg["ne_discrimination_tau"]))
    verdict = decide_availability(
        result, parity_floor=float(control_cfg["parity_floor"]),
        parity_median_abs_dp=float(control_cfg["parity_median_abs_dp"]),
        ne_discrimination_min=int(control_cfg["ne_discrimination_min"]),
        pinned=sets.pinned,
    )

    # The same four conditions `decide_availability` fails closed on, restated here so the measurement
    # block below has real objects to print. Explicit `is None` tests rather than bare `assert`s: asserts
    # are stripped under `python -O`, and a preflight that loses its narrowing in an optimised run is not
    # the kind of thing this file should be adding more of.
    if (
        result.endpoint_unavailable
        or result.parity is None
        or result.ne is None
        or result.established is None
    ):
        print(f"ENDPOINT UNAVAILABLE: {result.endpoint_error}")
    else:
        print(f"parity: {result.parity.n_tau_agree}/{result.parity.n_total} "
              f"({result.parity.agree_rate:.4%}), median|dp|={result.parity.median_abs_dp:.4f}")
        print(f"ne_discrimination: {result.ne.n_correct}/{result.ne.n_total}")
        print(f"established (recorded, not a gate): p>=0.5: {result.established.n_p_ge_half}/"
              f"{result.established.n_total}, p>=tau: {result.established.n_p_ge_tau}/"
              f"{result.established.n_total}")
        print(f"pinned planned n: established {sets.pinned.established}, ne {sets.pinned.ne}, "
              f"total {sets.pinned.total}")
        accuracy = result.established.accuracy
        if accuracy is None:
            # accuracy is None iff n_total == 0. Printing "0.0% accuracy" or crashing on `None * 100` would
            # both misreport it; the verdict above has already failed closed on the count identity.
            print("established accuracy: NOT EVALUATED (zero elements) -- the drop alert did not run")
        else:
            baseline_pct = 95.5  # sealed dry-run baseline, established_200, 2026-09-26
            drop = baseline_pct - accuracy * 100
            alert_drop = float(control_cfg["est_accuracy_alert_drop"])
            if drop > alert_drop:
                print(f"ALERT (not a gate): established accuracy dropped {drop:.1f} points below the "
                      f"{baseline_pct}% sealed baseline (threshold {alert_drop} points)")

    print(f"VERDICT: {'AVAILABLE' if verdict.available else 'UNAVAILABLE (fail closed)'}")
    for reason in verdict.reasons:
        print(f"  reason: {reason}")

    # Issue #44 trigger wiring (Lead-2, 2026-09-26): write the record the engine gates on -- ALWAYS, whether
    # this run passed or failed, so a failing run's record is itself what makes the engine fail closed (a
    # missing record and a recorded failure both refuse the second judge; only a fresh, matching, PASSING
    # record lets AndGateJudge reach it). Nothing is written when record_path is unset (the gate is OFF by default).
    record_path = control_cfg.get("record_path")
    if record_path:
        # No default: an unset endpoint_id/adapter_sha is written as None, never "" -- check_record refuses
        # to match on a None expected identity rather than letting two unset configs "agree" as equal
        # empty strings.
        write_record(
            Path(record_path), available=verdict.available,
            endpoint_id=cfg.second_judge.get("endpoint_id"),
            adapter_sha=cfg.second_judge.get("adapter_sha"), reasons=verdict.reasons,
        )
        print(f"record written: {record_path}")
    else:
        print("no record_path configured -- record not written (the engine's record gate is OFF unless record_path is set)")

    return EXIT_AVAILABLE if verdict.available else EXIT_UNAVAILABLE


if __name__ == "__main__":
    raise SystemExit(main())
