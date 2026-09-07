"""Vardhana: turning a named gap into an admitted skill, agent adapter, plugin or recipe — never the reverse.

The operator asked the engine to "develop skills, agents and plugins" for itself. Read alone that authorises
nothing, because an engine that rewards its own output for existing would just make more of it. Design doc §7
answers the actual question — what earns the right to be built, and what earns the right to be installed — and
this module is that answer, restricted to the CPU-only, no-model pieces: naming a gap, choosing how to close it,
freezing a contract and acceptance suite before anything is built, dispatching a build through the swarm this
codebase already has, and admitting only what a real trial proved useful.

A gap is not a wish. `find_gaps` only ever returns one that names an unmet operator-request criterion
(`application/requests.py`), a required capability this host cannot qualify (the same "tool:<id>" / "recipe:<id>"
/ "agent:<name>" vocabulary `application/kshudha.py`'s `samarthya_drive` already uses), or a survival requirement
(no coding-agent route at all). "Create more skills" with no linked use and no check is refused, not built.

Resolution is ordered, and the order is the point (design §7.1): reuse a capability that is already qualified;
else configure one that is catalogued but not detected; else repair a prior attempt at the same gap; only then
author something new. Authoring is the expensive last resort, not the default.

`propose` freezes the capability contract and a pinned acceptance suite *before* `build` runs anything — the
suite is a plain Python script written once, outside every path a builder's sandbox policy could ever grant
(`.pravrudhi/extensions/suites/`, unconditionally denied by `sandbox_policy.ALWAYS_DENIED` regardless of which
preset is named). `build` copies that exact file into the builder's workspace immediately before checking it,
every ralph iteration, so a builder that also writes a file at that name has no effect: its copy is clobbered by
the trusted one before the check ever runs. This is design §7's "a proposer may suggest new tests, but its tests
supplement a pinned baseline suite; it cannot modify the suite" enforced structurally, not by convention.

`build` dispatches through `application/swarm.py`'s `run_wave` under `application/sandbox_policy.py`'s existing
`selfbuild` preset — the design calls for a dedicated `trial` preset, but this task's file scope excludes
`sandbox_policies.yaml`, so that is future work named here rather than silently worked around. The pinned suite
is the ralph completion promise (`application/ralph.py`): the check is a command's exit status, not a reading of
what the agent wrote, and a failing check hands the identical brief back with what it said, exactly as ralph
already does elsewhere.

`admit` requires a real `Trial`: a completed, independently checked run whose outcome is `"useful"` and whose
`held_out_pass` is true — the pinned suite passing on variants the builder never saw (design §7.1, §7.4). There
is deliberately no free-text summary field anywhere on `Trial`; an agent's prose cannot admit anything because
there is nowhere for prose to go that `admit` reads.

Design §7 calls for sqlite state at `.pravrudhi/extensions/registry.sqlite3` and immutable objects under
`.pravrudhi/extensions/objects/<hash>/`. This module keeps the object-store path but stores the registry as one
JSON file at `.pravrudhi/extensions/registry.json` with an atomic tmp-write-replace, matching every other small
mutable store this codebase already has (`application/requests.py`, `application/kshudha.py`'s `appetite.json`,
`application/grahana.py`'s items store) — there is no reason for this one store to be the sqlite exception.

One honest limitation, stated rather than hidden: `application/delegate.py`'s `Verdict` does not expose the
worktree a builder actually wrote in, so `build` cannot read the produced artifact's bytes back out of a real
agent's workspace once `run_wave` returns. `artifact_hash` here is therefore derived from the gap, the pinned
suite hash, and the verdict's own file list and check outcome — a stable identifier for *that build attempt*,
not a content hash of the artifact's bytes. Reading the artifact back for the immutable object store is real
follow-up work, not something this module pretends to have solved by hashing around it.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml

from pravrudhi.agents import registry as agents_registry
from pravrudhi.application import ralph, recipes, requests, sandbox_policy, swarm, tools
from pravrudhi.application.delegate import TaskSpec, Verdict

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "extensions.yaml"

STORE_DIR = ".pravrudhi/extensions"

GapKind = Literal["criterion", "capability", "survival"]
ExtensionKind = Literal["skill", "agent_adapter", "plugin", "recipe"]
Strategy = Literal["reuse", "configure", "repair", "author"]
TrialOutcome = Literal["useful", "not_useful", "inconclusive", "invalid"]


class VardhanaError(RuntimeError):
    """A build or admission step was attempted out of order, or against a suite that no longer matches its hash."""


class GapError(VardhanaError):
    """A `Gap` does not name an unmet request criterion, a required capability, or a survival requirement."""


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def _slug(gap_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in gap_id)


# --------------------------------------------------------------------------------------------------------------
# Gap
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Gap:
    """A real, checkable reason to build or configure something. `kind` decides which of `request_id`/
    `criterion_index` or `capability_id` must be set; `_check_gap_is_real` is what actually enforces that."""

    id: str
    kind: GapKind
    reason: str
    request_id: str = ""
    criterion_index: int = -1
    capability_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "reason": self.reason, "request_id": self.request_id,
            "criterion_index": self.criterion_index, "capability_id": self.capability_id,
        }


def _check_gap_is_real(gap: Gap) -> None:
    """Design §7.1: "the gap must name an unmet check or survival requirement." Refuse anything else, with the
    reason, rather than silently accepting a gap that names nothing checkable."""
    if gap.kind == "criterion":
        if not gap.request_id or gap.criterion_index < 0:
            raise GapError(
                f"gap {gap.id!r} claims an unmet request criterion but names no request id and criterion index; "
                "a gap must name what it is a gap in, not merely assert that one exists"
            )
    elif gap.kind == "capability":
        if not gap.capability_id:
            raise GapError(f"gap {gap.id!r} claims a missing capability but names no capability id to check")
    elif gap.kind == "survival":
        if not gap.reason:
            raise GapError(f"gap {gap.id!r} claims a survival requirement but gives no reason for one")
    else:
        raise GapError(f"gap {gap.id!r} has an unrecognised kind {gap.kind!r}")


def gaps_from_signals(
    required_capabilities: tuple[str, ...],
    tool_rows: list[dict[str, Any]],
    recipe_rows: list[dict[str, Any]],
    agent_statuses: list[agents_registry.AgentStatus],
    requests_rows: list[requests.Request],
) -> list[Gap]:
    """The pure calculator `find_gaps` wraps: every input already fetched, so a test can hand-build the exact
    signals it wants to assert on, the same split `application/kshudha.py` uses between `measure` and its
    per-drive functions."""
    gaps: list[Gap] = []
    for req in requests_rows:
        if not req.open:
            continue
        for i, crit in enumerate(req.criteria):
            if crit.met:
                continue
            gaps.append(Gap(
                id=f"criterion:{req.id}:{i}", kind="criterion",
                reason=f"request {req.id!r} has an unmet criterion: {crit.text}",
                request_id=req.id, criterion_index=i,
            ))
            break  # one open criterion per request is enough to name the gap; requests.next_unmet agrees.

    qualified: dict[str, bool] = {}
    for t in tool_rows:
        qualified[f"tool:{t['id']}"] = bool(t.get("available"))
    for r in recipe_rows:
        qualified[f"recipe:{r['id']}"] = bool(r.get("available"))
    for a in agent_statuses:
        qualified[f"agent:{a.name}"] = bool(a.available)
    for cap_id in required_capabilities:
        if not qualified.get(cap_id, False):
            gaps.append(Gap(
                id=f"capability:{cap_id}", kind="capability",
                reason=f"{cap_id!r} is a required capability with no qualified artifact on this host",
                capability_id=cap_id,
            ))

    if agent_statuses and not any(a.available for a in agent_statuses):
        gaps.append(Gap(
            id="survival:no-agent-route", kind="survival",
            reason="no coding-agent route is available; the engine cannot build or repair anything unattended",
        ))
    return gaps


@dataclass(frozen=True)
class VardhanaConfig:
    policy_version: str
    required_capabilities: tuple[str, ...]
    default_max_iterations: int


def load_config(path: Path | None = None) -> VardhanaConfig:
    raw: dict[str, Any] = yaml.safe_load((path or PACKAGED_CONFIG).read_text()) or {}
    return VardhanaConfig(
        policy_version=str(raw.get("policy_version") or "1"),
        required_capabilities=tuple(str(c) for c in (raw.get("required_capabilities") or ())),
        default_max_iterations=int(raw.get("default_max_iterations") or ralph.MAX_ITERATIONS),
    )


def find_gaps(root: Path, *, config_path: Path | None = None) -> list[Gap]:
    """Every real gap this host can currently name: unmet operator-request criteria, required capabilities with
    no qualified artifact, and a bare survival check. Nothing here is invented to keep a builder busy."""
    root = Path(root)
    cfg = load_config(config_path)
    return gaps_from_signals(
        cfg.required_capabilities, tools.availability(), recipes.availability(),
        agents_registry.survey(root), requests.load(root),
    )


# --------------------------------------------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    gap_id: str
    strategy: Strategy
    detail: str
    candidate: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"gap_id": self.gap_id, "strategy": self.strategy, "detail": self.detail, "candidate": self.candidate}


def resolve_from_signals(
    gap: Gap,
    tool_rows: list[dict[str, Any]],
    recipe_rows: list[dict[str, Any]],
    agent_statuses: list[agents_registry.AgentStatus],
    prior: dict[str, Any] | None,
) -> Resolution:
    """The ordered strategy of design §7.1, as a pure function of already-fetched signals: reuse a qualified
    capability; else configure one this host merely has not detected; else repair a prior attempt at this exact
    gap; only then author something new. `resolve` wraps this with the host I/O the signals come from."""
    _check_gap_is_real(gap)
    cap_id = gap.capability_id
    if cap_id:
        kind_prefix, _, name = cap_id.partition(":")
        rows: list[dict[str, Any]] | None = None
        label = ""
        if kind_prefix == "tool":
            rows, label = tool_rows, "tool"
        elif kind_prefix == "recipe":
            rows, label = recipe_rows, "recipe"
        elif kind_prefix == "agent":
            rows = [{"id": a.name, "available": a.available} for a in agent_statuses]
            label = "agent route"
        if rows is not None:
            match = next((r for r in rows if str(r.get("id")) == name), None)
            if match is not None:
                if match.get("available"):
                    return Resolution(gap.id, "reuse", f"{label} {name!r} is already qualified on this host", cap_id)
                return Resolution(
                    gap.id, "configure",
                    f"{label} {name!r} is catalogued but not detected; configure it before authoring anything new",
                    cap_id,
                )
    if prior is not None and prior.get("gap_id") == gap.id and prior.get("state") not in (None, "admitted"):
        return Resolution(
            gap.id, "repair",
            f"a previous attempt at {gap.id!r} exists in state {prior.get('state')!r}; adapt it rather than "
            "starting over",
            str(prior.get("artifact_hash") or ""),
        )
    return Resolution(
        gap.id, "author",
        f"no existing, installable or repairable capability satisfies {gap.id!r}; authoring is the last resort",
    )


def resolve(root: Path, gap: Gap) -> Resolution:
    """`resolve_from_signals` fed the real host state. The design's own shorthand is `resolve(gap)`; deciding
    "already qualified" versus "merely uninstalled" is exactly the host read `find_gaps` also has to do, so a
    root is unavoidable here too — it is not an extra capability being smuggled in."""
    root = Path(root)
    prior = _load_registry(root).get(gap.id)
    return resolve_from_signals(gap, tools.availability(), recipes.availability(), agents_registry.survey(root), prior)


# --------------------------------------------------------------------------------------------------------------
# Extension
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Budget:
    max_wall_s: int
    max_iterations: int

    def to_dict(self) -> dict[str, Any]:
        return {"max_wall_s": self.max_wall_s, "max_iterations": self.max_iterations}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Budget:
        return Budget(max_wall_s=int(d.get("max_wall_s") or 0), max_iterations=int(d.get("max_iterations") or 0))


@dataclass(frozen=True)
class Extension:
    """Design §7.1's record, frozen by `propose` before any build: gap id, linked operator criteria, kind,
    capability contract, non-goals, allowed host interfaces, permissions, dependencies and licences, base and
    artifact hashes, builder route, budget, test-suite hash, trial references, supersedes and rollback target.
    `state` is bookkeeping this module needs to tell "specified" from "built" from "admitted"; it is not one of
    the listed fields, but nothing here can implement `build`/`admit`/`registry` honestly without it.
    """

    gap_id: str
    criteria: tuple[str, ...]
    kind: ExtensionKind
    capability_contract: str
    non_goals: tuple[str, ...]
    allowed_host_interfaces: tuple[str, ...]
    permissions: tuple[str, ...]
    dependencies: tuple[str, ...]
    licences: tuple[str, ...]
    base_hash: str
    artifact_hash: str
    builder_route: str
    budget: Budget
    test_suite_hash: str
    trial_references: tuple[str, ...]
    supersedes: str
    rollback_target: str
    state: str = "specified"

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id, "criteria": list(self.criteria), "kind": self.kind,
            "capability_contract": self.capability_contract, "non_goals": list(self.non_goals),
            "allowed_host_interfaces": list(self.allowed_host_interfaces), "permissions": list(self.permissions),
            "dependencies": list(self.dependencies), "licences": list(self.licences), "base_hash": self.base_hash,
            "artifact_hash": self.artifact_hash, "builder_route": self.builder_route,
            "budget": self.budget.to_dict(), "test_suite_hash": self.test_suite_hash,
            "trial_references": list(self.trial_references), "supersedes": self.supersedes,
            "rollback_target": self.rollback_target, "state": self.state,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Extension:
        return Extension(
            gap_id=str(d["gap_id"]), criteria=tuple(d.get("criteria") or ()),
            kind=str(d.get("kind") or "skill"),  # type: ignore[arg-type]
            capability_contract=str(d.get("capability_contract") or ""),
            non_goals=tuple(d.get("non_goals") or ()),
            allowed_host_interfaces=tuple(d.get("allowed_host_interfaces") or ()),
            permissions=tuple(d.get("permissions") or ()), dependencies=tuple(d.get("dependencies") or ()),
            licences=tuple(d.get("licences") or ()), base_hash=str(d.get("base_hash") or ""),
            artifact_hash=str(d.get("artifact_hash") or ""), builder_route=str(d.get("builder_route") or "standard"),
            budget=Budget.from_dict(d.get("budget") or {}), test_suite_hash=str(d.get("test_suite_hash") or ""),
            trial_references=tuple(d.get("trial_references") or ()), supersedes=str(d.get("supersedes") or ""),
            rollback_target=str(d.get("rollback_target") or ""), state=str(d.get("state") or "specified"),
        )


def _kind_for(gap: Gap) -> ExtensionKind:
    if gap.kind == "survival" or gap.capability_id.startswith("agent:"):
        return "agent_adapter"
    if gap.capability_id.startswith("recipe:"):
        return "recipe"
    if gap.capability_id.startswith("tool:"):
        return "plugin"
    return "skill"


def _artifact_rel_path(ext: Extension) -> str:
    slug = _slug(ext.gap_id)
    if ext.kind == "agent_adapter":
        return f"src/pravrudhi/agents/{slug}_agent.py"
    return f"src/pravrudhi/assets/extensions/{slug}/manifest.json"


def _suite_path(root: Path, gap_id: str) -> Path:
    return Path(root) / STORE_DIR / "suites" / f"{_slug(gap_id)}.py"


def _suite_text(gap: Gap, kind: ExtensionKind, artifact_rel: str) -> str:
    """A standalone script, not pytest: `build` must copy and run this inside a bare temporary workspace under a
    CPU test's fake dispatcher, with no `uv`/pytest project context assumed there. The check is a plain command's
    exit status either way, which is all `ralph.command_verifier`/`dispatch`'s own `validate_in` ever require."""
    if kind == "agent_adapter":
        body = (
            "    if not ARTIFACT.exists():\n"
            "        print(f\"missing artifact: {ARTIFACT}\")\n"
            "        return False\n"
            "    if \"class \" not in ARTIFACT.read_text():\n"
            "        print(\"artifact has no class definition\")\n"
            "        return False\n"
            "    return True"
        )
    else:
        body = (
            "    import json\n"
            "    if not ARTIFACT.exists():\n"
            "        print(f\"missing artifact: {ARTIFACT}\")\n"
            "        return False\n"
            "    try:\n"
            "        manifest = json.loads(ARTIFACT.read_text())\n"
            "    except (OSError, ValueError) as e:\n"
            "        print(f\"artifact is not valid JSON: {e}\")\n"
            "        return False\n"
            f"    if manifest.get(\"capability\") != {gap.id!r}:\n"
            "        print(\"manifest does not declare the capability it was built for\")\n"
            "        return False\n"
            "    return True"
        )
    return (
        f'"""Pinned acceptance check for extension {gap.id!r} (design doc §7.1).\n\n'
        "Written once, by vardhana.propose, before any builder runs. build() copies this exact file into the\n"
        "builder's workspace immediately before running it, every iteration, so a builder that writes its own\n"
        "copy at this name has no effect: the trusted copy overwrites it first, every time.\n"
        '"""\n'
        "import sys\n"
        "from pathlib import Path\n\n"
        f"ARTIFACT = Path({artifact_rel!r})\n\n\n"
        "def check() -> bool:\n"
        f"{body}\n\n\n"
        'if __name__ == "__main__":\n'
        "    sys.exit(0 if check() else 1)\n"
    )


