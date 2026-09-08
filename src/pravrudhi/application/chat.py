"""A conversation with the engine that cannot quote a number the engine did not just look up.

Every other surface in this codebase reaches its numbers by replaying the ledger: `objectives.progress`
recomputes a delta from external-eval rows on every call, and `evidence.py` renders documents straight from
those rows. A chat surface breaks that discipline in a way no other screen can, because the thing composing
the sentence is a language model: it will happily write "the harness improved by 4.8 points" whether or not
any row says so, and the sentence is indistinguishable from a true one. A screen that renders a stored number
can at least be traced to the store. A model's prose has no store behind it at all.

So the model here is never trusted with arithmetic or recall. It may only ask for tools, and the tools are
the same replay functions the rest of the engine uses. After the model has written its draft, `converse`
runs a final pass that deletes every sentence containing a numeral no tool returned this turn, and reports
each deletion under `refusals` with the sentence it came from. The guarantee therefore does not depend on
the model cooperating, on a system prompt being obeyed, or on a particular model being used: a number that
no tool produced cannot reach the caller, because the code removes it after the model is finished.

Matching is by literal value, not by equivalent value. A tool that returns `0.646` does not license the
reply "64.6%": that is arithmetic the tool did not do, and the assistant must quote a measurement the way
the ledger's own renderer forms it. The cost of this rule is an occasional stripped sentence that was in
fact true; the cost of the looser rule is a number the ledger never contained, which is the failure this
whole module exists to prevent.

`complete` is injected rather than constructed, because a tool-calling loop whose only test is "does it
reach the GPU" is untested. The default adapter talks to any OpenAI-compatible endpoint
(`PRAVRUDHI_CHAT_ENDPOINT`, defaulting to the proposer's local llama.cpp), and asks for a JSON draft rather
than using the native tools API, which llama.cpp does not implement.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from pravrudhi.api.identity import User
from pravrudhi.application.memory import MemoryError as MemoryStoreError
from pravrudhi.application.memory_store import MemoryStore, store_for

# The proposer's own default endpoint (`models.openai_compat.ChatClient`): a local llama.cpp server. The chat
# surface borrows whatever the proposer would have used unless the operator names another endpoint.
DEFAULT_ENDPOINT = "http://127.0.0.1:8080/v1"

# Four rounds is enough for "list the objectives, then read the one the user meant, then read its plan" and
# short enough that a model looping on a tool it keeps mis-calling cannot spend the turn doing nothing.
MAX_TOOL_ROUNDS = 4

# One model call: the conversation so far and the tools it may ask for, in; a draft and any tool requests, out.
# The returned dict carries "content" (the draft, possibly empty) and "tool_calls" ([{"tool", "args"}]).
Complete = Callable[[list[dict[str, str]], list[dict[str, Any]]], dict[str, Any]]

_NO_ARGS: dict[str, Any] = {"type": "object", "properties": {}, "required": []}


def _one_arg(name: str, description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {name: {"type": "string", "description": description}},
        "required": [name],
    }


# The whole of what the assistant may do. Nothing outside this tuple is dispatchable: `dispatch` refuses any
# other name rather than falling back to something plausible, because a tool the assistant invented is
# indistinguishable, in the reply, from one the engine provides.
TOOL_SCHEMA: tuple[dict[str, Any], ...] = (
    {"name": "objectives", "description": "Every objective in this workspace with its replayed standing.",
     "parameters": _NO_ARGS},
    {"name": "objective_progress", "description": "One objective's standing, recomputed from the ledger.",
     "parameters": _one_arg("id", "the objective id")},
    {"name": "objective_plan", "description": "The proposed decomposition of an objective's intent. Nothing "
                                              "in a plan has run.",
     "parameters": _one_arg("id", "the objective id")},
    {"name": "recipes", "description": "The recipe catalogue, each marked available or not on this machine.",
     "parameters": _NO_ARGS},
    {"name": "tools", "description": "The tool catalogue, each marked available or not on this machine.",
     "parameters": _NO_ARGS},
    {"name": "memory_recall", "description": "The user's durable notes matching a query.",
     "parameters": _one_arg("query", "what to look for; empty for the most recent notes")},
    {"name": "memory_remember", "description": "Record a durable fact for the user. Refused if it restates a "
                                               "ledger number.",
     "parameters": _one_arg("text", "the fact to remember, in the user's own terms")},
    {"name": "nights", "description": "Every night this engine has run, newest last: how many candidates it "
                                      "proposed, how many were pruned or promoted, and the GPU-hours it spent. "
                                      "Ask this for anything about a night, a candidate or the incumbent.",
     "parameters": _NO_ARGS},
    {"name": "candidates", "description": "Every candidate this engine holds, newest last, each with its badge "
                                          "— the same rows the candidates page renders.",
     "parameters": _NO_ARGS},
    {"name": "agent_trace", "description": "What the agents actually did, newest last: dispatches accepted or "
                                           "refused, usage limits hit, and routes handed on.",
     "parameters": _NO_ARGS},
    {"name": "routing_seats", "description": "Every route with its relative cost, its measured record, and "
                                             "whether it can be dispatched to now or is sitting out a usage "
                                             "limit and until when.",
     "parameters": _NO_ARGS},
    {"name": "routing_report", "description": "What the router would choose at each tier now, and why.",
     "parameters": _NO_ARGS},
    {"name": "evidence", "description": "A rendered evidence document by name.",
     "parameters": _one_arg("name", "the document name, without the .md suffix")},
    {"name": "appetite", "description": "What the engine wants right now: every drive's operands, the largest "
                                        "eligible deficit, and the first-person voice generated from them - the "
                                        "same figures the /appetite API endpoint serves.",
     "parameters": _NO_ARGS},
)

TOOL_NAMES = frozenset(str(t["name"]) for t in TOOL_SCHEMA)

# What a message's own wording is enough to justify calling, before the model has said anything. A model too
# small to reliably emit the JSON tool-call envelope still gets the ledger consulted on its behalf: keyed by
# the tool the keywords license, not scattered as magic strings through `_grounding_calls`.
_GROUNDING_KEYWORDS: dict[str, tuple[str, ...]] = {
    "objectives": ("baseline", "progress", "improv", "benchmark", "score", "result"),
    "objective_plan": ("plan", "loom", "steps"),
    "recipes": ("recipe",),
    "tools": ("tool",),
    "routing_report": ("routing",),
    "appetite": ("want", "appetite", "hungry", "hunger"),
}

# What the reply becomes when the message plainly asked a ledger question but the turn's tools - the ones the
# wording itself justified - came back with nothing to cite. Anything the model wrote instead is a guess this
# turn cannot verify, numeral-free or not, so the guess is what gets replaced, not just its numbers.
_GROUNDING_FALLBACK_REPLY = "I could not consult the ledger for that; try naming the objective."

# A numeral, but not one embedded in an identifier: "GSM8K" and "Qwen3-4B" are names, not claims, and a rule
# that treated their digits as measurements would delete every sentence that named a model.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?%?(?![A-Za-z0-9])")

# Sentence boundaries only where terminal punctuation is followed by space, so "0.646 and" keeps its decimal
# intact; a newline ends a sentence too, so one bad bullet does not take the list with it.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|\n+")

# Which key names a nested result well enough to say what a cited ledger row is about. `model` is deliberately
# absent: a Measurement carries one, and citing "the Qwen row" is less useful than citing "the GSM8K row".
_LABEL_KEYS = ("benchmark", "metric", "name", "id", "pack", "condition")

_EVIDENCE_NAME_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# How a finished reply is cut into `token` events for the streaming route: each chunk keeps its own trailing
# whitespace, so joining every chunk back together reproduces the reply exactly - a client that just concatenates
# `token.text` in order needs no separator logic of its own.
_TOKEN_CHUNK_RE = re.compile(r"\S+\s*")


@dataclass(frozen=True)
class ToolInvocation:
    """One dispatched tool call, with the raw result kept beside the summary.

    The raw result never reaches the caller — it is what the honesty pass checks the draft's numbers against,
    and what citations are harvested from. Only `result_summary` is shown, because a reply that pastes a whole
    replayed objective back at the user is not an answer.
    """

    tool: str
    args: dict[str, Any]
    result: dict[str, Any]
    result_summary: str
    refusal: str = ""
    grounding: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "args": dict(self.args), "result_summary": self.result_summary,
                "grounding": self.grounding}


@dataclass(frozen=True)
class ChatOutcome:
    """What one turn produced, after the honesty pass has run over the model's draft."""

    thread_id: str
    reply: str
    citations: tuple[dict[str, Any], ...]
    tool_calls: tuple[ToolInvocation, ...]
    refusals: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "reply": self.reply,
            "citations": [dict(c) for c in self.citations],
            "tool_calls": [t.to_dict() for t in self.tool_calls],
            "refusals": list(self.refusals),
        }


