"""Lawyer-reviewed structural check: a local demo page over the engine's REAL components.

A reviewer maps each required ingredient of a registry contract to a fact and a verbatim quote and confirms it. The
engine's own quote checker (`nyaya_quote.locate_quote`) checks every quote against the fact text; only an ingredient the
reviewer CONFIRMED and whose quote checked out is asserted Met. The assertions go to the real, sha256-pinned Lean
`score` binary (`BinaryRegistry.check`, the REG wire), exactly as the automated workflow sends them after its judges.

No model is called. Reviewer confirmations are human decisions and are labelled as such everywhere; they are never
presented as, or mixed with, model judgments. The automated analyse-facts workflow is untouched: this is a separate,
human-in-the-loop path. Every check is appended to a local JSONL record (the reviewer's name, mappings, the wire, the
binary's sha and its raw verdict).

    PRABHASA_NYAYA_SCORE_BIN=/abs/path/to/score uv run --frozen python scripts/reviewer_demo/app.py

The page is served on http://127.0.0.1:8765 (override with REVIEWER_DEMO_PORT). It refuses to start unless the binary's
sha256 equals configs/nyaya_agent.yaml's pinned_score_sha256.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import uvicorn
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_agent import (
    BinaryRegistry,
    assemble_assertions,
    expected_outcome,
    outcome_from_lean,
)
from pravrudhi.application.nyaya_gold_score import SCORE_BIN_ENV
from pravrudhi.application.nyaya_quote import locate_quote

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
RECORD = Path(os.environ.get("REVIEWER_DEMO_RECORD", HERE / "review_record.jsonl"))
#: The contracts this demo offers. Each must be a checkable registry contract; the binary itself supplies its ingredients.
CONTRACTS = ("ipc415_property",)

#: The case shown (the four facts as filed in the reported matter). Test data, no client names.
CASE_FACTS = {
    "F1": "In the present case, the complaint alleges that the petitioners induced the complainant to supply EVA raw "
          "material worth approximately ₹77,92,720/- upon an assurance that payment would be made within thirty days.",
    "F2": "It is further alleged that despite repeated demands, no payment was made and that the petitioners acted "
          "pursuant to a pre-planned design.",
    "F3": "The complainant has specifically alleged that the petitioners induced him to part with goods worth "
          "₹77,92,720/- by representing themselves to be established traders, assuring payment within one month and "
          "promising to pay above the prevailing market price.",
    "F4": "It is further alleged that after obtaining delivery of the goods, the petitioners failed to honour their "
          "assurances and deliberately avoided payment.",
}

#: Recorded model judgments for the same four facts (hosted engine 0.6.34, 4 Nov 2026, run nyaya-agent-1afefc404f).
#: Shown for contrast only, read-only; never used by the check on this page.
RECORDED_MODEL_RUN = {
    "run_id": "nyaya-agent-1afefc404f",
    "when": "2026-11-04 02:51 UTC, hosted engine 0.6.34",
    "outcome": "REFER_TO_LAWYER (second_judge_not_confirmed)",
    "scores": {
        "deceives another person, whether by affirmative misrepresentation or by dishonest concealment of facts":
            {"screening_4b": 0.947, "confirming_32b": 0.349},
        "the deception fraudulently or dishonestly induces the deceived person": {"screening_4b": 0.924, "confirming_32b": 0.245},
        "the inducement causes delivery of property to any person, or consent that any person shall retain property":
            {"screening_4b": 0.980, "confirming_32b": 0.269},
    },
}


def _pinned_sha() -> str:
    cfg = yaml.safe_load((ROOT / "configs" / "nyaya_agent.yaml").read_text())
    return str(cfg["pinned_score_sha256"])


def _registry() -> BinaryRegistry:
    path = os.environ.get(SCORE_BIN_ENV)
    if not path:
        raise SystemExit(f"{SCORE_BIN_ENV} is not set: point it at the pinned Lean `score` binary")
    # BinaryRegistry refuses a binary whose full sha256 differs from the pin, before any subprocess call.
    return BinaryRegistry(Path(path), pinned_sha256=_pinned_sha())


class Mapping_(BaseModel):
    element: str
    fact_id: str | None = None
    quote: str | None = None
    #: The reviewer's explicit confirmation. Unconfirmed rows are never asserted Met.
    confirmed: bool = False
    note: str | None = Field(default=None, max_length=1000)


class CheckRequest(BaseModel):
    contract_id: str
    reviewer: str = Field(min_length=1, max_length=120)
    label: str | None = Field(default=None, max_length=120)
    mappings: list[Mapping_]


def create_app(registry: BinaryRegistry) -> FastAPI:
    app = FastAPI(title="Pravrudhi reviewer demo")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(HERE / "index.html")

    @app.get("/api/meta")
    def meta() -> dict[str, Any]:
        return {
            "binary_sha256": registry.sha256,
            "pinned_sha256": _pinned_sha(),
            "binary_path_name": Path(registry.score_bin).name,
            "contracts": list(CONTRACTS),
            "facts": CASE_FACTS,
            "recorded_model_run": RECORDED_MODEL_RUN,
        }

    @app.get("/api/contract/{contract_id}")
    def contract(contract_id: str) -> dict[str, Any]:
        if contract_id not in CONTRACTS:
            raise HTTPException(404, "not offered in this demo")
        d = registry.describe(contract_id)  # the binary's own --describe-contract
        return {"contract_id": contract_id, "elements": d.elements, "denials": d.denials,
                "source": registry.source_text(contract_id), "sections": registry.list_contracts().get(contract_id, [])}

    @app.post("/api/check")
    def check(req: CheckRequest) -> dict[str, Any]:
        if req.contract_id not in CONTRACTS:
            raise HTTPException(404, "not offered in this demo")
        d = registry.describe(req.contract_id)
        known = set(d.elements) | set(d.denials)
        unknown = [m.element for m in req.mappings if m.element not in known]
        if unknown:
            raise HTTPException(422, f"not an ingredient of {req.contract_id}: {unknown}")
        by_el = {m.element: m for m in req.mappings}
        rows: list[dict[str, Any]] = []
        met: dict[str, bool] = {}
        for el in [*d.elements, *d.denials]:
            m = by_el.get(el)
            loc = locate_quote(CASE_FACTS, fact_id=m.fact_id if m else None, quote=m.quote if m else None)
            confirmed = bool(m and m.confirmed)
            asserted = confirmed and loc.valid
            met[el] = asserted
            rows.append({
                "element": el, "is_denial": el in d.denials,
                "reviewer_confirmed": confirmed, "fact_id": m.fact_id if m else None, "quote": m.quote if m else None,
                "note": m.note if m else None,
                "quote_check": {"valid": loc.valid, "reason": loc.reason, "start": loc.start, "end": loc.end,
                                "occurrences": loc.occurrences, "match": loc.match},
                "asserted_met": asserted,
            })
        assertions = assemble_assertions(d, met)
        wire = reg.reg_wire_line(assertions, req.contract_id)
        lean = registry.check(assertions, req.contract_id)  # the real binary, REG wire
        outcome = outcome_from_lean(lean)
        local = expected_outcome(d, assertions)
        result = {
            "contract_id": req.contract_id, "reviewer": req.reviewer, "label": req.label,
            "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rows": rows, "assertions": assertions, "wire": wire,
            "wire_sha256": reg.reg_wire_sha256(assertions, req.contract_id),
            "binary_sha256": registry.sha256, "lean": lean, "lean_outcome": outcome,
            "local_cross_check": local, "cross_check_agrees": local == outcome,
            "source_of_confirmations": "human reviewer (not a model judgment)",
        }
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        with RECORD.open("a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        return result

    @app.get("/api/record")
    def record() -> list[dict[str, Any]]:
        if not RECORD.exists():
            return []
        return [json.loads(line) for line in RECORD.read_text(encoding="utf-8").splitlines() if line.strip()][-20:]

    return app


def main() -> int:
    registry = _registry()
    for cid in CONTRACTS:
        registry.describe(cid)  # fail at start, not mid-demo, if the binary does not know the contract
    port = int(os.environ.get("REVIEWER_DEMO_PORT", "8765"))
    print(f"Lean score binary verified: sha256 {registry.sha256}")
    print(f"Reviewer demo on http://127.0.0.1:{port}  (record: {RECORD})")
    uvicorn.run(create_app(registry), host="127.0.0.1", port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
