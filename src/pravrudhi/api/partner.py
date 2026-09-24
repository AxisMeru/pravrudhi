"""The partner API (L4, `docs/decisions/LEG-PLAN-2026-09-23.md`): `/api/v1`.

First endpoint only: `POST /api/v1/analyse-facts`, a thin HTTP wrapper over `application.nyaya_agent.
NyayaAgent` -- facts in, one `ContractResult` per selected registry contract out, `quote_source` visible per
element in the response (not just the final PROOF/DENIAL/ABSTAIN/REFER_TO_LAWYER verdict), so a partner's UI
can show a user which fact grounded each element rather than asking them to trust an opaque outcome.

Tenancy (orgs, API keys, per-org scoping) is a separate, larger L4 item and is NOT in this router -- this
endpoint currently rides the same optional session identity every other user-facing nyaya route uses
(`CurrentUserDep`), not a partner API key. Widening to real API-key auth is tracked as follow-up, stated here
rather than silently implied by the `/api/v1` prefix looking partner-ready.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.application import nyaya_lean_registry as reg
from pravrudhi.application.nyaya_agent import NyayaAgent


class AnalyseFactsRequest(BaseModel):
    facts: list[str]
    narrative: str = ""
    #: Which registry contracts to check. Omit both this and `sections` for every contract the configured
    #: registry knows how to check (`nyaya_agent.select_contracts`'s own default).
    contract_ids: list[str] | None = None
    sections: list[str] | None = None


class ElementResultOut(BaseModel):
    element: str
    is_denial: bool
    status: str
    claimed: bool
    p_established: float | None
    fact_id: str | None
    quote: str | None
    start: int | None
    end: int | None
    quote_check: str | None
    attempts: int
    occurrences: int
    offsets_source: str | None
    #: Who supplied the quote text (`"model"` today; `nyaya_judges.ElementJudgment`'s own field) -- surfaced
    #: per element, per Lead-2-assistant's explicit requirement, so a whole-fact claim is visible to the
    #: user rather than folded silently into the verdict.
    quote_source: str | None
    error: str | None


class ContractResultOut(BaseModel):
    contract_id: str
    outcome: str
    reason: str
    elements: list[ElementResultOut]
    assertions: dict[str, bool] | None
    lean: dict[str, Any] | None
    lean_outcome: str | None
    uncertain: list[str]
    statute_text_mismatch: bool | None


class AnalyseFactsResponse(BaseModel):
    run_id: str
    judge: str
    score_sha256: str
    facts: list[dict[str, str]]
    contracts: list[ContractResultOut]
    audit_path: str
    provenance: str = Field(default="agama")


AgentFactory = Callable[[Path], NyayaAgent]


def build_partner_router(root: Path, *, agent_factory: AgentFactory | None = None) -> APIRouter:
    """`agent_factory` is injectable (mirrors `nyaya.py`'s `ask_fn` pattern): production leaves it `None` and
    gets the configured house agent (`NyayaAgent.house`, real vLLM judge + real pinned Lean binary); tests
    supply a factory returning an agent built from scripted test doubles, the same shape `test_nyaya_agent.py`
    itself uses, so this router's own tests cover HTTP wiring only, not re-proving the agent's decision logic.
    """
    engine_root = Path(root)
    factory: AgentFactory = agent_factory or (lambda r: NyayaAgent.house(r))
    router = APIRouter(prefix="/api/v1")

    @router.post("/analyse-facts", response_model=AnalyseFactsResponse)
    def analyse_facts_ep(req: AnalyseFactsRequest, user: User | None = CurrentUserDep) -> dict[str, Any]:
        del user  # optional session identity today; see module docstring on tenancy/API-key auth
        if not req.facts or not any(f.strip() for f in req.facts):
            raise HTTPException(422, "at least one non-empty fact is required")
        try:
            agent = factory(engine_root)
            result = agent.run(
                req.facts, narrative=req.narrative, contract_ids=req.contract_ids, sections=req.sections,
            )
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        except reg.UnknownContractError as e:
            raise HTTPException(422, str(e)) from e
        except (FileNotFoundError, OSError) as e:
            raise HTTPException(503, f"nyaya agent unavailable: {e}") from e
        return result.to_dict()

    return router
