"""Export a recorded snapshot of this engine for the hosted site.

A visitor to the public site has no engine and, since browsers began blocking the loopback address space from
public origins, cannot be given one by pointing the page at their own machine. The site must therefore stand on
its own: it shows what this engine actually did, recorded, so that someone deciding whether to install it can see
the product working rather than an error message.

The snapshot is a static bundle of the same JSON shapes the live API serves, so every page renders from it
unchanged. It carries results, not machinery: what was improved, by how much, on which public benchmark, and the
sequence of a real run as it happened.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi import KERNEL_VERSION
from pravrudhi import __version__ as ENGINE_VERSION
from pravrudhi.agents.registry import survey as agent_survey
from pravrudhi.api.runs import models_listing
from pravrudhi.application import routing, selfbuild, subagents
from pravrudhi.application.external import external_rows
from pravrudhi.application.intent import compile_intent
from pravrudhi.application.objectives import load_all
from pravrudhi.application.objectives import problems as objective_problems
from pravrudhi.application.objectives import summary as objective_summary
from pravrudhi.application.policies import POLICIES
from pravrudhi.application.recipes import availability, library
from pravrudhi.application.recipes import installed as installed_skills
from pravrudhi.application.status import status
from pravrudhi.application.tools import availability as tool_availability
from pravrudhi_kernel.ledger import replay
from pravrudhi_kernel.ledger.verify import iter_events

MAX_EVENTS = 400
MAX_SWARM_RUNS = 20

# A whole-string absolute path, or one embedded in free text (an exception message, a agent's own words):
# matched so it can be collapsed to its relative-to-root or bare-filename form before the record leaves this
# machine, since a run record may otherwise carry this checkout's own filesystem layout.
_ABS_PATH = re.compile(r"/(?:[\w.\-]+/)+[\w.\-]+")


# The public site's page list, mirrored from app/frontend/src/components/Sidebar.tsx NAV (a TypeScript constant this
# Python module cannot import). Keep the two in sync by hand when a route is added or renamed there.
PAGES = ("/", "/objectives", "/chat", "/runs", "/search", "/models", "/machines", "/settings", "/install")


def _commit() -> str | None:
    """The engine's own short commit hash, or None when this tree is not a git checkout (e.g. a packaged install)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _exported_at() -> str:
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _nights(ledger: Path) -> list[dict[str, Any]]:
    starts: dict[tuple[int, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for ev in iter_events(ledger):
        p = ev.payload
        if ev.kind != "audit":
            continue
        track = str(p.get("track") or "lora")
        if p.get("kind") == "night_start":
            starts[(ev.night, track)] = {"policy": p.get("selection_policy"), "incumbent": p.get("incumbent")}
        elif p.get("kind") == "night_end":
            s = starts.get((ev.night, track), {})
            outcomes = p.get("outcomes") or {}
            out.append({
                "night": ev.night, "track": track, "selection_policy": s.get("policy"),
                "spent_gpu_h": round(float(p.get("spent_gpu_h") or 0.0), 3),
                "promoted": [c for c, o in outcomes.items() if o == "promoted"],
                "pruned": sum(1 for o in outcomes.values() if o == "pruned"),
                "candidates": len(outcomes),
            })
    return out


def _replay_run(ledger: Path, night: int, track: str) -> list[dict[str, Any]]:
    """One real night as the sequence of events the app's live view would have shown."""
    events: list[dict[str, Any]] = []
    inside = False
    for ev in iter_events(ledger):
        p = ev.payload
        is_track = (p.get("track") or ("harness" if ev.surface == "H3.prompt" else "lora")) == track
        if ev.kind == "audit" and p.get("kind") == "night_start" and ev.night == night and is_track:
            inside = True
            continue
        if not inside:
            continue
        if ev.kind == "audit" and p.get("kind") == "night_end" and ev.night == night:
            events.append({"type": "closed", "night": night, "status": "closed"})
            break
        if ev.kind == "propose" and ev.candidate_id:
            events.append({"type": "proposed_one", "candidate": ev.candidate_id,
                           "strategy": p.get("strategy"), "family": p.get("edit_family")})
        elif ev.kind == "observe" and p.get("arm") == "candidate" and ev.candidate_id:
            o = p["observed"]
            events.append({
                "type": "paired", "candidate": ev.candidate_id, "seed": p.get("seed_index", 0),
                "incumbent": round(float(o.get("value_ref") or 0.0), 4),
                "candidate_score": round(float(o["value"]), 4),
                "delta": round(float(o["delta_in"]), 4),
                "decision": ((p.get("stats") or {}).get("boundary")) or "",
                "n": int((p.get("stats") or {}).get("n") or 0),
            })
        elif ev.kind == "promote" and ev.candidate_id:
            events.append({"type": "promoted", "candidate": ev.candidate_id})
        elif ev.kind == "prune" and ev.candidate_id:
            events.append({"type": "pruned", "candidate": ev.candidate_id})
    return events[:MAX_EVENTS]


def _strip_paths(value: Any, root: Path) -> Any:
    """Replace this checkout's own absolute paths in a run record with their root-relative form, and collapse
    any other absolute path (from a different machine or worktree) to its bare filename."""
    if isinstance(value, dict):
        return {k: _strip_paths(v, root) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strip_paths(v, root) for v in value]
    if isinstance(value, str):
        root_str = str(root)
        value = value.replace(root_str + "/", "").replace(root_str, ".")
        return _ABS_PATH.sub(lambda m: Path(m.group(0)).name, value)
    return value


def _fleet(root: Path) -> list[dict[str, Any]]:
    """Every install this engine can see, with the version it runs and when it last checked.

    The public site could show what the engine had produced and nothing about how it keeps itself current, so a
    visitor had no way to see that two machines update themselves from the releases unattended.
    """
    from pravrudhi.application.fleet import known_installs

    try:
        return [i.to_dict() for i in known_installs(root)]
    except Exception:  # noqa: BLE001 (a fleet that cannot be read is an empty fleet, not a failed export)
        return []


def _health(root: Path) -> dict[str, Any]:
    """The engine's own survival state and every check behind it."""
    from pravrudhi.application.svasthya import assess

    try:
        h = assess(root)
        return {
            "state": str(getattr(h, "state", "")),
            "checks": [
                {"name": c.name, "ok": bool(c.ok), "detail": str(c.detail)[:300]}
                for c in getattr(h, "checks", [])
            ],
        }
    except Exception:  # noqa: BLE001
        return {"state": "", "checks": []}


def _update(root: Path) -> dict[str, Any]:
    """Which channel this engine follows and how it is configured to keep itself current."""
    from pravrudhi.application.update_apply import load_config
    from pravrudhi.application.updates import current as current_version

    try:
        cfg = load_config(root)
        return {
            "channel": cfg.channel, "auto_apply": cfg.auto_apply,
            "check_interval_min": cfg.check_interval_min, "keep_previous": cfg.keep_previous,
            "current": current_version(),
        }
    except Exception:  # noqa: BLE001
        return {}


def _requests(root: Path) -> dict[str, Any]:
    """The operator's own asks and how far each has got.

    The pages shipped before the snapshot carried their data, so every one of them rendered "this recording
    predates..." on the public site while working perfectly against a live engine. A page is not delivered until
    the recording feeds it.
    """
    from pravrudhi.application.requests import backlog

    return backlog(root)


def _inbox(root: Path) -> list[dict[str, Any]]:
    from pravrudhi.application.night import inbox_listing

    return inbox_listing(root)


def _candidates(root: Path, st: Any) -> list[dict[str, Any]]:
    return [{"id": cid, "badge": st.badges[cid], **c.model_dump()} for cid, c in st.candidates.items()]


def _observations(ledger: Path) -> list[dict[str, Any]]:
    """The `observe` rows the candidates page places on a night axis. Capped, newest kept."""
    rows = [
        {"seq": ev.seq, "night": ev.night, "candidate_id": ev.candidate_id,
         "payload": {"delta_in": (ev.payload or {}).get("delta_in")}}
        for ev in iter_events(ledger) if ev.kind == "observe"
    ]
    return rows[-2000:]


def _heartbeat(root: Path) -> list[dict[str, Any]]:
    """The last beats, newest first. The public page shows the engine working on its own, not only when driven."""
    from pravrudhi.application.heartbeat import history

    return [b.to_dict() for b in reversed(history(root, 20))]


def _swarm(root: Path) -> dict[str, Any]:
    """The same shapes the live `/api/swarm` route serves: agent availability, the routing table's live
    per-tier choice, and the last 20 runs of both the objective swarm and the self-build swarm, newest first."""
    return {
        "agents": [{"name": a.name, "available": a.available, "reason": a.reason} for a in agent_survey(root)],
        "routing": routing.report(root),
        "subagent_runs": [
            _strip_paths(asdict(r), root) for r in reversed(subagents.runs(root)[-MAX_SWARM_RUNS:])
        ],
        "selfbuild_runs": [
            _strip_paths(asdict(r), root) for r in reversed(selfbuild.runs(root)[-MAX_SWARM_RUNS:])
        ],
    }


def _parity(root: Path) -> dict[str, Any] | None:
    """The capability scoreboard, so the published site can show it without an engine behind it."""
    try:
        from pravrudhi.application.parity import report

        return report(root).model_dump()
    except Exception:  # noqa: BLE001 (a snapshot section that cannot be built is omitted, not fatal)
        return None


def _appetite(root: Path) -> dict[str, Any] | None:
    """What the engine wanted at export time, with every drive's operands rather than just its verdict."""
    try:
        from pravrudhi.application import kshudha

        state = kshudha.current(root)
        return {
            "drives": [d.to_dict() for d in state.drives],
            "appetite": state.to_dict(),
            "sentence": kshudha.sentence(state),
        }
    except Exception:  # noqa: BLE001
        return None


# How many worktrees the recording carries a full diff for. The viewer opens onto file contents, not a count,
# so a summary is not enough; a full diff per task is large, and a handful is what a demo needs.
DEMO_DIFF_TASKS = 6


def _diffs(root: Path) -> list[dict[str, Any]]:
    """Full diffs for the most recent dispatched tasks, which is what the viewer opens onto.

    The obvious thing to export is the summary list the task picker shows, and that was the first mistake here:
    its `files` field is a count, the viewer's is a list of file diffs, so every task opened to a page reporting
    it could not reach the engine. The recording has to carry what the reader will actually ask for.
    """
    try:
        from dataclasses import asdict

        from pravrudhi.application.diffs import recent as recent_diffs
        from pravrudhi.application.diffs import worktree_diff

        out: list[dict[str, Any]] = []
        for summary in recent_diffs(root, DEMO_DIFF_TASKS):
            try:
                diff = asdict(worktree_diff(root, summary.task_id))
            except Exception:  # noqa: BLE001 (a worktree that has since gone is skipped, not fatal)
                continue
            out.append({"task_id": summary.task_id, **diff})
        return out
    except Exception:  # noqa: BLE001
        return []


def _search(ledger: Path) -> dict[str, Any]:
    """Branching and selection pressure, so the recorded demo carries them without a live engine."""
    from pravrudhi.application.archive import ancestry_report, parent_map, selection_pressure

    parents = parent_map(ledger)
    pressure = selection_pressure(ledger)
    return {
        "ancestry": ancestry_report(parents).to_dict(),
        "pressure": [p.to_dict() for p in pressure],
        "binding_nights": sum(1 for p in pressure if p.binding),
        "declined": sum(p.declined for p in pressure),
    }


def build_demo(root: Path) -> dict[str, Any]:
    root = Path(root)
    ledger = root / "research" / "ledger.jsonl"
    st = replay(ledger)
    nights = _nights(ledger)
    featured = next((n for n in nights if n["promoted"]), nights[-1] if nights else None)
    runs: list[dict[str, Any]] = []
    for n in reversed(nights[-8:]):
        runs.append({
            "id": f"n{n['night']}-{n['track']}",
            "target": "model" if n["track"] == "lora" else "harness",
            "night": n["night"], "status": "finished", "policy": n["selection_policy"],
            "spent_gpu_h": n["spent_gpu_h"], "candidates": n["candidates"],
            "promoted": n["promoted"], "pruned": n["pruned"],
        })
    return {
        "recorded": True,
        "version": {
            "engine": ENGINE_VERSION,
            "kernel": KERNEL_VERSION,
            "commit": _commit(),
            "exported_at": _exported_at(),
        },
        "capabilities": {
            "tools": [{"id": t["id"], "kind": t["category"], "available": t["available"]} for t in tool_availability()],
            "agents": [{"name": a.name, "available": a.available} for a in agent_survey(root)],
            "policies": list(POLICIES),
            "recipes": len(library()),
            "pages": list(PAGES),
        },
        "engine": {"version": ENGINE_VERSION, "candidates": len(st.candidates)},
        "search": _search(ledger),
        "parity": _parity(root),
        "appetite": _appetite(root),
        "diffs": _diffs(root),
        "status": status(root),
        "models": models_listing(root),
        "external": external_rows(ledger),
        "nights": nights,
        "runs": runs,
        "objectives": {
            "objectives": [objective_summary(root, o) for o in load_all(root)],
            "problems": [{"file": f, "reason": r} for f, r in objective_problems(root)],
        },
        "recipes": availability(),
        "swarm": _swarm(root),
        "heartbeat": _heartbeat(root),
        "requests": _requests(root),
        "fleet": _fleet(root),
        "health": _health(root),
        "update": _update(root),
        "inbox": _inbox(root),
        "candidates": _candidates(root, st),
        "observations": _observations(ledger),
        "plans": {
            o.id: {
                "objective": o.id,
                **{k: v for k, v in asdict(
                    compile_intent(o, tuple(library()), installed_skills=frozenset(installed_skills()))
                ).items() if k != "objective"},
            }
            for o in load_all(root)
        },
        "featured_run": {
            "id": f"n{featured['night']}-{featured['track']}" if featured else "",
            "night": featured["night"] if featured else 0,
            "track": featured["track"] if featured else "lora",
            "events": _replay_run(ledger, featured["night"], featured["track"]) if featured else [],
        },
    }


def write_demo(root: Path, dest: Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(build_demo(root), indent=2, sort_keys=True, default=str) + "\n")
    return dest
