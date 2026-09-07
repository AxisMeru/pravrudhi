"""A shared medium so agents dispatched in the same swarm run can affect one another.

Before this module existed, `run_wave` gave every task its own agent and its own worktree, and nothing one agent
learned could reach another: two agents could rediscover the same dead end, or repeat the same mistake, because
neither could see what the other had already found. That is concurrency, not a swarm.

The blackboard is a small, append-only, per-run log. A task posts a `finding`, `warning`, `convention`, `blocker`
or `artifact` as it completes; the next task dispatched into the same run is handed a short digest of what is
already known, newest first, deduplicated, bounded in size, via `peer_briefing(digest(...))` prepended to its
brief. `competition` closes the loop the other direction: when two or more independent attempts at the same task
are compared, the winner and the reason it won are recorded, so a better approach can spread to later work
instead of each agent finding it alone. The mechanism mirrors `teamLearning.js`/`competitiveCoordinator.js` from
the openclaw swarm platform -- cross-agent lessons and a recorded, reasoned winner -- without borrowing their
code: this is JSON lines on disk, not a database, and it carries none of that platform's telegram/RAG/db plumbing.

Entries persist to `.pravrudhi/blackboard/<wave_id>.jsonl`, one file per run, the same append-only JSON-lines shape
`memory.py` uses for notes. A blackboard entry is operational scaffolding for the swarm, not evidence: like
`memory.py`'s notes, it must never carry a numeric result claim, because that number belongs to the ledger and a
copy kept here would go stale the moment the ledger is repaired. The same guard `memory.py` uses is reused here
rather than re-implemented, so the two stores cannot drift apart on what counts as a "numeric claim".
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.application.memory import _NUMERIC_CLAIM_RE

KINDS: tuple[str, ...] = ("finding", "warning", "convention", "blocker", "artifact")


class BlackboardError(ValueError):
    """An entry that names an unknown kind, carries no text, or restates a ledger number."""


@dataclass(frozen=True)
class BlackboardEntry:
    id: str
    wave_id: str
    author: str
    kind: str
    subject: str
    body: str
    refs: tuple[str, ...] = field(default_factory=tuple)
    ts: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "refs": list(self.refs)}


@dataclass(frozen=True)
class CompetitionResult:
    """Two or more independent attempts at one task, and which won.

    `winner` is the agent name of the accepted attempt chosen, or `None` when nothing was accepted -- a result
    worth recording too, since the reasons every attempt failed are exactly what the next agent should not repeat.
    """

    task_id: str
    winner: str | None
    reason: str
    entry: BlackboardEntry


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def board_dir(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "blackboard"


def _path(root: Path, wave_id: str) -> Path:
    return board_dir(root) / f"{wave_id}.jsonl"


def _check_no_numeric_claim(text: str) -> None:
    if _NUMERIC_CLAIM_RE.search(text):
        raise BlackboardError(
            f"refusing to post {text!r}: it reads as a numeric claim about a result, and evidence comes only "
            "from the ledger. A blackboard note is operational scaffolding for a swarm run, not evidence."
        )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every well-formed line in `path`. A corrupt line is skipped, not fatal -- the same rule `memory.py` follows
    for its own JSON-lines stores."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def post(
    root: Path, wave_id: str, *, author: str, kind: str, subject: str, body: str, refs: tuple[str, ...] = ()
) -> BlackboardEntry:
    """Append one entry to `wave_id`'s blackboard. Refuses an unknown kind, an empty body, or a numeric claim
    about a result in either the subject or the body -- the same guard `memory.remember` applies to a note."""
    if kind not in KINDS:
        raise BlackboardError(f"unknown blackboard kind {kind!r}; expected one of {', '.join(KINDS)}")
    subject = subject.strip()
    body = body.strip()
    if not body:
        raise BlackboardError("a blackboard entry with no body tells a peer nothing")
    _check_no_numeric_claim(subject)
    _check_no_numeric_claim(body)
    entry = BlackboardEntry(
        id=uuid.uuid4().hex[:12], wave_id=wave_id, author=author, kind=kind,
        subject=subject, body=body, refs=tuple(refs), ts=_now(),
    )
    path = _path(root, wave_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry.to_dict(), sort_keys=True) + "\n")
    return entry


def read(root: Path, wave_id: str, *, since: str | None = None, kinds: tuple[str, ...] | None = None) -> list[BlackboardEntry]:
    """Every entry posted to `wave_id`, oldest first. `since` keeps entries whose timestamp sorts strictly after
    it; `kinds` keeps only the named kinds."""
    entries: list[BlackboardEntry] = []
    for row in _read_jsonl(_path(root, wave_id)):
        try:
            entries.append(BlackboardEntry(
                id=row["id"], wave_id=row["wave_id"], author=row["author"], kind=row["kind"],
                subject=row["subject"], body=row["body"], refs=tuple(row.get("refs") or ()), ts=row.get("ts", ""),
            ))
        except (KeyError, TypeError):
            continue
    if since is not None:
        entries = [e for e in entries if e.ts > since]
    if kinds is not None:
        entries = [e for e in entries if e.kind in kinds]
    return entries


def digest(root: Path, wave_id: str, max_chars: int) -> str:
    """A short briefing of what peers have already found, warned about, or established as a convention.

    Newest first, deduplicated on (kind, subject, body) so a repeated finding costs nothing extra, and bounded by
    adding whole lines only while they still fit -- a digest that got cut mid-sentence would be worse than a
    shorter one, so the budget is respected by dropping the oldest lines that do not fit rather than truncating
    text.
    """
    entries = list(reversed(read(root, wave_id)))
    seen: set[tuple[str, str, str]] = set()
    lines: list[str] = []
    total = 0
    for e in entries:
        key = (e.kind, e.subject, e.body)
        if key in seen:
            continue
        seen.add(key)
        line = f"- [{e.kind}] {e.subject}: {e.body}"
        added = len(line) + (1 if lines else 0)
        if total + added > max_chars:
            break
        lines.append(line)
        total += added
    return "\n".join(lines)


def peer_briefing(digest: str) -> str:
    """`digest`, wrapped in a preamble a dispatcher can prepend to a brief -- the blackboard's equivalent of
    `swarm.SCOPE_PREAMBLE` -- so an agent starts knowing what its peers have already found, warned about, or
    established as a convention. Empty when there is nothing yet to report."""
    if not digest.strip():
        return ""
    return (
        "Your peers in this swarm run have already left notes on the shared blackboard. Do not repeat a finding "
        "or a mistake already recorded here; build on it instead.\n\n" + digest + "\n\n"
    )


def competition(root: Path, wave_id: str, task_id: str, verdicts: list[Any]) -> CompetitionResult:
    """Record two or more independent attempts at `task_id` and which won.

    An accepted attempt beats an unaccepted one; among several accepted attempts the one with fewest recorded
    reasons wins (nothing to note against it). When nothing was accepted, there is no winner, and the reason is
    simply why each attempt failed -- itself useful, since it is exactly what the next attempt should not repeat.
    This is the mechanism by which a better approach spreads: the result is posted as a `finding`, so it reaches
    every later task's `peer_briefing` in this run.
    """
    if len(verdicts) < 2:
        raise BlackboardError("a competition needs two or more independent attempts at the same task")

    accepted = [v for v in verdicts if v.accepted]
    if accepted:
        winner_v = min(accepted, key=lambda v: len(v.reasons))
        others = [v for v in verdicts if v is not winner_v]
        failed = [v for v in others if not v.accepted]
        if failed:
            failure_desc = "; ".join(f"{v.agent} ({'; '.join(v.reasons) or 'no reason recorded'})" for v in failed)
            reason = f"{winner_v.agent} was accepted; {failure_desc}"
        else:
            reason = (
                f"{winner_v.agent} was chosen over {', '.join(v.agent for v in others)}: all attempts were "
                "accepted, and this one had the fewest issues recorded against it"
            )
        winner = winner_v.agent
    else:
        winner = None
        reason = "no attempt was accepted: " + "; ".join(
            f"{v.agent} ({'; '.join(v.reasons) or 'no reason recorded'})" for v in verdicts
        )

    refs = tuple(dict.fromkeys([task_id, *(winner_v.files if accepted else [])]))
    entry = post(
        root, wave_id, author="blackboard:competition", kind="finding",
        subject=f"competition on {task_id}", body=reason, refs=refs,
    )
    return CompetitionResult(task_id=task_id, winner=winner, reason=reason, entry=entry)


__all__ = [
    "KINDS", "BlackboardError", "BlackboardEntry", "CompetitionResult",
    "board_dir", "post", "read", "digest", "peer_briefing", "competition",
]