class ChatEndpointUnreachable(RuntimeError):
    """The model endpoint did not answer. Raised so the API can say which endpoint, rather than a bare 500."""


def chat_endpoint() -> str:
    """Where the chat model runs. Falls back to the proposer's endpoint rather than to nothing, so a machine
    already serving the proposer needs no extra configuration to hold a conversation."""
    # The operator's own local server was on a different port from this module's default, and the chat answered
    # with a bare 500 until someone found it. The proposer's endpoint setting is the second place to look.
    for var in ("PRAVRUDHI_CHAT_ENDPOINT", "PRAVRUDHI_PROPOSER_ENDPOINT"):
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()
    return DEFAULT_ENDPOINT


def system_prompt() -> str:
    """The assistant's standing instructions, naming the tools it has and the boundary it is held to anyway.

    The last paragraph is honest with the model about the fact that the boundary is enforced after it writes:
    a model told it will be checked mechanically has no incentive to guess, which makes stripped sentences
    rarer even though nothing depends on it complying.
    """
    lines = [
        "You are the assistant inside Pravrudhi, a recursive self-improvement engine for language models.",
        "",
        "You may call only these tools:",
    ]
    for tool in TOOL_SCHEMA:
        params = ", ".join(str(k) for k in dict(tool["parameters"])["properties"]) or "no arguments"
        lines.append(f"  {tool['name']}({params}) - {tool['description']}")
    lines += [
        "",
        "Answer with a single JSON object: {\"reply\": \"...\", \"tool_calls\": [{\"tool\": \"...\", "
        "\"args\": {...}}]}. Leave tool_calls empty once you have what you need.",
        "",
        "Every number in your reply must be one a tool returned in this same turn, written exactly as the",
        "tool wrote it - do not rescale, round, or convert a decimal into a percentage. Any numeral that no",
        "tool returned is deleted from your reply before the user sees it, along with the sentence around it,",
        "so a guessed number costs you the sentence and tells the user you guessed.",
    ]
    return "\n".join(lines)


