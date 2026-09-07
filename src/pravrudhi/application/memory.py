"""Memory: what belongs to the user, kept apart from what the ledger already owns.

Until this module existed, anything a user told the assistant about themselves — their preferred base model, a
constraint they mentioned once, the fact that this workspace is for a legal-domain LoRA rather than a generic one —
lived nowhere and was re-derived, or forgotten, at the start of every session. There was also no place to put a
conversation: chat had to be stateless or it had to borrow the ledger, and the ledger is the wrong place for it. The
kernel's evidence is append-only and immutable by design (`docs/blueprint/02-design/08-memory-and-context.md` §1-2;
`docs/superpowers/specs/2026-09-05-pravrudhi-multitenant-design.md` §5); a store for preferences, notes and chat
threads must never be confused with it, because if the ledger is later repaired (a signoff withdrawn, a candidate
resealed) a memory that duplicated a ledger number would now be false and would contradict the source of truth. This
module holds only what genuinely is not evidence: what the user asked to be remembered, what they set, and what they
said.

Three kinds live here, each a frozen dataclass with its own typed store: `Preference` (a key/value the user set, with
provenance of when and how); `MemoryNote` (a durable fact the user asked to remember, or the assistant recorded with
the user's assent — never a benchmark number, see `remember`); and `ChatTurn`/`ChatThread` (conversation history,
scoped to a thread). Storage is JSON lines under `<workspace>/.pravrudhi/memory/`, one file per kind, the same
per-workspace addressing `objectives.py` uses for objectives.

See docs/superpowers/specs/2026-09-05-pravrudhi-memory-design.md.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# A percentage figure ("49%") or a bare decimal shaped like a metric ("0.4898") is how this codebase's own evidence
# documents render a score or a delta (see `application/objectives.py::_measure`). A note whose text contains one is
# treated as an attempt to restate a ledger number from memory rather than to state a durable fact, and refused.
_NUMERIC_CLAIM_RE = re.compile(r"\d{1,3}(?:\.\d+)?\s*%|\b0\.\d{2,4}\b")

_ROLES = ("user", "assistant")


class MemoryError(ValueError):
    """A memory that restates a ledger number is not a memory, it is an unverified second copy of the ledger."""


@dataclass(frozen=True)
class Preference:
    """A key/value the user set, carrying the provenance of when and how it was set.

    `source` names who or what set it ("user" for an explicit setting, "assistant" for a value the assistant
    recorded on the user's behalf during a conversation); `set_at` is when. Both travel with the value so a later
    reader can tell a stale default from a considered choice.
    """

    key: str
    value: Any
    set_at: str
    source: str


@dataclass(frozen=True)
class MemoryNote:
    """A durable fact the user asked to remember, or the assistant recorded with the user's assent.

    `text` is never a bare numeric claim about a result — see `remember` — because that is the ledger's job, and a
    memory that duplicates it would go stale the moment the ledger is repaired.

    `revised` is empty until the note is edited and then carries when it last was. A reader who acts on a note is
    entitled to know that its text is not the text that was originally stored; keeping the original `created`
    alongside it says that this is the same note rather than a replacement, which is what a caller holding the id
    needs to be true. Rows written before this field existed simply have none, and load as unedited.
    """

    id: str
    text: str
    source: str
    created: str
    revised: str = ""


@dataclass(frozen=True)
class ChatTurn:
    """One turn of a conversation. `meta` is empty for a user's turn and, for the assistant's, carries the same
    citations, refusals and tool calls the caller saw when the turn was made - so reopening a thread later shows
    the honesty pass's receipt, not just the prose that survived it.

    Before this field existed, a turn's citations, refusals and tool calls were handed to the caller once, by
    `application/chat.py::converse`, and then gone: the thread only ever kept `role`/`content`/`ts`, so reopening
    it showed an answer with no row behind it - the opposite of what the honesty boundary exists to guarantee.
    """

    role: str
    content: str
    ts: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ChatThread:
    id: str
    turns: tuple[ChatTurn, ...]
    created: str
    updated: str


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def memory_dir(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "memory"


def _notes_path(root: Path) -> Path:
    return memory_dir(root) / "notes.jsonl"


def _preferences_path(root: Path) -> Path:
    return memory_dir(root) / "preferences.jsonl"


def _chat_path(root: Path) -> Path:
    return memory_dir(root) / "chat.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every well-formed line in `path`. A corrupt line is skipped, not fatal — one bad line must not hide the
    rest of the store, the same rule `objectives.load_all` follows for a malformed objective file."""
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


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _load_notes(root: Path) -> list[MemoryNote]:
    notes: list[MemoryNote] = []
    for row in _read_jsonl(_notes_path(root)):
        try:
            notes.append(MemoryNote(**row))
        except TypeError:
            continue
    return notes


def _admissible(text: str) -> str:
    """The text a note is allowed to hold, or a refusal. Shared by `remember` and `revise` deliberately: a guard
    that only covers the way a note is created is not a guard, because editing reaches the same store."""
    text = text.strip()
    if not text:
        raise MemoryError("a memory note with no text remembers nothing")
    if _NUMERIC_CLAIM_RE.search(text):
        raise MemoryError(
            f"refusing to remember {text!r}: it reads as a numeric claim about a result, and evidence comes only "
            "from the ledger. If this is true, the ledger already has it; ask for the objective's progress "
            "instead of recording the number here."
        )
    return text


def remember(root: Path, text: str, *, source: str) -> MemoryNote:
    """Store a durable fact. Refuses a bare numeric claim about a result (e.g. "GSM8K is now 49%") because that
    number belongs to the ledger; if it is true the ledger already contains it, and if the ledger is later
    repaired, a copy kept here would quietly go on lying."""
    note = MemoryNote(id=uuid.uuid4().hex[:12], text=_admissible(text), source=source, created=_now())
    _append_jsonl(_notes_path(root), asdict(note))
    return note


def revise(root: Path, note_id: str, text: str, *, source: str) -> MemoryNote | None:
    """Replace a note's text in place, returning the revised note, or `None` if no note carries that id.

    A note the user can write but never correct is a note that goes stale and stays stale, which is worse than no
    note at all — the user's own record of their work is the one thing here they are entitled to change. The
    revision keeps the id and the original `created` so that this is an edit rather than a delete and a rewrite,
    and it passes the same admissibility guard `remember` does, since the store cannot tell how a numeric claim
    arrived in it and should not have to.

    `source` records who made the edit, which may differ from who wrote the note: the assistant may revise a note
    it recorded on the user's behalf, and the user may correct one the assistant wrote.
    """
    text = _admissible(text)
    path = _notes_path(root)
    rows = _read_jsonl(path)
    revised: MemoryNote | None = None
    for index, row in enumerate(rows):
        if row.get("id") != note_id:
            continue
        revised = MemoryNote(id=note_id, text=text, source=source, created=str(row.get("created", "")), revised=_now())
        rows[index] = asdict(revised)
        break
    if revised is not None:
        _write_jsonl(path, rows)
    return revised


def recall(root: Path, query: str = "", *, limit: int = 5) -> list[MemoryNote]:
    """Rank stored notes by substring match, then recency.

    This is a simple substring-and-recency ranker, not an embedding search: a query "matches" a note only when it
    appears as a literal, case-insensitive substring of the note's text. Matching notes are returned before
    non-matching ones, and each group is ordered most-recent-first. Recency is the note's position in the
    append-only store rather than its wall-clock timestamp, since two notes written within the same second would
    otherwise tie; append order is exact where the clock is not. A vector/embedding recall able to match on meaning
    rather than characters is a later capability; until it exists, this ranker is deliberately honest about what it
    can find, rather than pretending a keyword index is semantic search only for that gap to surface as a silent
    miss later.
    """
    notes = list(enumerate(_load_notes(root)))  # append order: later index is more recent
    notes.sort(key=lambda pair: pair[0], reverse=True)
    if query.strip():
        q = query.strip().lower()
        notes.sort(key=lambda pair: 0 if q in pair[1].text.lower() else 1)
    return [note for _, note in notes[:limit]]


def forget(root: Path, note_id: str) -> bool:
    """Remove a note by id. Returns whether a note was actually removed."""
    path = _notes_path(root)
    rows = _read_jsonl(path)
    kept = [r for r in rows if r.get("id") != note_id]
    removed = len(kept) != len(rows)
    if removed:
        _write_jsonl(path, kept)
    return removed


def set_preference(root: Path, key: str, value: Any, *, source: str) -> Preference:
    key = key.strip()
    if not key:
        raise MemoryError("a preference with no key cannot be recalled by key")
    pref = Preference(key=key, value=value, set_at=_now(), source=source)
    _append_jsonl(_preferences_path(root), asdict(pref))
    return pref


def preferences(root: Path) -> dict[str, Preference]:
    """Every preference's current value: an append log of settings, one entry per key wins by most recent
    `set_at`, so the history of a changed mind stays on disk even though only the latest value is read back."""
    latest: dict[str, Preference] = {}
    for row in _read_jsonl(_preferences_path(root)):
        try:
            pref = Preference(**row)
        except TypeError:
            continue
        current = latest.get(pref.key)
        if current is None or pref.set_at >= current.set_at:
            latest[pref.key] = pref
    return latest


def append_turn(
    root: Path, thread_id: str, role: str, content: str, meta: dict[str, Any] | None = None
) -> ChatTurn:
    if role not in _ROLES:
        raise MemoryError(f"chat turn role must be one of {_ROLES}, got {role!r}")
    turn = ChatTurn(role=role, content=content, ts=_now(), meta=dict(meta or {}))
    _append_jsonl(_chat_path(root), {"thread_id": thread_id, **asdict(turn)})
    return turn


def _thread_from_rows(thread_id: str, rows: list[dict[str, Any]]) -> ChatThread:
    turns = tuple(
        ChatTurn(role=r["role"], content=r["content"], ts=r["ts"], meta=dict(r.get("meta") or {})) for r in rows
    )
    return ChatThread(id=thread_id, turns=turns, created=turns[0].ts, updated=turns[-1].ts)


def thread(root: Path, thread_id: str) -> ChatThread:
    rows = [r for r in _read_jsonl(_chat_path(root)) if r.get("thread_id") == thread_id]
    if not rows:
        raise MemoryError(f"no chat thread {thread_id!r} in this workspace")
    return _thread_from_rows(thread_id, rows)


def threads(root: Path) -> list[ChatThread]:
    """Every chat thread in the workspace, most recently updated first."""
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in _read_jsonl(_chat_path(root)):
        tid = str(row.get("thread_id") or "")
        if not tid:
            continue
        by_id.setdefault(tid, []).append(row)
    out = [_thread_from_rows(tid, rows) for tid, rows in by_id.items()]
    out.sort(key=lambda t: t.updated, reverse=True)
    return out
