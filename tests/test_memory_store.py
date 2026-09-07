"""Tests for `memory_store.py`: the `MemoryStore` shape over a local root and over Supabase."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pravrudhi.api.identity import User
from pravrudhi.application.memory import MemoryError as PravrudhiMemoryError
from pravrudhi.application.memory_store import (
    FileMemoryStore,
    SupabaseMemoryStore,
    store_for,
)

USER_ID = "user-123"


class FakeFetch:
    """Records every call and returns queued responses per (method, path), FIFO."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []
        self._queues: dict[tuple[str, str], list[list[dict[str, Any]] | dict[str, Any]]] = {}

    def queue(self, method: str, path: str, response: list[dict[str, Any]] | dict[str, Any]) -> None:
        self._queues.setdefault((method, path), []).append(response)

    def __call__(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        self.calls.append((method, path, json, params))
        queued = self._queues.get((method, path))
        if queued:
            return queued.pop(0)
        return []


def _store(fetch: FakeFetch) -> SupabaseMemoryStore:
    return SupabaseMemoryStore("https://example.supabase.co", "service-key", USER_ID, fetch)


# --- FileMemoryStore: delegates to memory.py over a real root -----------------------------------------------


def test_file_memory_store_round_trips(tmp_path: Path) -> None:
    store = FileMemoryStore(tmp_path)

    note = store.remember("the workspace is for a legal-domain LoRA", source="user")
    assert store.recall("legal")[0].id == note.id
    assert store.forget(note.id) is True
    assert store.recall("legal") == []

    pref = store.set_preference("base_model", "qwen2.5-7b", source="user")
    assert store.preferences()["base_model"] == pref

    turn = store.append_turn("thread-1", "user", "hello")
    thread = store.thread("thread-1")
    assert thread.turns == (turn,)
    assert store.threads()[0].id == "thread-1"


def test_file_memory_store_append_turn_with_meta_round_trips(tmp_path: Path) -> None:
    store = FileMemoryStore(tmp_path)
    meta = {"citations": [{"seq": 1, "what": "objective_progress: law"}], "refusals": [], "tool_calls": []}

    turn = store.append_turn("thread-1", "assistant", "it scores 0.5", meta=meta)

    assert turn.meta == meta
    assert store.thread("thread-1").turns[0].meta == meta


# --- SupabaseMemoryStore: each method hits the right table, filtered by user_id -----------------------------


def test_remember_posts_memory_notes_filtered_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue(
        "POST",
        "memory_notes",
        [{"id": "n1", "text": "hello", "source": "user", "created_at": "2026-01-01T00:00:00Z"}],
    )
    note = _store(fetch).remember("hello", source="user")

    assert note.id == "n1"
    assert note.created == "2026-01-01T00:00:00Z"
    method, path, json, params = fetch.calls[0]
    assert (method, path) == ("POST", "memory_notes")
    assert json == {"user_id": USER_ID, "text": "hello", "source": "user"}


def test_recall_gets_memory_notes_filtered_by_user_and_ranks_matches_first(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue(
        "GET",
        "memory_notes",
        [
            {"id": "n2", "text": "no match here", "source": "user", "created_at": "2026-01-02T00:00:00Z"},
            {"id": "n1", "text": "mentions legal domain", "source": "user", "created_at": "2026-01-01T00:00:00Z"},
        ],
    )
    notes = _store(fetch).recall("legal")

    assert [n.id for n in notes] == ["n1", "n2"]
    method, path, json, params = fetch.calls[0]
    assert (method, path) == ("GET", "memory_notes")
    assert params == {"user_id": f"eq.{USER_ID}", "order": "created_at.desc"}


def test_forget_deletes_memory_notes_filtered_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue("DELETE", "memory_notes", [{"id": "n1"}])
    assert _store(fetch).forget("n1") is True
    method, path, json, params = fetch.calls[0]
    assert (method, path) == ("DELETE", "memory_notes")
    assert params == {"id": "eq.n1", "user_id": f"eq.{USER_ID}"}

    fetch2 = FakeFetch()
    fetch2.queue("DELETE", "memory_notes", [])
    assert _store(fetch2).forget("missing") is False


def test_revise_patches_the_note_filtered_by_user_and_stamps_when(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue("PATCH", "memory_notes", [{
        "id": "n1", "text": "corrected", "source": "user",
        "created_at": "2026-01-01T00:00:00Z", "revised_at": "2026-02-02T00:00:00Z",
    }])
    note = _store(fetch).revise("n1", "corrected", source="user")

    assert note is not None
    assert (note.text, note.created, note.revised) == ("corrected", "2026-01-01T00:00:00Z", "2026-02-02T00:00:00Z")
    method, path, json, params = fetch.calls[0]
    assert (method, path) == ("PATCH", "memory_notes")
    assert params == {"id": "eq.n1", "user_id": f"eq.{USER_ID}"}
    assert json is not None and json["text"] == "corrected" and json["revised_at"]


def test_revising_a_note_of_someone_elses_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter names both the id and the owner, so another user's note is indistinguishable from a missing one
    — which is the answer that does not confirm the note exists."""
    fetch = FakeFetch()
    fetch.queue("PATCH", "memory_notes", [])
    assert _store(fetch).revise("someone-elses", "mine now", source="user") is None


def test_revise_refuses_a_numeric_claim_before_fetch() -> None:
    """Editing must not be the way around the guard that creation enforces."""
    fetch = FakeFetch()
    with pytest.raises(PravrudhiMemoryError):
        _store(fetch).revise("n1", "GSM8K is now 49%", source="user")
    assert fetch.calls == []


def test_a_note_never_edited_reads_back_unedited(monkeypatch: pytest.MonkeyPatch) -> None:
    """`revised_at` is null for an unedited note and absent for a row written before the column existed."""
    fetch = FakeFetch()
    fetch.queue("GET", "memory_notes", [
        {"id": "n1", "text": "never edited", "source": "user", "created_at": "2026-01-01T00:00:00Z",
         "revised_at": None},
        {"id": "n2", "text": "older row", "source": "user", "created_at": "2025-12-01T00:00:00Z"},
    ])
    assert [n.revised for n in _store(fetch).recall("")] == ["", ""]


def test_set_preference_posts_preferences_filtered_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue(
        "POST",
        "preferences",
        [{"key": "base_model", "value": "qwen2.5-7b", "set_at": "2026-01-01T00:00:00Z", "source": "user"}],
    )
    pref = _store(fetch).set_preference("base_model", "qwen2.5-7b", source="user")

    assert pref.value == "qwen2.5-7b"
    method, path, json, params = fetch.calls[0]
    assert (method, path) == ("POST", "preferences")
    assert json == {"user_id": USER_ID, "key": "base_model", "value": "qwen2.5-7b", "source": "user"}


def test_preferences_gets_filtered_by_user_and_keeps_latest_per_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue(
        "GET",
        "preferences",
        [
            {"key": "base_model", "value": "new", "set_at": "2026-01-02T00:00:00Z", "source": "user"},
            {"key": "base_model", "value": "old", "set_at": "2026-01-01T00:00:00Z", "source": "user"},
        ],
    )
    prefs = _store(fetch).preferences()

    assert prefs["base_model"].value == "new"
    method, path, json, params = fetch.calls[0]
    assert (method, path) == ("GET", "preferences")
    assert params == {"user_id": f"eq.{USER_ID}", "order": "set_at.desc"}


def test_append_turn_creates_thread_when_missing_then_inserts_turn_then_touches_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch = FakeFetch()
    fetch.queue("GET", "chat_threads", [])  # no existing thread
    fetch.queue("POST", "chat_turns", [{"role": "user", "content": "hi", "created_at": "2026-01-01T00:00:00Z"}])
    turn = _store(fetch).append_turn("thread-1", "user", "hi")

    assert turn.content == "hi"
    assert [(m, p) for m, p, _, _ in fetch.calls] == [
        ("GET", "chat_threads"),
        ("POST", "chat_threads"),
        ("POST", "chat_turns"),
        ("PATCH", "chat_threads"),
    ]
    get_method, get_path, _, get_params = fetch.calls[0]
    assert get_params == {"id": "eq.thread-1", "user_id": f"eq.{USER_ID}"}
    create_method, create_path, create_json, _ = fetch.calls[1]
    assert create_json == {"id": "thread-1", "user_id": USER_ID}
    _, _, turn_json, _ = fetch.calls[2]
    assert turn_json == {"thread_id": "thread-1", "role": "user", "content": "hi", "meta": {}}
    _, _, patch_json, patch_params = fetch.calls[3]
    assert patch_json == {"updated_at": "2026-01-01T00:00:00Z"}
    assert patch_params == {"id": "eq.thread-1", "user_id": f"eq.{USER_ID}"}


def test_append_turn_sends_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue("GET", "chat_threads", [{"id": "thread-1"}])
    meta = {"citations": [{"seq": 1, "what": "objective_progress: law"}], "refusals": [], "tool_calls": []}
    fetch.queue(
        "POST",
        "chat_turns",
        [{"role": "assistant", "content": "it scores 0.5", "created_at": "2026-01-01T00:00:00Z", "meta": meta}],
    )

    turn = _store(fetch).append_turn("thread-1", "assistant", "it scores 0.5", meta=meta)

    assert turn.meta == meta
    _, _, turn_json, _ = fetch.calls[1]
    assert turn_json == {"thread_id": "thread-1", "role": "assistant", "content": "it scores 0.5", "meta": meta}


def test_append_turn_skips_creation_when_thread_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue("GET", "chat_threads", [{"id": "thread-1"}])
    fetch.queue("POST", "chat_turns", [{"role": "user", "content": "hi", "created_at": "2026-01-01T00:00:00Z"}])
    _store(fetch).append_turn("thread-1", "user", "hi")

    assert [(m, p) for m, p, _, _ in fetch.calls] == [
        ("GET", "chat_threads"),
        ("POST", "chat_turns"),
        ("PATCH", "chat_threads"),
    ]


def test_thread_gets_chat_threads_and_chat_turns_filtered_by_user(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue(
        "GET",
        "chat_threads",
        [{"id": "thread-1", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z"}],
    )
    fetch.queue("GET", "chat_turns", [{"role": "user", "content": "hi", "created_at": "2026-01-01T00:00:00Z"}])
    thread = _store(fetch).thread("thread-1")

    assert thread.id == "thread-1"
    assert thread.turns[0].content == "hi"
    assert thread.updated == "2026-01-02T00:00:00Z"
    _, _, _, threads_params = fetch.calls[0]
    assert threads_params == {"id": "eq.thread-1", "user_id": f"eq.{USER_ID}"}
    _, _, _, turns_params = fetch.calls[1]
    assert turns_params == {"thread_id": "eq.thread-1", "order": "created_at.asc"}


def test_thread_raises_and_makes_no_further_call_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue("GET", "chat_threads", [])
    with pytest.raises(PravrudhiMemoryError):
        _store(fetch).thread("missing")
    assert len(fetch.calls) == 1


def test_threads_lists_chat_threads_for_user_most_recently_updated_first(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = FakeFetch()
    fetch.queue(
        "GET",
        "chat_threads",
        [
            {"id": "older", "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z"},
            {"id": "newer", "created_at": "2026-01-02T00:00:00Z", "updated_at": "2026-01-03T00:00:00Z"},
        ],
    )
    fetch.queue("GET", "chat_turns", [])
    fetch.queue("GET", "chat_turns", [])
    threads = _store(fetch).threads()

    assert [t.id for t in threads] == ["newer", "older"]
    method, path, json, params = fetch.calls[0]
    assert (method, path) == ("GET", "chat_threads")
    assert params == {"user_id": f"eq.{USER_ID}"}


# --- Guards refuse before any network call --------------------------------------------------------------


def test_remember_refuses_empty_text_before_fetch() -> None:
    fetch = FakeFetch()
    with pytest.raises(PravrudhiMemoryError):
        _store(fetch).remember("   ", source="user")
    assert fetch.calls == []


def test_remember_refuses_numeric_claim_before_fetch() -> None:
    fetch = FakeFetch()
    with pytest.raises(PravrudhiMemoryError):
        _store(fetch).remember("GSM8K is now 49%", source="user")
    assert fetch.calls == []


def test_set_preference_refuses_empty_key_before_fetch() -> None:
    fetch = FakeFetch()
    with pytest.raises(PravrudhiMemoryError):
        _store(fetch).set_preference("   ", "value", source="user")
    assert fetch.calls == []


def test_append_turn_refuses_bad_role_before_fetch() -> None:
    fetch = FakeFetch()
    with pytest.raises(PravrudhiMemoryError):
        _store(fetch).append_turn("thread-1", "system", "x")
    assert fetch.calls == []


# --- store_for: which home a request gets ---------------------------------------------------------------


def test_store_for_returns_file_store_when_no_user(tmp_path: Path) -> None:
    assert isinstance(store_for(tmp_path, None), FileMemoryStore)


def test_store_for_returns_file_store_when_supabase_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    user = User(id=USER_ID, email="u@example.com", role="authenticated")
    assert isinstance(store_for(tmp_path, user), FileMemoryStore)


def test_store_for_returns_supabase_store_when_user_and_env_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")
    user = User(id=USER_ID, email="u@example.com", role="authenticated")
    store = store_for(tmp_path, user)
    assert isinstance(store, SupabaseMemoryStore)
    assert store._user_id == USER_ID  # noqa: SLF001 — verifying construction, not behavior, here
