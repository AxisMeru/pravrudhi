"""Issue #44: the live preflight CLI for the standing second-judge positive control.

Run before any worker takes traffic (deploy-time preflight) and on a schedule -- never through the public
analyse-facts path. Calls the REAL second-judge endpoint with the production prompt/template (via
`HouseJudge`, the same class the served `AndGateJudge` uses for its second slot), scores both sealed control
sets, and prints the verdict. Exit code 0 = available, 1 = second judge should be treated as unavailable
(the caller -- deploy pipeline or scheduler -- is responsible for actually flipping the deployed
second_judge to a state AndGateJudge's existing second_judge_unavailable path will catch; this script only
decides, it does not itself mutate the running deployment).

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
    decide_availability,
    resolve_private_root,
    run_live_check,
)


def load_control_elements(nyaya_root: Path) -> tuple[list[ControlElement], list[ControlElement]]:
    configc = nyaya_root / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
    established_raw = json.loads((configc / "established_200.json").read_text())
    ne_raw = json.loads((configc / "ne_discrimination_71.json").read_text())

    def to_elements(raw: dict) -> list[ControlElement]:
        return [
            ControlElement(row["item_id"], row["element_id"], row["sealed_reference_p"], row["gold_status"])
            for row in raw["ids"]
        ]

    return to_elements(established_raw), to_elements(ne_raw)


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


def main() -> int:
    try:
        nyaya_root = resolve_private_root()
    except PrivateControlDataUnavailable as e:
        print(f"REFUSING TO RUN: {e}")
        return 1  # fail closed: unable to verify means treated the same as verified-and-failed
    cfg = load_agent_config(REPO_ROOT)
    if not cfg.second_judge:
        print("no second_judge configured -- nothing to check, exiting 0 (config-off is not a control "
              "failure, it's today's default single-judge behaviour)")
        return 0
    import yaml

    body_path = REPO_ROOT / "configs" / "nyaya_agent.yaml"
    control_cfg = yaml.safe_load(body_path.read_text())["second_judge_positive_control"]

    established, ne = load_control_elements(nyaya_root)
    context = load_request_context(nyaya_root)
    score_fn = build_score_fn(cfg, context)

    result = run_live_check(established, ne, score_fn=score_fn, parity_tau=float(control_cfg["ne_discrimination_tau"]))
    verdict = decide_availability(
        result, parity_floor=float(control_cfg["parity_floor"]),
        parity_median_abs_dp=float(control_cfg["parity_median_abs_dp"]),
        ne_discrimination_min=int(control_cfg["ne_discrimination_min"]),
    )

    if result.endpoint_unavailable:
        print(f"ENDPOINT UNAVAILABLE: {result.endpoint_error}")
    else:
        print(f"parity: {result.parity.n_tau_agree}/{result.parity.n_total} "
              f"({result.parity.agree_rate:.4%}), median|dp|={result.parity.median_abs_dp:.4f}")
        print(f"ne_discrimination: {result.ne.n_correct}/{result.ne.n_total}")
        print(f"established (recorded, not a gate): p>=0.5: {result.established.n_p_ge_half}/"
              f"{result.established.n_total}, p>=tau: {result.established.n_p_ge_tau}/"
              f"{result.established.n_total}")
        baseline_pct = 95.5  # sealed dry-run baseline, established_200, 2026-09-26
        drop = baseline_pct - result.established.accuracy * 100
        alert_drop = float(control_cfg["est_accuracy_alert_drop"])
        if drop > alert_drop:
            print(f"ALERT (not a gate): established accuracy dropped {drop:.1f} points below the "
                  f"{baseline_pct}% sealed baseline (threshold {alert_drop} points)")

    print(f"VERDICT: {'AVAILABLE' if verdict.available else 'UNAVAILABLE (fail closed)'}")
    for reason in verdict.reasons:
        print(f"  reason: {reason}")

    return 0 if verdict.available else 1


if __name__ == "__main__":
    raise SystemExit(main())
