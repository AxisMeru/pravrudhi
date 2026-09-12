"""Memory storage across engines: the same shape, two homes.

Until this module existed, `memory.py`'s API was reachable only through a `root` directory on disk, which
fits the local single-user engine but not a hosted one: a logged-in user on a shared, stateless engine
process has no per-workspace directory of their own to write a preference, a note, or a chat turn into.
`MemoryStore` is `memory.py`'s API with `root` bound at construction instead of passed per call, so a caller
that already holds a store never needs to know whether it is writing to a JSONL file or a Supabase table.
`store_for` picks the right one for the request in front of it — a `Path` root when there is one (the local
engine, or no verified user), Supabase-backed storage otherwise.

`SupabaseMemoryStore` speaks PostgREST directly rather than through a client library, because the only thing
it needs is a handful of table reads and writes scoped to one `user_id`, and because an injectable `fetch`
lets it be exercised in tests with no network. It reapplies the same guards `memory.py` enforces (a durable
note may never restate a ledger number, a preference must have a key, a chat turn's role must be one of the
two the schema allows) before any network call, not after, so a rejected write never becomes a half-written
row a caller has to clean up.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol

import httpx

from pravrudhi.api.identity import User
from pravrudhi.application import memory
from pravrudhi.application.memory import ChatThread, ChatTurn, MemoryNote, Preference


class Fetch(Protocol):
    """One PostgREST call: a method and a table path, with an optional JSON body and query params."""

    def __call__(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]: ...


class MemoryStore(Protocol):
    """`memory.py`'s API with the store's home (a workspace root, or a user's Supabase rows) already bound."""

    def remember(self, text: str, *, source: str) -> MemoryNote: ...

    def recall(self, query: str = "", *, limit: int = 5) -> list[MemoryNote]: ...

    def revise(self, note_id: str, text: str, *, source: str) -> MemoryNote | None: ...

    def forget(self, note_id: str) -> bool: ...

    def set_preference(self, key: str, value: Any, *, source: str) -> Preference: ...

    def preferences(self) -> dict[str, Preference]: ...

    def append_turn(self, thread_id: str, role: str, content: str, meta: dict[str, Any] | None = None) -> ChatTurn: ...

    def thread(self, thread_id: str) -> ChatThread: ...

    def threads(self) -> list[ChatThread]: ...


class FileMemoryStore:
    """`MemoryStore` over the local JSONL files under `<root>/.pravrudhi/memory/`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def remember(self, text: str, *, source: str) -> MemoryNote:
        return memory.remember(self._root, text, source=source)

    def recall(self, query: str = "", *, limit: int = 5) -> list[MemoryNote]:
        return memory.recall(self._root, query, limit=limit)

    def revise(self, note_id: str, text: str, *, source: str) -> MemoryNote | None:
        return memory.revise(self._root, note_id, text, source=source)

    def forget(self, note_id: str) -> bool:
        return memory.forget(self._root, note_id)

    def set_preference(self, key: str, value: Any, *, source: str) -> Preference:
        return memory.set_preference(self._root, key, value, source=source)

    def preferences(self) -> dict[str, Preference]:
        return memory.preferences(self._root)

    def append_turn(self, thread_id: str, role: str, content: str, meta: dict[str, Any] | None = None) -> ChatTurn:
        return memory.append_turn(self._root, thread_id, role, content, meta=meta)

    def thread(self, thread_id: str) -> ChatThread:
        return memory.thread(self._root, thread_id)

    def threads(self) -> list[ChatThread]:
        return memory.threads(self._root)


def _note_from_row(row: dict[str, Any]) -> MemoryNote:
    """One reading of a `memory_notes` row. `revised_at` is null for a note nobody has edited, and for a row
    written before the column existed; both mean unedited, which is the empty string this dataclass uses."""
    return MemoryNote(
        id=str(row["id"]), text=str(row["text"]), source=str(row["source"]),
        created=str(row["created_at"]), revised=str(row.get("revised_at") or ""),
    )


def _as_list(result: list[dict[str, Any]] | dict[str, Any]) -> list[dict[str, Any]]:
    return result if isinstance(result, list) else [result]


def _one(result: list[dict[str, Any]] | dict[str, Any]) -> dict[str, Any]:
    rows = _as_list(result)
    return rows[0]


def _httpx_fetch(url: str, service_key: str) -> Fetch:
    """The default `Fetch`: PostgREST over `{url}/rest/v1/`, authenticated with the service key.

    The key is carried only in request headers, never interpolated into a URL, a log line, or an
    exception message, so nothing here can leak it into a place a log aggregator or error tracker keeps.
    """
    base = url.rstrip("/") + "/rest/v1"
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }

    def call(
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        response = httpx.request(method, f"{base}/{path}", headers=headers, json=json, params=params, timeout=10.0)
        response.raise_for_status()
        if not response.content:
            return []
        result: list[dict[str, Any]] | dict[str, Any] = response.json()
        return result

    return call


class SupabaseMemoryStore:
    """`MemoryStore` over the `preferences`, `memory_notes`, `chat_threads` and `chat_turns` tables
    (`supabase/schema.sql`), scoped to one `user_id` the way every row's `owner all` RLS policy is."""

    def __init__(self, url: str, service_key: str, user_id: str, fetch: Fetch | None = None) -> None:
        self._user_id = user_id
        self._fetch = fetch if fetch is not None else _httpx_fetch(url, service_key)

    def remember(self, text: str, *, source: str) -> MemoryNote:
        text = memory._admissible(text)
        row = _one(self._fetch("POST", "memory_notes", json={"user_id": self._user_id, "text": text, "source": source}))
        return _note_from_row(row)

    def revise(self, note_id: str, text: str, *, source: str) -> MemoryNote | None:
        """Edit a note in place. `None` when no note of the caller's carries that id — which is also what a note
        belonging to somebody else looks like from here, since the filter is on both columns."""
        text = memory._admissible(text)
        rows = _as_list(self._fetch(
            "PATCH", "memory_notes",
            params={"id": f"eq.{note_id}", "user_id": f"eq.{self._user_id}"},
            json={"text": text, "source": source, "revised_at": memory._now()},
        ))
        return _note_from_row(rows[0]) if rows else None

    def recall(self, query: str = "", *, limit: int = 5) -> list[MemoryNote]:
        rows = _as_list(
            self._fetch(
                "GET", "memory_notes", params={"user_id": f"eq.{self._user_id}", "order": "created_at.desc"}
            )
        )
        notes = [_note_from_row(r) for r in rows]
        if query.strip():
            q = query.strip().lower()
            notes.sort(key=lambda n: 0 if q in n.text.lower() else 1)
        return notes[:limit]

    def forget(self, note_id: str) -> bool:
        rows = _as_list(
            self._fetch(
                "DELETE", "memory_notes", params={"id": f"eq.{note_id}", "user_id": f"eq.{self._user_id}"}
            )
        )
        return len(rows) > 0

    def set_preference(self, key: str, value: Any, *, source: str) -> Preference:
        key = key.strip()
        if not key:
            raise memory.MemoryError("a preference with no key cannot be recalled by key")
        row = _one(
            self._fetch(
                "POST",
                "preferences",
                json={"user_id": self._user_id, "key": key, "value": value, "source": source},
            )
        )
        return Preference(
            key=str(row["key"]), value=row["value"], set_at=str(row["set_at"]), source=str(row["source"])
        )

    def preferences(self) -> dict[str, Preference]:
        rows = _as_list(
            self._fetch("GET", "preferences", params={"user_id": f"eq.{self._user_id}", "order": "set_at.desc"})
        )
        latest: dict[str, Preference] = {}
        for r in rows:
            key = str(r["key"])
            if key not in latest:
                latest[key] = Preference(
                    key=key, value=r["value"], set_at=str(r["set_at"]), source=str(r["source"])
                )
        return latest

    def append_turn(self, thread_id: str, role: str, content: str, meta: dict[str, Any] | None = None) -> ChatTurn:
        if role not in memory._ROLES:
            raise memory.MemoryError(f"chat turn role must be one of {memory._ROLES}, got {role!r}")
        existing = _as_list(
            self._fetch(
                "GET", "chat_threads", params={"id": f"eq.{thread_id}", "user_id": f"eq.{self._user_id}"}
            )
        )
        if not existing:
            self._fetch("POST", "chat_threads", json={"id": thread_id, "user_id": self._user_id})
        row = _one(
            self._fetch(
                "POST",
                "chat_turns",
                json={"thread_id": thread_id, "role": role, "content": content, "meta": dict(meta or {})},
            )
        )
        self._fetch(
            "PATCH",
            "chat_threads",
            json={"updated_at": row["created_at"]},
            params={"id": f"eq.{thread_id}", "user_id": f"eq.{self._user_id}"},
        )
        return ChatTurn(
            role=str(row["role"]), content=str(row["content"]), ts=str(row["created_at"]),
            meta=dict(row.get("meta") or {}),
        )

    def thread(self, thread_id: str) -> ChatThread:
        threads_rows = _as_list(
            self._fetch(
                "GET", "chat_threads", params={"id": f"eq.{thread_id}", "user_id": f"eq.{self._user_id}"}
            )
        )
        if not threads_rows:
            raise memory.MemoryError(f"no chat thread {thread_id!r} in this workspace")
        thread_row = threads_rows[0]
        turns = self._turns(thread_id)
        return ChatThread(
            id=thread_id, turns=turns, created=str(thread_row["created_at"]), updated=str(thread_row["updated_at"])
        )

    def threads(self) -> list[ChatThread]:
        thread_rows = _as_list(self._fetch("GET", "chat_threads", params={"user_id": f"eq.{self._user_id}"}))
        out = [
            ChatThread(
                id=str(t["id"]),
                turns=self._turns(str(t["id"])),
                created=str(t["created_at"]),
                updated=str(t["updated_at"]),
            )
            for t in thread_rows
        ]
        out.sort(key=lambda t: t.updated, reverse=True)
        return out

    def _turns(self, thread_id: str) -> tuple[ChatTurn, ...]:
        rows = _as_list(
            self._fetch("GET", "chat_turns", params={"thread_id": f"eq.{thread_id}", "order": "created_at.asc"})
        )
        return tuple(
            ChatTurn(
                role=str(r["role"]), content=str(r["content"]), ts=str(r["created_at"]),
                meta=dict(r.get("meta") or {}),
            )
            for r in rows
        )


class MemoryAccessError(ValueError):
    """A caller for whom no memory location can be resolved: genuinely anonymous, on a deployment where
    authentication is not switched off, so there is no local-operator-by-construction reading to fall back on
    and no user id to keep a store apart by."""


def _per_user_memory_root(engine_root: Path, user_id: str) -> Path:
    """Where one signed-in user's file-backed memory lives when no Supabase project is configured to hold it
    instead: `<engine_root>/.pravrudhi/memory-users/<user id>`, treated as its own root exactly the way
    `application/workspaces.py` treats a workspace directory as a complete project root -- `FileMemoryStore`'s
    own `.pravrudhi/memory/` layout applies underneath it unchanged, so nothing else in this module needs to
    know the difference between a per-user root and the engine's own.

    This project's standing rule is that no deployment is ever given a Supabase service key, so this is not a
    rare fallback: it is the store every signed-in product user actually gets today, and the shared root every
    one of them fell into before this existed.
    """
    uid = (user_id or "").strip()
    if not uid or "/" in uid or "\\" in uid or ".." in uid:
        raise MemoryAccessError(f"cannot resolve a memory location for user id {user_id!r}")
    base = (Path(engine_root) / ".pravrudhi" / "memory-users").resolve()
    path = (base / uid).resolve()
    if path != base and base not in path.parents:
        raise MemoryAccessError(f"memory path for user id {user_id!r} would escape {base}")
    return path


def store_for(root: Path, user: User | None) -> MemoryStore:
    """The right store for this request: Supabase-backed when a verified user has a Supabase project configured
    to hold their rows; a per-user file-backed store, keyed by their own id, when one is signed in but no such
    project is configured (this deployment's standing case, since a service key is never issued); the engine's
    own root only for the one caller it was always the project of - the operator, either signed in on the
    admin allowlist or, with authentication switched off entirely, the local caller by construction.

    A genuinely anonymous caller on a deployment where authentication is not disabled is refused rather than
    handed the engine's own root: exactly the hole a signed-in non-admin user fell into before this existed,
    just with no user id at all to keep it apart with.
    """
    from pravrudhi.api.roles import is_admin

    if user is not None:
        url = os.environ.get("SUPABASE_URL", "")
        service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
        if url and service_key:
            return SupabaseMemoryStore(url, service_key, user.id)
        if is_admin(user):
            return FileMemoryStore(root)
        return FileMemoryStore(_per_user_memory_root(root, user.id))
    if is_admin(None):
        return FileMemoryStore(root)
    raise MemoryAccessError("sign in to use memory: nobody is asking, and this machine is not the operator's own")