def _objective_summary(root: Path, oid: str) -> dict[str, Any] | None:
    from pravrudhi.application.objectives import load_all, summary

    for obj in load_all(root):
        if obj.id == oid:
            return summary(root, obj)
    return None


def _evidence_document(root: Path, name: str) -> dict[str, Any]:
    """Read a rendered evidence document, refusing any name that could escape the evidence directory.

    The path check mirrors `api/server.py`'s: resolve first, then require the resolved parent to be the
    evidence directory itself, so a traversal that survives the name regex still cannot read another file.
    """
    if not _EVIDENCE_NAME_RE.fullmatch(name):
        return {"error": f"no evidence document named {name!r}"}
    base = (root / "docs" / "evidence").resolve()
    path = (base / f"{name}.md").resolve()
    if path.parent != base or not path.is_file():
        return {"error": f"no evidence document named {name!r}"}
    return {"name": name, "markdown": path.read_text()}


def _summarise(tool: str, args: dict[str, Any], result: dict[str, Any]) -> str:
    """A one-line account of what a tool returned, stating no quantity the result does not contain.

    Counts are the exception and are safe: they are computed here from the result itself, so a count in a
    summary is as replayed as the rows it counted, and `allowed_numbers` admits it for that reason.
    """
    if "error" in result:
        return str(result["error"])
    if tool == "candidates":
        rows = result.get("candidates") or []
        badges: dict[str, int] = {}
        for row in rows:
            badges[str(row.get("badge"))] = badges.get(str(row.get("badge")), 0) + 1
        return f"{len(rows)} candidates; " + ", ".join(f"{n} {b}" for b, n in sorted(badges.items()))
    if tool == "agent_trace":
        entries = result.get("entries") or []
        if not entries:
            return "no agent activity recorded yet"
        return f"{len(entries)} trace entries; the last is {entries[-1].get('summary')}"
    if tool == "routing_seats":
        seats = result.get("seats") or []
        down = [s for s in seats if not s.get("usable")]
        return (
            f"{len(seats)} seats, {len(seats) - len(down)} usable"
            + (f"; sitting out a limit: {', '.join(str(s.get('id')) for s in down)}" if down else "")
        )
    if tool == "nights":
        rows = result.get("nights") or []
        last = rows[-1] if rows else None
        if last is None:
            return "no nights on record yet"
        return (
            f"{len(rows)} nights; the last is night {last['night']} with {last['candidates']} candidates, "
            f"{last['pruned']} pruned, {len(last['promoted'])} promoted, {last['spent_gpu_h']} GPU-h spent"
        )
    if tool == "objectives":
        rows = list(result.get("objectives") or [])
        names = ", ".join(str(o.get("id")) for o in rows) or "none"
        return f"{len(rows)} objective(s): {names}"
    if tool == "objective_progress":
        rows = list(result.get("progress") or [])
        states = ", ".join(f"{p.get('benchmark')} {p.get('state')}" for p in rows) or "no benchmarks"
        return f"{result.get('objective')}: {states}"
    if tool == "objective_plan":
        return f"{result.get('objective')}: {len(list(result.get('steps') or []))} proposed step(s), none run"
    if tool in ("recipes", "tools"):
        rows = list(result.get(tool) or [])
        return f"{len(rows)} {tool} catalogued, {sum(1 for r in rows if r.get('available'))} available here"
    if tool == "memory_recall":
        rows = list(result.get("notes") or [])
        return f"{len(rows)} note(s) for {args.get('query') or 'the most recent'}"
    if tool == "memory_remember":
        return f"remembered: {result.get('text')}"
    if tool == "routing_report":
        rows = list(result.get("tiers") or [])
        return ", ".join(f"{r.get('tier')} -> {r.get('route') or r.get('error')}" for r in rows) or "no tiers"
    if tool == "evidence":
        return f"evidence document {result.get('name')}, {len(str(result.get('markdown') or ''))} characters"
    if tool == "appetite":
        return str(result.get("sentence") or "no appetite data")
    return tool


