"""Let the operator talk to this engine through the model network, when no other model is available.

The chat surface pointed at a single OpenAI-compatible endpoint read from an environment variable, defaulting to
a local server. On a machine where that server is not running the whole conversation answered 503, which is what
it did here. Meanwhile the engine already knows about several working hosted models, keeps a measured record of
each, and knows which are spent and when they return — and none of that reached the one surface the operator
would need if the session driving this work went away.

So the chat draws on the same routing table the swarm does. It walks the permitted routes at a tier in order,
uses the first that is reachable and not cooling, and moves on when one reports a usage limit rather than
returning an error to the person typing.

Only routes that are an HTTP endpoint can serve a conversation. `claude-code` and `codex` are command-line
agents that drive their own loops and cannot answer a single completion, so they are skipped here — which is the
point: this exists for when those are exactly what is unavailable.

No credential is passed on a command line, held in a log, or returned to a caller. Keys come from the same
owner-only files the agents use and go straight into the request.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pravrudhi.application import availability, routing

# Which routes can answer a completion, and where each one's key comes from. A route whose agent is not here is
# a command-line tool rather than an endpoint, and is skipped rather than treated as broken.
ENDPOINT_AGENTS = ("opencode:alibaba-plan", "opencode:alibaba", "hosted")


class NoModelAvailable(RuntimeError):
    """Every route that could answer is unreachable, unconfigured, or spent."""


def _endpoint_for(agent: str) -> tuple[str, str] | None:
    """The base URL and credential for one route's agent, or None when it cannot serve a conversation."""
    from pravrudhi.agents.alibaba_agent import credential
    from pravrudhi.application.credentials import PROVIDERS

    if agent in ("opencode:alibaba", "opencode:alibaba-plan"):
        provider_id = agent.split(":", 1)[1]
        try:
            secret = credential(provider_id)
        except (OSError, ValueError):
            return None
        return PROVIDERS[provider_id].base_url, secret.reveal()
    if agent == "hosted":
        try:
            secret = credential("alibaba")
        except (OSError, ValueError):
            return None
        return PROVIDERS["alibaba"].base_url, secret.reveal()
    return None


def usable_routes(root: Path, tier: str = "standard") -> list[routing.Route]:
    """Routes at this tier that can answer a completion and are not cooling, in the table's own order."""
    table = routing.load_table()
    cooling = availability.cooling(root)
    return [
        route
        for route in table.permitted(tier)
        if route.agent in ENDPOINT_AGENTS and route.agent not in cooling
    ]


# The shape the conversation expects back. A local llama server has this compiled into its sampler, which a
# hosted endpoint cannot do, so the shape is asked for in words and the parser tolerates a model that answers in
# prose anyway.
_SHAPE = (
    'Answer as one JSON object and nothing else: {"reply": "<what you want to say>", '
    '"tool_calls": [{"tool": "<name>", "args": {}}]}. Leave tool_calls empty when you need no tool. '
    "Put your whole answer in reply; do not write prose outside the object."
)


def network_complete(
    root: Path, *, tier: str = "standard"
) -> Callable[[list[dict[str, str]], list[dict[str, Any]]], dict[str, Any]]:
    """A `Complete` that answers from whichever model in the network is available.

    A route reporting a usage limit is cooled and the next is tried, exactly as a dispatched task would be. The
    person typing sees an answer rather than an error, which is the whole reason this exists.
    """
    from pravrudhi.models.openai_compat import ChatClient

    def complete(messages: list[dict[str, str]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        routes = usable_routes(root, tier)
        if not routes:
            raise NoModelAvailable(
                "no model in the network can answer right now: every endpoint route is unconfigured or cooling"
            )
        last = ""
        for route in routes:
            endpoint = _endpoint_for(route.agent)
            if endpoint is None:
                continue
            base_url, key = endpoint
            client = ChatClient(base_url, model=route.model, api_key=key, timeout_s=180, thinking=None)
            try:
                result = client.chat(
                    [*messages, {"role": "system", "content": _SHAPE}],
                    temperature=0.2, max_tokens=1024, json_mode=True,
                )
            except Exception as exc:  # noqa: BLE001 (a route that fails is a reason to try the next one)
                text = str(exc)
                if availability.classify(route.agent, text, 1) == "limited":
                    availability.mark_limited(root, route.agent, until=availability.reset_at(text))
                last = text[-200:]
                continue
            return _parse(result.text)
        raise NoModelAvailable(f"no model in the network answered; the last said: {last or 'nothing'}")

    return complete


def _parse(text: str) -> dict[str, Any]:
    """The reply and any tool calls it asked for, tolerating a model that answered in prose.

    A model that ignores the requested shape still said something, and the honesty pass downstream strips any
    number no tool returned, so prose is safe to pass through rather than an error to raise.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"content": text, "tool_calls": []}
    if not isinstance(data, dict):
        return {"content": text, "tool_calls": []}
    # A model that returned an object but named the field something else still said something. Falling through
    # to the raw text keeps that answer rather than handing the caller an empty reply, which reads as a broken
    # engine when the model was merely inconsistent.
    said = str(data.get("reply") or data.get("content") or data.get("answer") or "").strip()
    return {"content": said or text, "tool_calls": list(data.get("tool_calls") or [])}


__all__ = ["ENDPOINT_AGENTS", "NoModelAvailable", "network_complete", "usable_routes"]
