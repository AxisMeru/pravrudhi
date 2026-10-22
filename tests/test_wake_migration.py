# ruff: noqa: E501
"""#1038 PR-4: structure checks on supabase/wake_migration_v1.sql (and its down-migration). Text-level, no database: they pin the
properties R2 and the data-change approval rest on. The SQL is RUN by tests/test_wake_pg_it.py where docker and a Postgres image exist."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "supabase"
UP = (ROOT / "wake_migration_v1.sql").read_text()
DOWN = (ROOT / "wake_migration_v1_down.sql").read_text()
TABLES = ("wake_requests", "wake_nonces", "wake_limits")
FUNCS = re.findall(r"create or replace function (tenancy\.\w+)\(", UP)


def _code(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def test_every_table_is_in_the_tenancy_schema_and_has_rls_with_no_policy() -> None:
    code = _code(UP)
    created = re.findall(r"create table if not exists (\S+)", code)
    assert sorted(created) == sorted(f"tenancy.{t}" for t in TABLES)  # nothing else is created, and never in public
    for t in TABLES:
        assert f"alter table tenancy.{t} enable row level security" in code
    assert "create policy" not in code.lower()  # RLS with no policy: no role but the owner (the functions) reads a row


def test_all_privileges_are_revoked_from_public_anon_and_authenticated_and_nothing_is_granted_on_tables() -> None:
    code = _code(UP)
    assert "revoke all on tenancy.wake_requests, tenancy.wake_nonces, tenancy.wake_limits from public" in code
    assert "from anon" in code and "from authenticated" in code
    assert not re.search(r"grant\s+(select|insert|update|delete|all)[^;]*\bon\s+(table\s+)?tenancy\.wake", code, re.I)
    assert not re.search(r"grant[^;]*\bto\s+(public|anon|authenticated)\b", code, re.I)


def test_every_function_is_security_definer_with_a_fixed_search_path_and_only_the_login_role_may_run_it() -> None:
    code = _code(UP)
    assert len(FUNCS) == 12
    bodies = re.split(r"create or replace function ", code)[1:]
    for b in bodies:
        name = b.split("(")[0]
        if name == "tenancy._wake_now":
            assert "set search_path = pg_catalog" in b
            continue
        assert "security definer" in b and "set search_path = pg_catalog, tenancy" in b, name
    for f in FUNCS:
        assert re.search(rf"revoke all on function {re.escape(f)}\(", code), f"{f} is not revoked from public"
    public = [f for f in FUNCS if not f.startswith("tenancy._")]
    for f in public:
        assert re.search(rf"grant execute on function {re.escape(f)}\([^)]*\) to tenancy_rpc;", code), f"{f} has no grant to the login role"
    assert not [f for f in FUNCS if f.startswith("tenancy._") and f"grant execute on function {f}(" in code]  # the helpers are internal


def test_every_write_function_purges_and_every_read_ignores_expired_rows() -> None:
    code = _code(UP)
    for fn in ("wake_ensure", "wake_extend", "wake_nonce", "wake_begin"):
        body = code.split(f"function tenancy.{fn}(")[1].split("end $$;")[0]
        assert "tenancy._wake_purge()" in body, fn
    for fn in ("wake_open", "wake_state", "wake_lease", "wake_active"):
        body = code.split(f"function tenancy.{fn}(")[1].split("end $$;")[0]
        assert "expires_at > t" in body, fn  # retention is not visibility
        assert "_wake_purge" not in body and "insert" not in body and "update" not in body and "delete" not in body, fn  # a read writes nothing


def test_the_listing_is_only_unanswered_young_rows_and_the_nonce_insert_is_atomic() -> None:
    code = _code(UP)
    assert "answer is null and expires_at > t and t - created_at <= p_req_ttl" in code.split("function tenancy.wake_open(")[1]
    assert "on conflict (nonce) do nothing" in code and "primary key" in code  # the unique key makes the replay guard atomic
    assert "for update" in code.split("function tenancy.wake_begin(")[1].split("end $$;")[0]  # the user's row is locked
    assert "pg_advisory_xact_lock" in code.split("function tenancy.wake_ensure(")[1].split("end $$;")[0]  # one live request per judge


def test_only_coordination_columns_exist_never_content() -> None:
    code = _code(UP).lower()
    blocks = re.findall(r"create table if not exists tenancy\.\w+ \((.*?)\n\);", code, re.S)
    assert len(blocks) == 3
    cols = {m for b in blocks for m in re.findall(r"^\s{2}(\w+) (?:text|double precision|jsonb)", b, re.M)}
    assert cols == {"nonce", "label", "kind", "lease_ref", "created_at", "expires_at", "answer", "answer_lease", "answered_at",
                    "user_id", "starts_json", "active_since", "cool_until"}
    for banned in ("fact", "narrative", "result", "paper", "contract", "text_body", "payload"):
        assert not [c for c in cols if banned in c]


def test_the_down_migration_drops_exactly_what_the_up_migration_creates() -> None:
    down = _code(DOWN)
    for t in TABLES:
        assert f"drop table if exists tenancy.{t};" in down
    dropped = set(re.findall(r"drop function if exists (tenancy\.\w+)\(", down))
    assert dropped == set(FUNCS) and not re.search(r"drop (schema|role|extension)|truncate|delete from", down, re.I)
