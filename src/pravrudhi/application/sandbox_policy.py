"""A sandboxed subagent's policy was assembled by hand, once, at the one call site that needed it.

`subagents.tasks_from_plan` builds a `TaskSpec` per step by writing out an `allowed_paths` tuple and a `validate`
shell command inline; that was the whole of what "this task runs under a policy" meant. NemoClaw's OpenShell
sandboxes make the same idea -- an explicit, named policy of what a sandbox may touch and call -- a declared
object instead of scattered call-site logic: a policy names allowed and denied paths, a network posture, a set
of permitted tools, and a wall-clock budget, and a sandbox is handed one of these by name rather than having its
scope re-derived every time it is dispatched.

This module is that vocabulary for Pravrudhi, borrowed rather than copied: `Policy` is the declared object,
`sandbox_policies.yaml` is where the presets are named, and `apply_policy` is how a `TaskSpec` is narrowed to
fit one before dispatch. Nothing here dispatches a task or edits `application/subagents.py`; the wiring between
a proposal's plan and a named policy is a separate change.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.agents.base import SCRATCH_DIRNAME
from pravrudhi.application.delegate import TaskSpec

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "sandbox_policies.yaml"

# Merged into every policy's denied_paths regardless of what sandbox_policies.yaml declares, so a missing or
# mistyped entry in the config can never reopen what a subagent must never write to.
ALWAYS_DENIED: tuple[str, ...] = ("pravrudhi_kernel/**", "research/**", "gates/**", ".pravrudhi/**")

_NETWORK_LEVELS = ("none", "provider-only", "open")


class SandboxPolicyError(ValueError):
    """A policy that names an unknown network level, or a request for a policy id that is not declared."""


@dataclass(frozen=True)
class Policy:
    id: str
    allowed_paths: tuple[str, ...]
    denied_paths: tuple[str, ...]
    network: str
    tools: tuple[str, ...]
    max_wall_s: int
    validate: str

    def __post_init__(self) -> None:
        if self.network not in _NETWORK_LEVELS:
            raise SandboxPolicyError(f"policy {self.id!r}: network {self.network!r} not in {_NETWORK_LEVELS}")
        merged = tuple(dict.fromkeys((*self.denied_paths, *ALWAYS_DENIED)))
        object.__setattr__(self, "denied_paths", merged)


def _collides(a: str, b: str) -> bool:
    """Whether two glob patterns could ever name the same path, in either direction."""
    return a == b or fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)


def load_policies(path: Path | None = None) -> dict[str, Policy]:
    raw: dict[str, Any] = yaml.safe_load((path or PACKAGED_CONFIG).read_text())
    out: dict[str, Policy] = {}
    for policy_id, p in (raw.get("policies") or {}).items():
        out[str(policy_id)] = Policy(
            id=str(policy_id),
            allowed_paths=tuple(str(x) for x in (p.get("allowed_paths") or ())),
            denied_paths=tuple(str(x) for x in (p.get("denied_paths") or ())),
            network=str(p.get("network") or "none"),
            tools=tuple(str(x) for x in (p.get("tools") or ())),
            max_wall_s=int(p.get("max_wall_s") or 1800),
            validate=str(p.get("validate") or "uv run pytest -q"),
        )
    return out


def policy_for(kind: str, path: Path | None = None) -> Policy:
    policies = load_policies(path)
    try:
        return policies[kind]
    except KeyError:
        raise SandboxPolicyError(f"no sandbox policy named {kind!r}; declared: {sorted(policies)}") from None


def _narrow_paths(requested: tuple[str, ...], policy: Policy) -> tuple[str, ...]:
    """`requested`, kept only where a policy's allowed_paths cover it and its denied_paths do not.

    Denied always wins: a path is dropped even if it also collides with something in `allowed_paths`. An empty
    `allowed_paths` (the `review` policy) permits nothing, since nothing then collides with it.
    """
    return tuple(
        p
        for p in requested
        if any(_collides(p, ap) for ap in policy.allowed_paths)
        and not any(_collides(p, dp) for dp in policy.denied_paths)
    )


def _policy_block(policy: Policy, allowed_paths: tuple[str, ...]) -> str:
    # The scratch exception is named in the SAME sentence as the restriction it qualifies, not a separate one
    # later in the prompt -- an agent reading "you may write only to X" and, elsewhere, "use this other
    # directory" reads the two as contradictory and obeys the stricter line (a real dispatch prompt read this
    # way before this fix: 2026-09-13). It is a plain sentence here, not a comma-joined token in `writable`,
    # because nothing parses this specific line with a regex the way loop_agent.py/hosted_agent.py parse
    # delegate.dispatch's "You may create or modify ONLY these paths:" line.
    deliverable = ", ".join(allowed_paths) or "nowhere"
    denied = ", ".join(policy.denied_paths)
    tools = ", ".join(policy.tools) or "none declared"
    return (
        f"Sandbox policy {policy.id!r} governs this task. You may write only to: {deliverable} -- plus "
        f"`{SCRATCH_DIRNAME}/**`, which is scratch or temporary space only, not part of your deliverable, and "
        "is discarded after this run. "
        f"These paths are always off limits, regardless of anything above: {denied}. "
        f"Network access: {policy.network}. Permitted tools: {tools}. "
        f"Wall-clock budget: {policy.max_wall_s}s. "
        f"Your work is accepted only if `{policy.validate}` passes."
    )


def apply_policy(spec: TaskSpec, policy: Policy) -> TaskSpec:
    """`spec`, narrowed to what `policy` permits: allowed paths intersected, validate and timeout set from the
    policy, and a plain-English statement of the policy appended to the prompt."""
    allowed_paths = _narrow_paths(spec.allowed_paths, policy)
    prompt = f"{spec.prompt}\n\n{_policy_block(policy, allowed_paths)}"
    return TaskSpec(
        task_id=spec.task_id,
        prompt=prompt,
        allowed_paths=allowed_paths,
        validate=policy.validate,
        timeout_s=policy.max_wall_s,
    )


__all__ = [
    "ALWAYS_DENIED",
    "Policy",
    "SandboxPolicyError",
    "apply_policy",
    "load_policies",
    "policy_for",
]