def dispatch(root: Path, store: MemoryStore, tool: str, args: dict[str, Any]) -> ToolInvocation:
    """Run one tool by name. Deterministic: the same workspace and arguments give the same result.

    An unknown name is refused rather than approximated. Before this was explicit, the failure mode was a
    model asking for `ledger()` or `benchmark()` and the loop either raising a 500 or, worse, guessing which
    real tool was meant - which would attribute a real replayed number to a question nobody asked.
    """
    if tool not in TOOL_NAMES:
        refusal = (
            f"refused the tool {tool!r}: the assistant may call only "
            f"{', '.join(sorted(TOOL_NAMES))}, and nothing else reaches this workspace"
        )
        return ToolInvocation(tool=tool, args=args, result={"error": refusal}, result_summary=refusal,
                              refusal=refusal)

    result: dict[str, Any]
    if tool == "candidates":
        from pravrudhi.application.demo_export import _candidates
        from pravrudhi_kernel.ledger.replay import replay

        result = {"candidates": _candidates(root, replay(root / "research" / "ledger.jsonl"))[-60:]}
    elif tool == "agent_trace":
        from pravrudhi.application import continuity

        result = {"entries": [e.to_dict() for e in continuity.entries(root, 60)]}
    elif tool == "routing_seats":
        from pravrudhi.application.roster import roster

        result = {"seats": [s.to_dict() for s in roster(root)]}
    elif tool == "nights":
        # The engine's whole subject was unreachable: asked what night 18 did, the assistant correctly reported
        # having no record, because no tool read the ledger's night rows, and the honesty pass then refused to
        # invent one — which made a missing tool look like a missing result.
        from pravrudhi.application.demo_export import _nights

        rows = _nights(root / "research" / "ledger.jsonl")
        result = {"nights": rows[-40:]}
    elif tool == "objectives":
        from pravrudhi.application.objectives import load_all, problems, summary

        result = {
            "objectives": [summary(root, o) for o in load_all(root)],
            "problems": [{"file": f, "reason": r} for f, r in problems(root)],
        }
    elif tool in ("objective_progress", "objective_plan"):
        oid = str(args.get("id") or "").strip()
        found = _objective_summary(root, oid)
        if found is None:
            result = {"error": f"no objective named {oid!r} in this workspace"}
        elif tool == "objective_progress":
            result = {"objective": oid, "progress": found["progress"]}
        else:
            from dataclasses import asdict as _asdict

            from pravrudhi.application.intent import compile_intent
            from pravrudhi.application.objectives import load_all
            from pravrudhi.application.recipes import installed, library

            obj = next(o for o in load_all(root) if o.id == oid)
            plan = compile_intent(obj, tuple(library()), installed_skills=frozenset(installed()))
            result = {**_asdict(plan), "objective": oid}
    elif tool == "recipes":
        from pravrudhi.application.recipes import availability as recipe_availability

        result = {"recipes": recipe_availability()}
    elif tool == "tools":
        from pravrudhi.application.tools import availability as tool_availability

        result = {"tools": tool_availability()}
    elif tool == "memory_recall":
        result = {"notes": [asdict(n) for n in store.recall(str(args.get("query") or ""), limit=5)]}
    elif tool == "memory_remember":
        text = str(args.get("text") or "").strip()
        try:
            result = asdict(store.remember(text, source="assistant"))
        except MemoryStoreError as exc:
            refusal = f"refused to remember {text!r}: {exc}"
            return ToolInvocation(tool=tool, args=args, result={"error": str(exc)}, result_summary=refusal,
                                  refusal=refusal)
    elif tool == "routing_report":
        from pravrudhi.application.routing import report

        result = {"tiers": report(root)}
    elif tool == "appetite":
        from pravrudhi.application import kshudha

        state = kshudha.current(root)
        result = {
            "drives": [d.to_dict() for d in state.drives],
            "appetite": state.to_dict(),
            "sentence": kshudha.sentence(state),
            "voice": kshudha.voice(state),
        }
    else:
        result = _evidence_document(root, str(args.get("name") or ""))

    return ToolInvocation(tool=tool, args=args, result=result, result_summary=_summarise(tool, args, result))