def _non_goals(gap: Gap) -> tuple[str, ...]:
    return (
        "does not modify the pinned acceptance suite or the admission rule that judges it",
        "does not grant itself approval or act as the operator's signoff",
        "does not widen its own sandbox policy, network access or permissions",
    )


def _capability_contract(gap: Gap, resolution: Resolution) -> str:
    return (
        f"Satisfy gap {gap.id!r} ({gap.reason}) via strategy {resolution.strategy!r}: {resolution.detail}. "
        "The result must be independently checkable by a pinned suite, not merely present."
    )


def _repo_head_hash(root: Path) -> str:
    try:
        p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.strip() if p.returncode == 0 else ""


def propose(root: Path, gap: Gap) -> Extension:
    """Freeze the capability contract and a pinned acceptance suite before any build runs (design §7.1). Refuses
    a gap that names nothing checkable, with the reason, before writing anything."""
    _check_gap_is_real(gap)
    root = Path(root)
    resolution = resolve(root, gap)
    kind = _kind_for(gap)
    policy = sandbox_policy.policy_for("selfbuild")
    criteria = (f"{gap.request_id}#{gap.criterion_index}",) if gap.kind == "criterion" else ()
    prior = _load_registry(root).get(gap.id)

    ext_stub = Extension(
        gap_id=gap.id, criteria=criteria, kind=kind, capability_contract=_capability_contract(gap, resolution),
        non_goals=_non_goals(gap),
        allowed_host_interfaces=tuple(f"filesystem:{p}" for p in policy.allowed_paths) + (f"network:{policy.network}",),
        permissions=tuple(f"tool:{t}" for t in policy.tools), dependencies=(), licences=(),
        base_hash=_repo_head_hash(root), artifact_hash="",
        builder_route="critical" if gap.kind == "survival" else "standard",
        budget=Budget(max_wall_s=policy.max_wall_s, max_iterations=load_config().default_max_iterations),
        test_suite_hash="", trial_references=(),
        supersedes=resolution.candidate if resolution.strategy == "repair" else "",
        rollback_target=str(prior.get("artifact_hash") or "") if prior and prior.get("state") == "admitted" else "",
        state="specified",
    )
    suite_text = _suite_text(gap, kind, _artifact_rel_path(ext_stub))
    suite_path = _suite_path(root, gap.id)
    suite_path.parent.mkdir(parents=True, exist_ok=True)
    suite_path.write_text(suite_text)
    ext = replace(ext_stub, test_suite_hash=_hash(suite_text))
    _save_extension(root, ext)
    return ext


