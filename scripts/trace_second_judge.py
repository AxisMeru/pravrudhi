#!/usr/bin/env python3
"""Trace the second (32B) judge on fixed IPC 415 cases, through the engine's own `HouseJudge` code path: the exact
serve-time prompt, the raw completion, the first token's label logprobs, the clamp, and the resulting score.

No credential is read or printed here. Authorization is either injected by an egress proxy for the judge's host, or
taken from NYAYA_SECOND_JUDGE_API_KEY by `HouseJudge.from_config` -- never echoed. One completion per (case, element);
a failed call is reported, never retried.

    python scripts/trace_second_judge.py --base-url https://api.runpod.ai/v2/<endpoint_id>/openai/v1 --model <served id>
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import yaml

from pravrudhi.application.nyaya_judges import HouseJudge, JudgeRequest, build_house_prompt

ROOT = Path(__file__).resolve().parent.parent
ELEMENTS = [
    "deceives another person, whether by affirmative misrepresentation or by dishonest concealment of facts",
    "the deception fraudulently or dishonestly induces the deceived person",
    "the inducement causes delivery of property to any person, or consent that any person shall retain property",
]
CASE_FACTS = [
    "In the present case, the complaint alleges that the petitioners induced the complainant to supply EVA raw material "
    "worth approximately ₹77,92,720/- upon an assurance that payment would be made within thirty days.",
    "It is further alleged that despite repeated demands, no payment was made and that the petitioners acted pursuant "
    "to a pre-planned design.",
    "The complainant has specifically alleged that the petitioners induced him to part with goods worth ₹77,92,720/- by "
    "representing themselves to be established traders, assuring payment within one month and promising to pay above "
    "the prevailing market price.",
    "It is further alleged that after obtaining delivery of the goods, the petitioners failed to honour their assurances "
    "and deliberately avoided payment.",
]
POSITIVE = [
    "The accused told the complainant that they were established traders and would pay for the goods within thirty days.",
    "When the accused said this, they knew it was false: they had no business and no intention of paying.",
    "Believing these statements, the complainant delivered EVA raw material worth Rs 77,92,720 to the accused.",
    "The accused never paid and stopped answering the complainant's calls.",
]
NEGATIVE = [
    "The complainant supplied goods to the buyer on credit under a written contract.",
    "The buyer made no statement about its business or its ability to pay.",
    "The buyer paid half the price and withheld the rest, disputing the quality of the remaining goods.",
    "The buyer has offered in writing to return the disputed goods.",
]
NARRATIVE = "A supplier alleges that two buyers obtained raw material on credit by misrepresentation and did not pay."
CASES = {
    "case_empty_narrative": (CASE_FACTS, ""),
    "case_with_narrative": (CASE_FACTS, NARRATIVE),
    "positive_control": (POSITIVE, ""),
    "negative_control": (NEGATIVE, ""),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="the second judge's OpenAI-compatible /v1 base URL")
    ap.add_argument("--model", required=True, help="the served model id the deployment pins")
    ap.add_argument("--tau", type=float, default=0.97)
    ap.add_argument("--collapse-facts", action="store_true", help="match a deployment that collapses fact whitespace")
    ap.add_argument("--cases", nargs="*", default=list(CASES))
    ap.add_argument("--out", default="second_judge_trace.jsonl")
    a = ap.parse_args()

    cfg = yaml.safe_load((ROOT / "configs" / "nyaya_agent.yaml").read_text())
    statute = cfg["judge_statute_text"]["ipc415_property"]
    hj = cfg["house_judge"]
    judge = HouseJudge.from_config(
        {"base_url": a.base_url, "model": a.model, "statute_chars": hj["statute_chars"], "max_tokens": hj["max_tokens"],
         "top_logprobs": hj["top_logprobs"], "timeout_s": 300, "label_mass_floor": hj["label_mass_floor"],
         "collapse_facts": a.collapse_facts},
        tau=a.tau, api_key_env="NYAYA_SECOND_JUDGE_API_KEY",
    )
    failures = 0
    with open(a.out, "w", encoding="utf-8") as out:
        for name in a.cases:
            facts, narrative = CASES[name]
            for el in ELEMENTS:
                req = JudgeRequest(contract_id="ipc415_property", element=el, is_denial=False, statute=statute,
                                   narrative=narrative, facts=tuple((f"F{i}", t) for i, t in enumerate(facts, 1)))
                prompt = build_house_prompt(req, statute_chars=judge.statute_chars, collapse_facts=a.collapse_facts)
                row: dict[str, object] = {"case": name, "element": el, "prompt": prompt}
                try:
                    j = judge.judge(req)
                    row.update(status=j.status, p=j.p_established, clamp=j.clamp, raw=j.raw, model=j.backend_model,
                               prompt_sha256=j.prompt_sha256, first_token=j.first_token)
                    labels = (j.first_token or {}).get("labels", {})
                    logit = math.log(j.p_established / (1 - j.p_established)) if 0 < j.p_established < 1 else None
                    k8 = "—" if logit is None else str(round(logit * 8, 3))
                    print(f"{name:<22} {el[:38]:<38} p={j.p_established:.3f} logit*8={k8:<6} "
                          f"clamp={j.clamp:<11} top1={(j.first_token or {}).get('top1')!r} labels={labels} raw={j.raw[:40]!r}")
                except Exception as e:  # noqa: BLE001 -- reported, never retried
                    failures += 1
                    msg = f"{type(e).__name__}: {e}"[:600]
                    row["error"] = msg
                    print(f"{name:<22} {el[:38]:<38} ERROR {msg}")
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"\nwrote {a.out}" + (f" ({failures} failed)" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