def _key(token: str) -> str:
    """A numeral's canonical form, so "0.6460" and "0.646" are one value and "49%" is not the value 49."""
    percent = token.endswith("%")
    return f"{float(token.rstrip('%')):g}{'%' if percent else ''}"


def numbers_in(text: str) -> set[str]:
    return {_key(m.group()) for m in _NUMBER_RE.finditer(text)}


def allowed_numbers(calls: tuple[ToolInvocation, ...]) -> frozenset[str]:
    """Every numeral this turn's tools actually produced.

    A refused call contributes nothing: its "result" is an explanation of the refusal, and a number appearing
    inside that sentence must not become licence to state it. Summaries do contribute, because this module
    computes them from the results themselves.
    """
    out: set[str] = set()
    for call in calls:
        if call.refusal:
            continue
        out |= numbers_in(json.dumps(call.result, default=str))
        out |= numbers_in(call.result_summary)
    return frozenset(out)


def enforce_honesty(reply: str, allowed: frozenset[str]) -> tuple[str, list[str]]:
    """Delete every sentence carrying a numeral no tool returned, and say which sentence went and why.

    The whole sentence goes, not just the digits. Excising "49%" from "the harness improved by 49%" leaves
    "the harness improved by", which still asserts an improvement the ledger may not contain; the sentence is
    the smallest unit that can be removed without leaving a claim behind.
    """
    kept: list[str] = []
    refusals: list[str] = []
    for chunk in _SENTENCE_RE.split(reply):
        sentence = chunk.strip()
        if not sentence:
            continue
        unsupported = sorted({m.group() for m in _NUMBER_RE.finditer(sentence) if _key(m.group()) not in allowed})
        if unsupported:
            refusals.append(
                f"removed {', '.join(unsupported)}: no tool call in this turn returned that number - {sentence!r}"
            )
            continue
        kept.append(sentence)
    return " ".join(kept), refusals