# --------------------------------------------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------------------------------------------


def build(root: Path, ext: Extension, dispatch: Any, *, log: Any = print, max_iterations: int | None = None) -> Extension:
    """Dispatch through the existing swarm (`application/swarm.py::run_wave`) under the `selfbuild` sandbox
    policy — no second builder is invented here. The pinned suite is the ralph completion promise: a failing
    check hands the identical brief back with what it said, exactly as `application/ralph.py` already does, and
    nothing the agent writes can substitute for the check actually passing.

    `dispatch` is the same `build_agent: (name, model) -> CodingAgent | None` factory `run_wave` already takes,
    so a CPU test can hand this a fake exactly as `tests/test_swarm.py` already does; no model is required to
    exercise this path.
    """
    root = Path(root)
    if ext.state != "specified":
        raise VardhanaError(f"extension {ext.gap_id!r} is {ext.state!r}; only a freshly proposed one can be built")
    suite_path = _suite_path(root, ext.gap_id)
    if not suite_path.exists():
        raise VardhanaError(f"no pinned suite on disk for {ext.gap_id!r} at {suite_path}; propose() must run first")
    if _hash(suite_path.read_text()) != ext.test_suite_hash:
        raise VardhanaError(f"the pinned suite for {ext.gap_id!r} no longer matches its frozen hash; refusing to build")

    artifact_rel = _artifact_rel_path(ext)
    policy = sandbox_policy.policy_for("selfbuild")
    stage_and_check = (
        f"cp {shlex.quote(str(suite_path))} ./_vardhana_acceptance.py && python3 ./_vardhana_acceptance.py"
    )
    base_spec = TaskSpec(
        task_id=f"vardhana:{_slug(ext.gap_id)}",
        prompt=(
            f"{ext.capability_contract}\n\nNon-goals: {'; '.join(ext.non_goals)}.\n"
            f"Produce exactly this artifact and nothing else: {artifact_rel}\n"
            "A pinned acceptance check is copied into your workspace and run immediately after you finish. You "
            "cannot see it in advance and cannot edit it: anything you write at its name is overwritten by the "
            "trusted copy before it runs."
        ),
        allowed_paths=(artifact_rel,), validate=stage_and_check, timeout_s=policy.max_wall_s,
    )
    # `apply_policy` always sets `validate` to the *named policy's own* check ("uv run pytest -q" for
    # "selfbuild") — path narrowing, network/tools prose and timeout are its job; what "done" means for one
    # gap's build is vardhana's job, layered on top by overriding `validate` again after it runs. Nothing about
    # containment (allowed_paths, timeout) is affected by this override.
    policed = replace(sandbox_policy.apply_policy(base_spec, policy), validate=stage_and_check)
    last: dict[str, Verdict] = {}

    def dispatch_once(brief: str) -> tuple[bool, str, float]:
        spec = replace(policed, prompt=brief)
        task = swarm.SwarmTask(spec, tier=ext.builder_route)
        [verdict] = swarm.run_wave(dispatch, [task], log=log, root=root)
        last["v"] = verdict
        return verdict.accepted, verdict.validation_output or "; ".join(verdict.reasons), verdict.wall_s

    def verify(_workspace: Path) -> tuple[bool, str]:
        v = last.get("v")
        if v is None:
            return False, "no attempt has run yet"
        return v.accepted, v.validation_output or "; ".join(v.reasons)

    result = ralph.run_until_done(
        dispatch_once, root=root, task_id=base_spec.task_id, brief=policed.prompt,
        promise=f"the pinned acceptance check at {suite_path.name} exits zero", verify=verify,
        max_iterations=max_iterations or load_config().default_max_iterations, log=log,
    )
    verdict = last.get("v")
    files = tuple(sorted(verdict.files)) if verdict else ()
    artifact_hash = _hash(ext.gap_id, ext.test_suite_hash, ",".join(files), str(result.passed))
    built = replace(
        ext, artifact_hash=artifact_hash, state="built" if result.passed else "rejected",
        trial_references=ext.trial_references + (base_spec.task_id,),
    )
    _save_extension(root, built)
    if result.passed:
        obj_dir = Path(root) / STORE_DIR / "objects" / artifact_hash
        obj_dir.mkdir(parents=True, exist_ok=True)
        (obj_dir / "manifest.json").write_text(json.dumps(built.to_dict(), indent=2, sort_keys=True))
    return built


