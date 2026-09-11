"""The prabhasa-nyaya surface over HTTP: ask, audit, corpus, history. User-facing in both editions.

Thin by design: every rule lives in `application/nyaya.py`, so the CLI and the Telegram bot get the same
guarantees without going through FastAPI. `ask_fn` is injectable so the whole route can be exercised against a
fake vendor with no CLI or key present.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from pravrudhi.application import nyaya, panel


class AskRequest(BaseModel):
    question: str
    vendors: list[str] = Field(default_factory=lambda: ["claude-cli"])
    k: int = 8
    checker: str | None = None


class AuditRequest(BaseModel):
    sources: str
    answer: str
    checker: str = "claude-cli"


def build_nyaya_router(root: Path, ask_fn: panel.AskFn | None = None) -> APIRouter:
    workspace = Path(root)
    router = APIRouter(prefix="/api/nyaya")

    @router.get("/vendors")
    def vendors() -> dict[str, Any]:
        return {"vendors": nyaya.available_vendors(workspace)}

    @router.get("/corpus")
    def corpus(q: str = "", k: int = 8) -> dict[str, Any]:
        c = nyaya.load_corpus(workspace)
        hits = (
            [
                {"id": d.id, "act": d.act, "section": d.section, "title": d.title, "score": s, "text": d.text}
                for d, s in c.retrieve(q, k=k)
            ]
            if q.strip()
            else []
        )
        return {"documents": len(c.documents), "sources": c.sources, "hits": hits}

    @router.get("/asks")
    def asks(limit: int = 20) -> dict[str, Any]:
        return {"asks": nyaya.recent_asks(workspace, limit=limit)}

    @router.post("/ask")
    def ask_ep(req: AskRequest) -> dict[str, Any]:
        if not req.question.strip():
            raise HTTPException(422, "an empty question asks nothing")
        try:
            rec = nyaya.ask(workspace, req.question, tuple(req.vendors), k=req.k, checker=req.checker, ask_fn=ask_fn)
        except KeyError as e:
            raise HTTPException(422, str(e)) from e
        return rec.to_dict()

    @router.post("/audit")
    def audit_ep(req: AuditRequest) -> dict[str, Any]:
        if not req.answer.strip():
            raise HTTPException(422, "nothing to audit")
        try:
            return nyaya.audit(workspace, req.sources, req.answer, req.checker, ask_fn=ask_fn)
        except KeyError as e:
            raise HTTPException(422, str(e)) from e
        except RuntimeError as e:
            raise HTTPException(503, f"checker unavailable: {e}") from e

    return router
