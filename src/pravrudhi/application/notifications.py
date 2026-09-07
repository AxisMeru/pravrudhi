"""Nights, dispatches and workflow runs are start-and-walk-away work, and there was no way to learn that one
finished short of sitting on a page and polling it. This module is the record of what happened while nobody was
watching: `emit` is called from the places that already know something finished — a run ending, a job accepted
or rejected, a beat dispatching, a workflow run completing, a criterion being met — and appends one entry to
`.pravrudhi/notifications.jsonl`. Nothing here changes what those call sites return; this is a side record only.

`unread`, `recent` and `mark_read` are the read side: what a bell in the interface polls, and what "mark all
read" calls when a person actually looks. A corrupt line is skipped rather than fatal, the same discipline every
other `.jsonl` reader in this codebase already follows (see `heartbeat.history`, `dispatchboard._read_all`). The
log is capped at `MAX_NOTIFICATIONS` entries so an idle workspace whose heartbeat has been ticking for months
does not grow this file without bound; the oldest entries are the ones dropped.

A notification's `title`, `detail` and `ref` are run through `credentials.redact` before they are ever written,
because a rejected job's reasons or a run's log line can echo back whatever text produced the failure, and that
text must never be trusted to be free of a key someone pasted into a prompt or a config value.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.application.credentials import redact

MAX_NOTIFICATIONS = 500

_LOCK = threading.Lock()


@dataclass
class Notification:
    id: str
    at: str
    kind: str
    title: str
    detail: str
    ref: str
    read: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "at": self.at, "kind": self.kind, "title": self.title,
            "detail": self.detail, "ref": self.ref, "read": self.read,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Notification:
        return Notification(
            id=str(d["id"]),
            at=str(d["at"]),
            kind=str(d["kind"]),
            title=str(d.get("title", "")),
            detail=str(d.get("detail", "")),
            ref=str(d.get("ref", "")),
            read=bool(d.get("read", False)),
        )


def log_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "notifications.jsonl"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_all(root: Path) -> list[Notification]:
    path = log_path(root)
    if not path.exists():
        return []
    out: list[Notification] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(Notification.from_dict(json.loads(line)))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue  # a corrupt line must not blind the reader to the rest
    return out


def _write_all(root: Path, rows: list[Notification]) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r.to_dict(), sort_keys=True) + "\n" for r in rows))
    tmp.replace(path)


def emit(root: Path, *, kind: str, title: str, detail: str = "", ref: str = "") -> Notification:
    """Record that something finished. `kind` is a short machine tag (`run_finished`, `job_accepted`,
    `job_rejected`, `job_cancelled`, `beat_dispatched`, `workflow_completed`, `criterion_met`, ...); `title` and
    `detail` are what a person reads; `ref` is a path into the app pointing at where it happened, or empty when
    there is nowhere to link. `title`, `detail` and `ref` are redacted before they are written, so a value that
    happens to be shaped like a provider key never lands in the feed."""
    note = Notification(
        id=uuid.uuid4().hex[:12], at=_now(), kind=kind,
        title=redact(title), detail=redact(detail), ref=redact(ref), read=False,
    )
    with _LOCK:
        path = log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(note.to_dict(), sort_keys=True) + "\n")
        rows = _read_all(root)
        if len(rows) > MAX_NOTIFICATIONS:
            _write_all(root, rows[-MAX_NOTIFICATIONS:])
    return note


def recent(root: Path, n: int = 50) -> list[Notification]:
    """The most recent notifications, newest first."""
    rows = sorted(_read_all(root), key=lambda r: r.at)
    rows.reverse()
    return rows[: max(0, n)]


def unread(root: Path) -> list[Notification]:
    """Every notification not yet marked read, newest first."""
    return [r for r in recent(root, n=MAX_NOTIFICATIONS) if not r.read]


def mark_read(root: Path, ids: list[str]) -> list[Notification]:
    """Mark the given notification ids read. An id this log does not have is ignored, not an error."""
    wanted = set(ids)
    with _LOCK:
        rows = _read_all(root)
        for r in rows:
            if r.id in wanted:
                r.read = True
        _write_all(root, rows)
    return rows


__all__ = ["MAX_NOTIFICATIONS", "Notification", "emit", "log_path", "mark_read", "recent", "unread"]