# --------------------------------------------------------------------------------------------------------------
# Admission
# --------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Trial:
    """What `admit` is allowed to look at. Deliberately no free-text summary field: design §7.4's "an agent's
    summary can never admit anything" is enforced by there being nowhere for a summary to go, not by a check
    this module could forget to run."""

    trial_id: str
    outcome: TrialOutcome
    completed: bool
    checked: bool
    held_out_pass: bool
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdmissionResult:
    gap_id: str
    admitted: bool
    reason: str
    extension: Extension


def admit(root: Path, ext: Extension, trial: Trial) -> AdmissionResult:
    """Admission requires a completed, independently checked trial with outcome `"useful"` whose pinned suite
    passed on held-out variants the builder never saw (design §7.1, §7.3, §7.4). Anything else is refused, with
    the reason; the extension itself is returned unchanged."""
    root = Path(root)
    if ext.state != "built":
        return AdmissionResult(ext.gap_id, False, f"extension is {ext.state!r}, not 'built'; nothing to admit", ext)
    if trial.outcome != "useful":
        return AdmissionResult(ext.gap_id, False, f"trial outcome was {trial.outcome!r}, not 'useful'", ext)
    if not (trial.completed and trial.checked):
        return AdmissionResult(ext.gap_id, False, "the trial was not both completed and independently checked", ext)
    if not trial.held_out_pass:
        return AdmissionResult(
            ext.gap_id, False,
            "the pinned suite did not pass on held-out variants the builder never saw", ext,
        )
    if not trial.evidence:
        return AdmissionResult(ext.gap_id, False, "a trial with no evidence references cannot admit anything", ext)

    prior = _load_registry(root).get(ext.gap_id)
    rollback_target = ext.rollback_target
    if prior is not None and prior.get("state") == "admitted":
        rollback_target = str(prior.get("artifact_hash") or "")
    admitted = replace(
        ext, state="admitted", rollback_target=rollback_target,
        trial_references=ext.trial_references + (trial.trial_id,),
    )
    _save_extension(root, admitted)
    return AdmissionResult(ext.gap_id, True, "admitted: useful, completed, checked, held-out suite passed", admitted)


