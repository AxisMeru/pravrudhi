"""The chat surface over HTTP: three routes, and no place for the model to reach the client unfiltered.

The engine's other routes each answer one replayed question, which meant the only way to ask "is my objective
working, and what should I do next" was to know which three endpoints to call and how to read them. This
router is the conversational front door to the same replay functions - and deliberately nothing more than a
front door: every honesty rule lives in `application/chat.py`, so a second caller (a CLI turn, a scheduled
summary) gets the same guarantees without going through FastAPI.

`complete` is a parameter of the router rather than a module-level default so that the whole route, including
persistence and the response contract, can be exercised against a fake model with no endpoint running.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pravrudhi.api.identity import CurrentUserDep, User
from pravrudhi.api.schemas import ChatResponse, ChatThreadDetailResponse, ChatThreadsResponse
from pravrudhi.application.chat import ChatEndpointUnreachable, Complete, converse, converse_stream
from pravrudhi.application.memory import MemoryError as MemoryStoreError
from pravrudhi.application.memory_store import store_for


class ChatRequest(BaseModel):
    """What the user said, and which conversation it belongs to. A null `thread_id` starts a new one rather
    than appending to whichever thread happened to be most recent."""

    message: str
    thread_id: str | None = None


def build_chat_router(root: Path, complete: Complete | None = None) -> APIRouter:
    workspace = Path(root)
    router = APIRouter(prefix="/api")

    if complete is None and not os.environ.get("PRAVRUDHI_CHAT_ENDPOINT", "").strip():
        # Without this the conversation points at a local OpenAI-compatible server read from the environment,
        # and on a machine where that is not running every turn answers 503 — which is what it did here, on the
        # one surface the operator would need if the session driving this work went away. The engine already
        # knows several working hosted models, which are measured, cooled and returned to by the same routing
        # table the swarm uses. An explicitly configured endpoint still wins: this is the fallback, not a policy.
        from pravrudhi.application.network_chat import network_complete

        complete = network_complete(workspace)

    @router.post("/chat", response_model=ChatResponse)
    async def chat_ep(req: ChatRequest, user: User | None = CurrentUserDep) -> dict[str, Any]:
        """Answer one turn. Any number the turn's tools did not return is stripped and reported under
        `refusals`, so a reply is either traceable to the ledger or visibly missing a sentence."""
        if not req.message.strip():
            raise HTTPException(422, "a chat turn with no message asks nothing")
        try:
            outcome = converse(workspace, req.message, thread_id=req.thread_id, user=user, complete=complete)
        except ChatEndpointUnreachable as exc:
            raise HTTPException(503, str(exc)) from exc
        return outcome.to_dict()

    @router.post("/chat/stream", response_model=ChatResponse)
    async def chat_stream_ep(req: ChatRequest, user: User | None = CurrentUserDep) -> StreamingResponse:
        """The same turn as `/chat`, delivered as server-sent events while it happens: `tool` when the
        assistant calls a tool and again when it returns, `token` for each piece of the finished, honesty-
        checked reply, `citation` per ledger row it stands on, and `done` with the same payload `/chat` returns
        in one piece - which is also what `response_model` documents here, since the raw `StreamingResponse`
        this returns bypasses it at runtime and a `done` event is the one SSE frame shaped exactly like it.
        A turn whose model endpoint never answers is reported as an `error` event rather than a dropped
        connection - `converse_stream` leaves the user's message unpersisted until it has a reply, so a client
        that falls back to `/chat` for the same message will not find it already sitting in the thread with
        nothing answering it.
        """
        if not req.message.strip():
            raise HTTPException(422, "a chat turn with no message asks nothing")

        def events() -> Iterator[str]:
            try:
                for ev in converse_stream(workspace, req.message, thread_id=req.thread_id, user=user,
                                          complete=complete):
                    yield f"data: {json.dumps(ev)}\n\n"
            except ChatEndpointUnreachable as exc:
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @router.get("/chat/threads", response_model=ChatThreadsResponse)
    async def threads_ep(user: User | None = CurrentUserDep) -> dict[str, Any]:
        """The caller's conversations. A logged-in user's follow their account; a local engine's stay on disk."""
        store = store_for(workspace, user)
        return {
            "threads": [{"id": t.id, "updated": t.updated, "turns": len(t.turns)} for t in store.threads()]
        }

    @router.get("/chat/threads/{thread_id}", response_model=ChatThreadDetailResponse)
    async def thread_ep(thread_id: str, user: User | None = CurrentUserDep) -> dict[str, Any]:
        """One conversation in full. A thread that does not exist is a 404, not an empty conversation: the
        two are different facts and a client that conflates them will silently start writing into nothing."""
        store = store_for(workspace, user)
        try:
            thread = store.thread(thread_id)
        except MemoryStoreError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {
            "id": thread.id,
            "turns": [{"role": t.role, "content": t.content, "created": t.ts} for t in thread.turns],
        }

    return router
