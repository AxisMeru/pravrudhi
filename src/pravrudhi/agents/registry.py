"""Choosing an agent, and refusing the ones that would not actually work.

The registry answers one question honestly: which coding agents can run right now, on this machine, as configured?
An agent that is installed but signed out, or whose orchestrator needs a display server that is not present, is
reported as unavailable with the reason, rather than being handed a task that will fail halfway through a night.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pravrudhi.agents.alibaba_agent import AlibabaAgent
from pravrudhi.agents.cli_agents import ClaudeCodeAgent, CodexAgent
from pravrudhi.agents.hosted_agent import HostedAgent
from pravrudhi.agents.orca_agent import OrcaAgent
from pravrudhi.models import hosted


@dataclass(frozen=True)
class AgentStatus:
    name: str
    available: bool
    reason: str


def build_registry(root: Path, *, include_orca: bool = True) -> dict[str, Any]:
    agents: dict[str, Any] = {"claude-code": ClaudeCodeAgent(root), "codex": CodexAgent(root)}
    agents["opencode:alibaba"] = AlibabaAgent(root)
    if include_orca:
        for agent_id in ("claude", "codex", "local"):
            a = OrcaAgent(root, agent_id=agent_id)
            agents[a.name] = a
    if hosted.opted_in():
        agents["hosted"] = HostedAgent(root)
    return agents


def survey(root: Path, *, include_orca: bool = True) -> list[AgentStatus]:
    """One line per agent: usable now, or the specific reason it is not."""
    out: list[AgentStatus] = []
    for name, a in build_registry(root, include_orca=include_orca).items():
        if isinstance(a, CodexAgent):
            if not a.available():
                out.append(AgentStatus(name, False, "codex CLI not installed"))
            elif not a.logged_in():
                out.append(AgentStatus(name, False, "codex CLI installed but not signed in (run `codex login`)"))
            else:
                out.append(AgentStatus(name, True, "ready"))
        elif isinstance(a, OrcaAgent):
            if not a.runtime_ready():
                out.append(AgentStatus(name, False, "orca-ide runtime not reachable (needs a display server: xvfb)"))
            elif not a.available():
                need = {"claude": "claude", "codex": "codex", "local": "opencode"}[a.agent_id]
                out.append(AgentStatus(name, False, f"orca is up but {need} is not on PATH"))
            else:
                out.append(AgentStatus(name, True, "ready"))
        elif isinstance(a, AlibabaAgent):
            ok, why = a.status()
            out.append(AgentStatus(name, ok, why))
        elif isinstance(a, HostedAgent):
            ok, why = hosted.available()
            out.append(AgentStatus(name, ok, "ready" if ok else why))
        else:
            ok = a.available()
            out.append(AgentStatus(name, ok, "ready" if ok else "claude CLI not installed"))
    return out


def build_agent(root: Path, name: str, model: str | None = None) -> Any | None:
    """One agent by name and model, or None when it cannot run here.

    The swarm asks for an agent by route; returning None rather than raising lets a wave continue with the agents
    that are available and record the rest as unrouted, which is more useful than failing the whole wave.
    """
    if name == "codex":
        a: Any = CodexAgent(root, model=model)
        return a if a.available() and a.logged_in() else None
    if name == "claude-code":
        a = ClaudeCodeAgent(root, model=model)
        return a if a.available() else None
    if name.startswith("orca:"):
        a = OrcaAgent(root, agent_id=name.split(":", 1)[1], model=model)
        return a if a.available() else None
    if name == "opencode:alibaba":
        a = AlibabaAgent(root, model=model) if model else AlibabaAgent(root)
        return a if a.available() else None
    if name == "hosted":
        a = HostedAgent(root, model=model) if model else HostedAgent(root)
        return a if a.available() else None
    return None


__all__ = ["AgentStatus", "build_registry", "survey", "build_agent"]
