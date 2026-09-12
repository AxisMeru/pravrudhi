"""The prabhasa-nyaya surface over HTTP: ask, audit, corpus, history. User-facing in both editions.

Thin by design: every rule lives in `application/nyaya.py`, so the CLI and the Telegram bot get the same
guarantees without going through FastAPI. `ask_fn` is injectable so the whole route can be exercised against a
fake vendor with no CLI or key present. Every route declares its response model, as the schema test requires:
a client generated from `/openapi.json` must know the shape of a verdict without reading Python.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.api.workspace_root import RootError, root_for
from pravrudhi.application import nyaya, panel
from pravrudhi.application.credentials import CredentialStore, store_for_session


class AskRequest(BaseModel):
    question: str
    vendors: list[str] = Field(default_factory=lambda: ["claude-cli"])
    k: int = 8
    checker: str | None = None


class AuditRequest(BaseModel):
    sources: str
    answer: str
    checker: str = "claude-cli"


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


class NyayaCorpusResponse(BaseModel):
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
                {"id": d.id, "act": d.act, "section": d.section, "title": d.title, "score": s, "text": d.text}
                for d, s in c.retrieve(q, k=k)
            ]
            if q.strip()
            else []
        )
        return {"documents": len(c.documents), "sources": c.sources, "hits": hits}

    @router.get("/asks", response_model=NyayaAsksResponse)
    def asks(limit: int = 20, workspace: str | None = None, user: User | None = CurrentUserDep) -> dict[str, Any]:
        project, _store = _session(user, workspace)
        return {"asks": nyaya.recent_asks(project, limit=limit)}

    @router.post("/ask", response_model=NyayaAskResponse)
    def ask_ep(
        req: AskRequest, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        if not req.question.strip():
            raise HTTPException(422, "an empty question asks nothing")
        project, store = _session(user, workspace)
        try:
            rec = nyaya.ask(
                project, req.question, tuple(req.vendors), k=req.k, checker=req.checker, ask_fn=ask_fn, store=store
            )
        except KeyError as e:
            raise HTTPException(422, str(e)) from e
        return rec.to_dict()

    @router.post("/audit", response_model=NyayaAudit, response_model_by_alias=True)
    def audit_ep(
        req: AuditRequest, workspace: str | None = None, user: User | None = CurrentUserDep
    ) -> dict[str, Any]:
        if not req.answer.strip():
            raise HTTPException(422, "nothing to audit")
        project, store = _session(user, workspace)
        try:
            return nyaya.audit(project, req.sources, req.answer, req.checker, ask_fn=ask_fn, store=store)
        except KeyError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(503, f"checker unavailable: {e}") from e

    return router