def _collect_citations(tool: str, node: Any, label: str, out: dict[int, str]) -> None:
    if isinstance(node, dict):
        here = label
        for key in _LABEL_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                here = value.strip()
                break
        seq = node.get("seq")
        if isinstance(seq, int) and not isinstance(seq, bool):
            out.setdefault(seq, f"{tool}: {here}" if here else tool)
        for value in node.values():
            _collect_citations(tool, value, here, out)
    elif isinstance(node, list):
        for value in node:
            _collect_citations(tool, value, label, out)


def citations_from(calls: tuple[ToolInvocation, ...]) -> tuple[dict[str, Any], ...]:
    """The ledger rows this turn's tools stood on, lowest sequence first.

    A `seq` anywhere in a result is a ledger row that admitted the number beside it, so the reply can be
    traced to the append-only record rather than to the conversation.
    """
    found: dict[int, str] = {}
    for call in calls:
        if call.refusal:
            continue
        _collect_citations(call.tool, call.result, "", found)
    return tuple({"seq": seq, "what": what} for seq, what in sorted(found.items()))


def _history(store: MemoryStore, thread_id: str) -> list[dict[str, str]]:
    try:
        thread = store.thread(thread_id)
    except MemoryStoreError:
        return []
    return [{"role": t.role, "content": t.content} for t in thread.turns]


def _tool_results_message(calls: list[ToolInvocation], limit: int = 8000) -> dict[str, str]:
    """Tool results as a user-role message, because llama.cpp has no tool role.

    The proposer's endpoint is any OpenAI-compatible server, and the smallest of those implement chat
    completions and nothing else. Feeding results back as prose keeps the loop working on every endpoint the
    engine already supports rather than only on the ones with a native tools API.
    """
    body = json.dumps([{"tool": c.tool, "args": c.args, "result": c.result} for c in calls], default=str)
    return {"role": "user", "content": "tool results:\n" + body[:limit]}


def _mentioned_objective_ids(root: Path, message: str) -> list[str]:
    """Objective ids the message names outright, matched whole-word against the workspace's own catalogue.

    A substring match would let "nyaya" inside some longer track name silently stand in for an objective id
    nobody typed; the word boundary keeps the match to what the user actually wrote.
    """
    from pravrudhi.application.objectives import load_all

    return [o.id for o in load_all(root) if re.search(rf"\b{re.escape(o.id)}\b", message, re.IGNORECASE)]


def _mentions(message: str, keywords: tuple[str, ...]) -> bool:
    lowered = message.lower()
    return any(keyword in lowered for keyword in keywords)


def _grounding_calls(root: Path, store: MemoryStore, message: str) -> list[ToolInvocation]:
    """Tool calls the message's own wording already justifies, run before the model is asked anything.

    This is what turns a model's silence into an answer rather than a guess: the results reach the model as
    an ordinary tool-results message, so even a model that never emits a tool call itself has the ledger row
    in front of it when it writes its reply.
    """
    named = _mentioned_objective_ids(root, message)
    wanted: list[tuple[str, dict[str, Any]]] = [("objective_progress", {"id": oid}) for oid in named]
    if not named and _mentions(message, _GROUNDING_KEYWORDS["objectives"]):
        wanted.append(("objectives", {}))
    if named and _mentions(message, _GROUNDING_KEYWORDS["objective_plan"]):
        wanted += [("objective_plan", {"id": oid}) for oid in named]
    for tool in ("recipes", "tools", "routing_report", "appetite"):
        if _mentions(message, _GROUNDING_KEYWORDS[tool]):
            wanted.append((tool, {}))
    return [replace(dispatch(root, store, tool, args), grounding=True) for tool, args in wanted]


