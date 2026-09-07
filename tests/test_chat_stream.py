"""The streaming twin of `/api/chat`: the same honesty guarantee, but delivered as the turn happens.

A chat reply that arrives all at once after a long silence is the single most noticeable way this surface felt
less finished than a competitor's. These tests hold the streaming route to the same standard `test_chat.py`
holds the blocking one to, plus what streaming adds: tool events must precede the result they announce, no
number can reach the wire before the tool call that would license it has resolved, and a model that never
answers must not strand the user's message half-written for a fallback call to duplicate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pravrudhi.api.chat import build_chat_router
from pravrudhi.application.chat import ChatEndpointUnreachable, converse, converse_stream
from pravrudhi.application.memory_store import FileMemoryStore
from pravrudhi.application.objectives import Benchmark, Objective, write
from pravrudhi_kernel.ledger import LedgerWriter

OBJECTIVE = Objective(
    id="legal-intent",
    intent="Answer questions of law with the statute relied on.",
    track="nyaya",
    benchmarks=(Benchmark(id="law", tool="lm-eval", metric="law acc,none"),),
)


class FakeModel:
    """A scripted `Complete`: one canned answer per round."""

    def __init__(self, *script: dict[str, Any]) -> None:
        self.script = list(script)
        self.seen: list[list[dict[str, str]]] = []

    def __call__(self, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.seen.append(list(messages))
        return self.script.pop(0) if self.script else {"content": "", "tool_calls": []}


class RaisingModel:
    """A `Complete` that answers once, exactly as scripted, then behaves like a dead endpoint.

    The first round is real so a run can show genuine tool progress before the failure - a model that raises
    on its very first call would not exercise anything a truly mid-turn failure needs to prove.
    """

    def __init__(self, first: dict[str, Any]) -> None:
        self._first = first
        self.calls = 0

    def __call__(self, messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return self._first
        raise ChatEndpointUnreachable("no chat model answered at test://nowhere")


def _measured_workspace(root: Path) -> Path:
    """An objective with a baseline and one candidate scored against it, admitted as external-eval rows."""
    write(root, OBJECTIVE)
    ledger = root / "research" / "ledger.jsonl"
    writer = LedgerWriter.open(ledger, "0.1.0")
    for condition, value in (("base", 0.4), ("candidate", 0.5)):
        writer.append(
            "audit",
            "auditor",
            {
                "kind": "external_eval",
                "severity": "info",
                "tier": "external",
                "track": OBJECTIVE.track,
                "condition": condition,
                "tool": "lm-eval",
                "metrics": {"law": {"acc,none": value, "acc_stderr,none": 0.01}},
                "n_samples": {"law": 1000},
            },
            epoch=0,
            night=1,
        )
    return ledger


def _events(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    return list(converse_stream(*args, **kwargs))


def _sse_events(resp: Any) -> list[dict[str, Any]]:
    return [json.loads(chunk[len("data: "):]) for chunk in resp.text.split("\n\n") if chunk.strip()]


def test_tool_events_precede_their_result_and_the_reply_follows_every_tool(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    model = FakeModel(
        {"content": "", "tool_calls": [{"tool": "objective_progress", "args": {"id": OBJECTIVE.id}}]},
        {"content": "The candidate scores 0.5 on law acc,none against a baseline of 0.4.", "tool_calls": []},
    )

    events = _events(tmp_path, "how is the legal objective doing?", complete=model)

    tool_events = [e for e in events if e["type"] == "tool"]
    assert [e["phase"] for e in tool_events] == ["called", "returned"]
    assert {e["tool"] for e in tool_events} == {"objective_progress"}
    assert "acc,none" in tool_events[1]["result_summary"]

    first_reply_event = next(i for i, e in enumerate(events) if e["type"] not in ("tool",))
    assert all(e["type"] == "tool" for e in events[:first_reply_event])
    assert events[-1]["type"] == "done"

    reply_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "0.5" in reply_text and "0.4" in reply_text
    assert reply_text == events[-1]["reply"]

    citation_events = [e for e in events if e["type"] == "citation"]
    assert citation_events and events.index(citation_events[-1]) < events.index(events[-1])
    assert {c["seq"] for c in citation_events} == {c["seq"] for c in events[-1]["citations"]}


def test_an_ungrounded_number_never_reaches_the_stream(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    model = FakeModel(
        {"content": "", "tool_calls": [{"tool": "objectives", "args": {}}]},
        {
            "content": "The harness now passes 49% of the suite. Evidence accumulates on the nyaya track.",
            "tool_calls": [],
        },
    )

    events = _events(tmp_path, "how good is the harness?", complete=model)

    # "49%" is legitimate inside a refusal's explanation of what got removed and why - the guarantee is that
    # it never appears as a stated fact, i.e. inside a `token` or the final `reply`, anywhere in the stream.
    reply_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "49" not in reply_text
    assert "nyaya track" in reply_text
    done = events[-1]
    assert done["type"] == "done"
    assert "49" not in done["reply"]
    assert len(done["refusals"]) == 1 and "49%" in done["refusals"][0]


def test_a_tool_averse_models_grounded_answer_still_streams_its_numbers(tmp_path: Path) -> None:
    """The grounding pre-pass dispatches before the model is asked anything, so its `tool` events come first
    even though nothing in the conversation licensed them yet - the model never called a tool itself here."""
    ledger = _measured_workspace(tmp_path)
    model = FakeModel({"content": "The candidate scores 0.5 on law acc,none against a baseline of 0.4.",
                        "tool_calls": []})

    events = _events(tmp_path, "What is the baseline for the legal-intent objective? Cite rows.", complete=model)

    assert len(model.seen) == 1
    tool_events = [e for e in events if e["type"] == "tool"]
    assert [e["phase"] for e in tool_events] == ["called", "returned"]
    reply_text = "".join(e["text"] for e in events if e["type"] == "token")
    assert "0.5" in reply_text and "0.4" in reply_text
    assert ledger.exists()


def test_a_mid_turn_failure_leaves_no_half_written_message_for_a_retry_to_duplicate(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    model = RaisingModel({"content": "", "tool_calls": [{"tool": "objective_progress", "args": {"id": OBJECTIVE.id}}]})

    collected: list[dict[str, Any]] = []
    with pytest.raises(ChatEndpointUnreachable):
        for ev in converse_stream(tmp_path, "how is the legal objective doing?", complete=model):
            collected.append(ev)

    assert any(e["type"] == "tool" for e in collected)  # the user saw real progress before the failure
    assert not any(e["type"] in ("token", "citation", "done") for e in collected)
    assert FileMemoryStore(tmp_path).threads() == []  # no orphaned user turn sitting without a reply

    # a caller falling back to the blocking route with the same message gets exactly one clean exchange,
    # not a second copy of a user turn the failed stream already wrote
    working = FakeModel(
        {"content": "", "tool_calls": [{"tool": "objective_progress", "args": {"id": OBJECTIVE.id}}]},
        {"content": "The candidate scores 0.5 on law acc,none against a baseline of 0.4.", "tool_calls": []},
    )
    outcome = converse(tmp_path, "how is the legal objective doing?", complete=working)
    assert "0.5" in outcome.reply
    thread = FileMemoryStore(tmp_path).thread(outcome.thread_id)
    assert [t.role for t in thread.turns] == ["user", "assistant"]


def test_the_stream_route_emits_sse_events_ending_in_done(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    model = FakeModel(
        {"content": "", "tool_calls": [{"tool": "objective_progress", "args": {"id": OBJECTIVE.id}}]},
        {"content": "The candidate scores 0.5 on law acc,none against a baseline of 0.4.", "tool_calls": []},
    )
    app = FastAPI()
    app.include_router(build_chat_router(tmp_path, complete=model))
    client = TestClient(app)

    resp = client.post("/api/chat/stream", json={"message": "how is the legal objective doing?", "thread_id": None})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp)
    assert events[0] == {"type": "tool", "phase": "called", "tool": "objective_progress",
                          "args": {"id": OBJECTIVE.id}}
    assert events[-1]["type"] == "done"
    assert "0.5" in events[-1]["reply"] and "0.4" in events[-1]["reply"]
    assert events[-1]["thread_id"]


def test_the_stream_route_turns_an_unreachable_endpoint_into_an_error_event(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    model = RaisingModel({"content": "", "tool_calls": [{"tool": "objective_progress", "args": {"id": OBJECTIVE.id}}]})
    app = FastAPI()
    app.include_router(build_chat_router(tmp_path, complete=model))
    client = TestClient(app)

    resp = client.post("/api/chat/stream", json={"message": "how is the legal objective doing?", "thread_id": None})

    assert resp.status_code == 200
    events = _sse_events(resp)
    assert events[-1]["type"] == "error"
    assert "test://nowhere" in events[-1]["error"]
    assert FileMemoryStore(tmp_path).threads() == []


def test_the_stream_route_rejects_a_blank_message(tmp_path: Path) -> None:
    app = FastAPI()
    app.include_router(build_chat_router(tmp_path, complete=FakeModel()))
    client = TestClient(app)

    assert client.post("/api/chat/stream", json={"message": "   ", "thread_id": None}).status_code == 422


def test_the_blocking_chat_route_is_unaffected_by_the_streaming_addition(tmp_path: Path) -> None:
    _measured_workspace(tmp_path)
    model = FakeModel(
        {"content": "", "tool_calls": [{"tool": "objective_progress", "args": {"id": OBJECTIVE.id}}]},
        {"content": "The candidate scores 0.5 on law acc,none against a baseline of 0.4.", "tool_calls": []},
    )
    app = FastAPI()
    app.include_router(build_chat_router(tmp_path, complete=model))
    client = TestClient(app)

    resp = client.post("/api/chat", json={"message": "how is the legal objective doing?", "thread_id": None})

    assert resp.status_code == 200
    body = resp.json()
    assert "0.5" in body["reply"] and "0.4" in body["reply"]
    assert body["refusals"] == []
