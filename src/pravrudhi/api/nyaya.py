"""The prabhasa-nyaya surface over HTTP: ask, audit, corpus, history. User-facing in both editions.

Thin by design: every rule lives in `application/nyaya.py`, so the CLI and the Telegram bot get the same
guarantees without going through FastAPI. `ask_fn` is injectable so the whole route can be exercised against a
fake vendor with no CLI or key present. Every route declares its response model, as the schema test requires:
a client generated from `/openapi.json` must know the shape of a verdict without reading Python.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pravrudhi.api.errors import CHECKER_UNAVAILABLE, REGISTRY_CHECKER_UNAVAILABLE, VENDOR_NOT_ALLOWED, coded, coded_503
from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.api.workspace_root import RootError, root_for
from pravrudhi.application import nyaya, panel
from pravrudhi.application.credentials import CredentialStore, store_for_session
from pravrudhi.application.tenant_vendors import VendorNotAllowed


class AskRequest(BaseModel):
    question: str
    vendors: list[str] = Field(default_factory=lambda: ["claude-cli"])
    k: int = 8
    checker: str | None = None
    #: Required when checker="lean" (Track A P1b, ADR-0003): which contract nyaya_lean.check scores every
    #: answer against. An unknown or missing id is refused (422), never silently defaulted.
    contract_id: str | None = None


class AuditRequest(BaseModel):
    sources: str
    answer: str
    checker: str = "claude-cli"
    #: Required when checker="lean"; see AskRequest's own field doc.
    contract_id: str | None = None


class NyayaVendor(BaseModel):
    id: str
    model: str
    interface: str
    available: bool
    why: str | None
    note: str


class NyayaVendorsResponse(BaseModel):
    vendors: list[NyayaVendor]


class NyayaCorpusHit(BaseModel):
    id: str
    act: str
    section: str
    title: str
    score: float
    text: str
    #: The condition on showing this text (every hit carries it, so a hit copied out alone still has it).
    notice: str
    #: The page the corpus recorded for this act on India Code, present ONLY when the corpus recorded one (BNS, BNSS, BSA
    #: today; null for the IPC and the Constitution); never a constructed link.
    source_url: str | None
    #: Always set: the India Code home page, for a reader of a hit whose act has no recorded page (so every hit has
    #: the notice AND a source link). A fallback, never a deep link.
    source_fallback_url: str


class NyayaCorpusResponse(BaseModel):
    #: The statute-text notice (the exact wording): `hits[].text` is unofficial and the official version is on India Code.
    #: The wording is the one counsel pack Q6 leaves open and changes when counsel answers.
    notice: str
    documents: int
    sources: list[dict[str, Any]]
    hits: list[NyayaCorpusHit]


class NyayaCitation(BaseModel):
    id: str
    status: str


class NyayaAudit(BaseModel):
    checker: str
    verdict: str
    span: str | None = None
    class_: str | None = Field(default=None, alias="class")
    why: str | None = None
    raw: str | None = None
    wall_s: float | None = None
    provenance: str | None = None
    #: `checker="lean"` only (Track A P1, ADR-0003): the specific claim(s) prabhasa-nyaya's compiled Lean
    #: scorer found the contract does not license, named rather than left as a bare "unlicensed" verdict.
    unlicensed_claims: list[str] | None = None
    #: `checker="lean"` only: how many `notFormalisable` entries the checked contract itself declares.
    not_formalisable_count: int | None = None

    model_config = {"populate_by_name": True}


class NyayaAnswer(BaseModel):
    vendor: str
    model: str
    text: str
    wall_s: float
    citations: list[NyayaCitation]
    verdict: str
    confidence: str
    error: str | None = None
    audit: NyayaAudit | None = None


class NyayaSource(BaseModel):
    id: str
    act: str
    section: str
    title: str


class NyayaAskResponse(BaseModel):
    id: str
    asked_at: str
    question: str
    sources: list[NyayaSource]
    answers: list[NyayaAnswer]
    provenance: str
    note: str


class NyayaAsksResponse(BaseModel):
    asks: list[NyayaAskResponse]


class NyayaRegistryContractEntry(BaseModel):
    id: str
    validated: bool
    sources: list[str] | None = None


class NyayaRegistryContractsResponse(BaseModel):
    #: The twenty-one BNS/IPC registry contract ids in KNOWN_CONTRACT_IDS (two BNSS 187 contracts
    #: excluded due to Lean-side defects) -- a DIFFERENT family from `AskRequest.contract_id`/
    #: `AuditRequest.contract_id` above (those are the citation-shaped "0"/"5" ids `checker="lean"` uses).
    #: Deliberately not folded into `AskRequest` or `AuditRequest`: these contracts score explicit
    #: per-element assertions, never free text, so they cannot be a `checker` value on the existing
    #: ask/audit path (see `registry_check` below).
    contracts: list[str]
    #: Same ids, with `validated` (true only for ids in the scorer's `validated_contracts`; every other id
    #: can only ever return REFER_TO_LAWYER `contract_not_validated`) and `sources` (the statute act/section
    #: the Lean binary cites; null if the binary is not available). Added alongside `contracts`, not instead.
    entries: list[NyayaRegistryContractEntry] = []


class NyayaRegistryElementsResponse(BaseModel):
    contract_id: str
    elements: list[str]


class NyayaRegistryCheckRequest(BaseModel):
    contract_id: str
    #: Element name -> Met (`True`) / Not-Met-or-unaddressed (`False`). Supplied directly by the caller --
    #: today, a person using the manual element-audit UI; the element-first harness's Stage 1 judge is
    #: meant to supply the same shape once it exists. This route never derives assertions from free text
    #: itself.
    assertions: dict[str, bool]
    #: Optional per-element source-text spans, for display only -- never sent to the Lean binary and never
    #: itself verified (see `application.nyaya.registry_check`'s own doc).
    evidence: dict[str, str] | None = None


class NyayaRegistryCheckResponse(BaseModel):
    checker: str
    contract_id: str
    verdict: str
    denied_claims: list[str]
    unlicensed_claims: list[str]
    omitted_claims: list[str]
    elements: dict[str, bool]
    evidence: dict[str, str]
    provenance: str


def build_nyaya_router(root: Path, ask_fn: panel.AskFn | None = None) -> APIRouter:
    engine_root = Path(root)
    router = APIRouter(prefix="/api/nyaya")

    def _session(user: User | None, workspace: str | None) -> tuple[Path, CredentialStore]:
        """This caller's own project and the credential store that belongs to it.

        Resolved exactly the way every other user-facing route resolves a project (`workspace_root.root_for`)
        and exactly the way a signed-in caller's model access is meant to reach a key
        (`credentials.store_for_session`) -- so the operator's own admin-labeled desktop session travels the
        identical path a BYOK product user's session does, and neither `roles.py`'s label nor anything here
        can bend that path for one and not the other.
        """
        try:
            project = root_for(user, workspace, engine_root=engine_root)
        except RootError as e:
            raise HTTPException(400, str(e)) from e
        return project, store_for_session(project, engine_root=engine_root, user=user).store

    @router.get("/vendors", response_model=NyayaVendorsResponse)
    def vendors(workspace: str | None = None, user: User | None = CurrentUserDep) -> dict[str, Any]:
        project, store = _session(user, workspace)
        return {"vendors": nyaya.available_vendors(project, store=store)}

    @router.get("/corpus", response_model=NyayaCorpusResponse)
    def corpus(
        q: str = "", k: int = 8, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        project, _store = _session(user, workspace)
        c = nyaya.load_corpus(project)
        hits = (
            [
                {
                    "id": d.id, "act": d.act, "section": d.section, "title": d.title, "score": s, "text": d.text,
                    "notice": nyaya.STATUTE_NOTICE, "source_url": nyaya.recorded_source_url(d.act, c.sources),
                    "source_fallback_url": nyaya.INDIA_CODE_HOME,
                }
                for d, s in c.retrieve(q, k=k)
            ]
            if q.strip()
            else []
        )
        return {"notice": nyaya.STATUTE_NOTICE, "documents": len(c.documents), "sources": c.sources, "hits": hits}

    @router.get("/asks", response_model=NyayaAsksResponse)
    def asks(limit: int = 20, workspace: str | None = None, user: User | None = CurrentUserDep) -> dict[str, Any]:
        project, _store = _session(user, workspace)
        return {"asks": nyaya.recent_asks(project, limit=limit)}

    @router.post("/ask", response_model=NyayaAskResponse)
    def ask_ep(
        req: AskRequest, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        if not req.question.strip():
            raise HTTPException(422, "an empty question asks nothing")
        project, store = _session(user, workspace)
        try:
            rec = nyaya.ask(
                project, req.question, tuple(req.vendors), k=req.k, checker=req.checker,
                contract_id=req.contract_id, ask_fn=ask_fn, store=store,
            )
        except VendorNotAllowed:  # the message names the caller-supplied vendor id: a fixed message and a code instead (#318)
            return coded(403, VENDOR_NOT_ALLOWED)
        except (KeyError, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        return rec.to_dict()

    @router.post("/audit", response_model=NyayaAudit, response_model_by_alias=True)
    def audit_ep(
        req: AuditRequest, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        if not req.answer.strip():
            raise HTTPException(422, "nothing to audit")
        project, store = _session(user, workspace)
        try:
            return nyaya.audit(
                project, req.sources, req.answer, req.checker, contract_id=req.contract_id,
                ask_fn=ask_fn, store=store,
            )
        except (KeyError, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError:
            logging.getLogger(__name__).warning("checker unavailable", exc_info=True)  # server-side only (#318)
            return coded_503(CHECKER_UNAVAILABLE)

    @router.get("/registry/contracts", response_model=NyayaRegistryContractsResponse)
    def registry_contracts_ep(user: User | None = CurrentUserDep) -> dict[str, Any]:
        del user  # auth-gated like every other route below; the id list itself carries nothing per-user
        return {"contracts": nyaya.registry_contract_ids(), "entries": nyaya.registry_contract_entries(engine_root)}

    @router.get("/registry/{contract_id}/elements", response_model=NyayaRegistryElementsResponse)
    def registry_elements_ep(
        contract_id: str, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        project, _store = _session(user, workspace)
        try:
            elements = nyaya.registry_elements(project, contract_id)
        except (KeyError, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError:
            logging.getLogger(__name__).warning("registry checker unavailable", exc_info=True)  # server-side only (#318)
            return coded_503(REGISTRY_CHECKER_UNAVAILABLE)
        return {"contract_id": contract_id, "elements": elements}

    @router.post("/registry/check", response_model=NyayaRegistryCheckResponse)
    def registry_check_ep(
        req: NyayaRegistryCheckRequest, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any] | JSONResponse:
        if not req.assertions:
            raise HTTPException(422, "at least one element assertion is required")
        project, _store = _session(user, workspace)
        try:
            return nyaya.registry_check(
                project, req.contract_id, req.assertions, evidence=req.evidence,
            )
        except (KeyError, ValueError) as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError:
            logging.getLogger(__name__).warning("registry checker unavailable", exc_info=True)  # server-side only (#318)
            return coded_503(REGISTRY_CHECKER_UNAVAILABLE)

    return router