def _run_turn(
    root: Path,
    message: str,
    *,
    thread_id: str | None,
    complete: Complete,
    store: MemoryStore,
) -> Iterator[dict[str, Any]]:
    """The turn engine shared by `converse` and `converse_stream`: grounding, then the tool-calling rounds.

    A live caller wants to know about each tool as it is called and as it returns; a caller that only wants
    the finished turn can drain this generator and read the last item. Either way the logic that decides which
    tools run and what the model is shown is written exactly once, because a chat surface with two turn engines
    that drift is a chat surface where one of them silently stops enforcing the honesty rule.

    Nothing here is persisted. The user's own message is not written until `_finish_turn` knows the model
    actually answered - a request whose endpoint never responds must leave no half-written turn behind for the
    thread to carry forward, or a client retrying the same message would find it already there without a reply.
    """
    tid = (thread_id or "").strip() or f"t-{uuid.uuid4().hex[:12]}"
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt()}]
    messages += _history(store, tid)
    messages.append({"role": "user", "content": message})

    grounded = _grounding_calls(root, store, message)
    for call in grounded:
        yield {"type": "tool", "phase": "called", "tool": call.tool, "args": dict(call.args)}
        yield {"type": "tool", "phase": "returned", "tool": call.tool, "args": dict(call.args),
               "result_summary": call.result_summary}
    calls: list[ToolInvocation] = list(grounded)
    if grounded:
        messages.append(_tool_results_message(grounded))

    draft = ""
    for _round in range(MAX_TOOL_ROUNDS):
        answer = complete(list(messages), list(TOOL_SCHEMA))
        draft = str(answer.get("content") or "")
        requested = [r for r in (answer.get("tool_calls") or []) if isinstance(r, dict)]
        if not requested:
            break
        this_round: list[ToolInvocation] = []
        for r in requested:
            tool = str(r.get("tool") or r.get("name") or "")
            args = dict(r.get("args") or r.get("arguments") or {})
            yield {"type": "tool", "phase": "called", "tool": tool, "args": args}
            call = dispatch(root, store, tool, args)
            yield {"type": "tool", "phase": "returned", "tool": tool, "args": args,
                   "result_summary": call.result_summary}
            this_round.append(call)
        calls += this_round
        asked = json.dumps({"tool_calls": [c.to_dict() for c in this_round]})
        messages.append({"role": "assistant", "content": asked})
        messages.append(_tool_results_message(this_round))

    yield {"type": "_final", "thread_id": tid, "draft": draft, "calls": tuple(calls), "grounded": bool(grounded)}


def _drain_final(events: Iterator[dict[str, Any]]) -> dict[str, Any]:
    final: dict[str, Any] | None = None
    for ev in events:
        if ev["type"] == "_final":
            final = ev
    assert final is not None, "_run_turn always ends with a _final event"
    return final


def _finish_turn(
    memory_store: MemoryStore, message: str, final: dict[str, Any],
) -> tuple[str, str, tuple[ToolInvocation, ...], tuple[dict[str, Any], ...], list[str]]:
    """Run the honesty pass over a finished turn and persist both halves of it.

    The user's turn is written here, beside the assistant's, rather than up front in `_run_turn` - so a turn
    that reaches this point has an answer, and a turn that does not reach this point (the model's endpoint
    never responded) leaves the thread exactly as a caller who retries the same message expects to find it.
    """
    tid, draft, made, grounded = final["thread_id"], final["draft"], final["calls"], final["grounded"]
    memory_store.append_turn(tid, "user", message)
    reply, refusals = enforce_honesty(draft, allowed_numbers(made))
    refusals = [c.refusal for c in made if c.refusal] + refusals
    citations = citations_from(made)
    if grounded and not citations:
        # The message's own wording justified a ledger lookup and one ran, but nothing in it was citable -
        # whatever the model wrote instead cannot be verified this turn, so it is replaced outright rather
        # than trusted just because it happens to contain no numeral for the honesty pass to catch.
        reply = _GROUNDING_FALLBACK_REPLY
    memory_store.append_turn(tid, "assistant", reply, meta={
        "citations": [dict(c) for c in citations],
        "refusals": list(refusals),
        "tool_calls": [c.to_dict() for c in made],
    })
    return tid, reply, made, citations, refusals


def converse(
    root: Path,
    message: str,
    *,
    thread_id: str | None = None,
    user: User | None = None,
    complete: Complete | None = None,
    store: MemoryStore | None = None,
) -> ChatOutcome:
    """One turn: let the model ask for tools, run them, then strip whatever it made up.

    Turns are persisted through `store_for`, so a hosted user's conversation follows their account and a
    local engine's stays on disk, without this module knowing which happened.
    """
    root = Path(root)
    memory_store = store if store is not None else store_for(root, user)
    model = complete if complete is not None else default_complete()

    final = _drain_final(_run_turn(root, message, thread_id=thread_id, complete=model, store=memory_store))
    tid, reply, made, citations, refusals = _finish_turn(memory_store, message, final)
    return ChatOutcome(
        thread_id=tid,
        reply=reply,
        citations=citations,
        tool_calls=made,
        refusals=tuple(refusals),
    )