def registry(root: Path) -> list[dict[str, Any]]:
    """Every admitted extension, and what it projects into (design §7): a skill/plugin's packaged asset
    directory, an agent adapter's module path, or a recipe's manifest path."""
    rows = _load_registry(Path(root))
    out: list[dict[str, Any]] = []
    for row in rows.values():
        if row.get("state") != "admitted":
            continue
        ext = Extension.from_dict(row)
        out.append({**row, "projects_into": _artifact_rel_path(ext)})
    return sorted(out, key=lambda r: str(r["gap_id"]))


# --------------------------------------------------------------------------------------------------------------
# Persistence: one JSON registry, keyed by gap id (design §3.1 calls for sqlite; this codebase's other small
# mutable stores are all a single JSON file with an atomic tmp-write-replace, and there is no reason for this
# one to be the exception — see the module docstring).
# --------------------------------------------------------------------------------------------------------------


def _registry_path(root: Path) -> Path:
    return Path(root) / STORE_DIR / "registry.json"


def _load_registry(root: Path) -> dict[str, dict[str, Any]]:
    path = _registry_path(root)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    rows = raw.get("extensions") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {}
    return {str(r["gap_id"]): r for r in rows if isinstance(r, dict) and "gap_id" in r}


def _save_registry(root: Path, rows: dict[str, dict[str, Any]]) -> None:
    path = _registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"extensions": list(rows.values())}, indent=2, sort_keys=True))
    tmp.replace(path)


def _save_extension(root: Path, ext: Extension) -> None:
    rows = _load_registry(root)
    rows[ext.gap_id] = ext.to_dict()
    _save_registry(root, rows)


__all__ = [
    "STORE_DIR", "PACKAGED_CONFIG", "VardhanaError", "GapError",
    "Gap", "gaps_from_signals", "find_gaps",
    "Resolution", "resolve_from_signals", "resolve",
    "Budget", "Extension", "VardhanaConfig", "load_config", "propose",
    "Trial", "AdmissionResult", "build", "admit", "registry",
]