def converse_stream(
    root: Path,
    message: str,
    *,
    thread_id: str | None = None,
    user: User | None = None,
    complete: Complete | None = None,
    store: MemoryStore | None = None,
) -> Iterator[dict[str, Any]]:
    """The same turn as `converse`, delivered as it happens.

    A `tool` event is yielded when the assistant calls a tool and again when it returns, in that order, for
    every tool this turn runs - the grounding pre-pass first, then whatever the model itself asks for. Only
    once every one of those calls has resolved does this function know what the model's draft is allowed to
    say, so the reply is put through the same `enforce_honesty` pass `converse` uses before any of it is cut
    into `token` events. That ordering is the whole of the grounding guarantee for this route: a number the
    model wrote before its own tool call resolved is judged, like every other number, only after the call it
    depended on has already returned - it cannot reach a `token` event any sooner than `converse`'s caller
    could have read it in the finished reply.

    Raises whatever `complete` raises (typically `ChatEndpointUnreachable`) mid-generator if the model never
    answers. `_run_turn` defers persisting the user's message until `_finish_turn` runs, so a turn that fails
    here leaves nothing behind for a caller to duplicate by retrying the same message through `converse`.
    """
    root = Path(root)
    memory_store = store if store is not None else store_for(root, user)
    model = complete if complete is not None else default_complete()

    final: dict[str, Any] | None = None
    for ev in _run_turn(root, message, thread_id=thread_id, complete=model, store=memory_store):
        if ev["type"] == "_final":
            final = ev
            break
        yield ev
    assert final is not None, "_run_turn always ends with a _final event"

    tid, reply, made, citations, refusals = _finish_turn(memory_store, message, final)
    for chunk in _TOKEN_CHUNK_RE.findall(reply):
        yield {"type": "token", "text": chunk}
    for c in citations:
        yield {"type": "citation", **dict(c)}
    yield {
        "type": "done",
        "thread_id": tid,
        "reply": reply,
        "citations": [dict(c) for c in citations],
        "tool_calls": [c.to_dict() for c in made],
        "refusals": list(refusals),
    }


_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "tool_calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"tool": {"type": "string"}, "args": {"type": "object"}},
                "required": ["tool"],
            },
        },
    },
    "required": ["reply"],
}


def default_complete(endpoint: str = "", model: str = "local") -> Complete:
    """A `Complete` over any OpenAI-compatible endpoint, constrained to the draft schema.

    The schema is enforced by the sampler rather than requested in prose, because a model that answers a
    tool-calling prompt with an apology produces a turn in which no tool ran and every number is therefore
    stripped - a correct outcome that looks like a broken engine.
    """
    from pravrudhi.models.openai_compat import ChatClient

    # A configurable endpoint that could not carry a credential could only ever be an unauthenticated local one,
    # which tied the chat surface — and so the Telegram bot, used precisely when away from the machine — to a GPU
    # server running at home. Both are read from the environment so no key is ever written into this repository.
    key = os.environ.get("PRAVRUDHI_CHAT_API_KEY", "").strip() or None
    named = os.environ.get("PRAVRUDHI_CHAT_MODEL", "").strip()
    client = ChatClient(endpoint or chat_endpoint(), model=named or model, api_key=key)

    def complete(messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            result = client.chat(messages, temperature=0.2, max_tokens=1024, json_schema=_DRAFT_SCHEMA)
        except (OSError, ConnectionError) as exc:
            raise ChatEndpointUnreachable(f"no chat model answered at {endpoint or chat_endpoint()}: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - the client wraps transport errors in its own types
            if "connect" in str(exc).lower() or "refused" in str(exc).lower() or "timeout" in str(exc).lower():
                raise ChatEndpointUnreachable(f"no chat model answered at {endpoint or chat_endpoint()}: {exc}") from exc
            raise
        try:
            data = json.loads(result.text)
        except json.JSONDecodeError:
            # An endpoint that ignored the schema still said something; the honesty pass will hold either way.
            return {"content": result.text, "tool_calls": []}
        return {"content": str(data.get("reply") or ""), "tool_calls": list(data.get("tool_calls") or [])}

    return complete
