"""INTEGRATION: the backstop and the engine sweeper against the pinned database image with the real migration's grants.

Skipped unless PRAVRUDHI_A0_PG_IT=1 and PRAVRUDHI_A0_MIGRATION names the migration file (the applied one; not part of this
repository). Needs docker and the optional pg8000 driver (on PYTHONPATH). A throwaway container on loopback, removed at the end;
the role passwords are random and exist only inside it. Proves that each role's pass calls only functions the migration grants it:
a call outside the grant is a permission error here, exactly as in production."""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import time
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    os.environ.get("PRAVRUDHI_A0_PG_IT") != "1" or not os.environ.get("PRAVRUDHI_A0_MIGRATION"),
    reason="integration test; set PRAVRUDHI_A0_PG_IT=1 and PRAVRUDHI_A0_MIGRATION=<migration file>",
)

IMAGE = "supabase/postgres@sha256:b3bfedb107413abb3b8cb0d0874b0414a1dceb3d55bc0c778de6ad22d1f7dc86"
NAME, PORT = "a0-backstop-it", 55437
REPO = Path(__file__).resolve().parent.parent
PW = {"postgres": secrets.token_hex(8), "assist_spend_rpc": secrets.token_hex(8), "assist_sweeper_rpc": secrets.token_hex(8)}


def sh(*a: str, **k) -> subprocess.CompletedProcess:
    return subprocess.run(list(a), capture_output=True, text=True, timeout=180, **k)


def psql(sql: str, user: str = "postgres") -> tuple[int, str]:
    r = sh(
        "docker",
        "exec",
        "-e",
        f"PGPASSWORD={PW[user]}",
        NAME,
        "psql",
        "-h",
        "127.0.0.1",
        "-U",
        user,
        "-d",
        "postgres",
        "-X",
        "-A",
        "-t",
        "-q",
        "-c",
        sql,
    )
    return r.returncode, (r.stdout or "").strip()


def grants_of(sql: str) -> dict[str, set[str]]:
    """The migration's `grant execute on function ... to <roles>` statements, as {role: {function names}} (comments stripped)."""
    text = re.sub(r"--[^\n]*", "", sql)
    out: dict[str, set[str]] = {}
    for m in re.finditer(r"grant\s+execute\s+on\s+function\s+(.*?)\s+to\s+([a-z_,\s]+?);", text, re.S | re.I):
        names = set(re.findall(r"assist\.([a-z_]+)\s*\(", m.group(1)))
        for role in re.split(r"[,\s]+", m.group(2).strip()):
            out.setdefault(role, set()).update(names)
    return out


@pytest.fixture(scope="module")
def db():
    pytest.importorskip("pg8000")
    mig = Path(os.environ["PRAVRUDHI_A0_MIGRATION"])
    sh("docker", "rm", "-f", NAME)
    r = sh(
        "docker",
        "run",
        "-d",
        "--name",
        NAME,
        "--memory",
        "2g",
        "-p",
        f"127.0.0.1:{PORT}:5432",
        "-e",
        f"POSTGRES_PASSWORD={PW['postgres']}",
        IMAGE,
    )
    assert r.returncode == 0, r.stderr[:200]
    try:
        for _ in range(120):
            time.sleep(1)
            if (
                psql("select count(*) from pg_roles where rolname in ('anon','authenticated','service_role','supabase_admin')")[1]
                == "4"
            ):
                break
        for role, lim in (("assist_spend_rpc", 10), ("assist_sweeper_rpc", 3)):
            spec = "login noinherit nosuperuser nocreatedb nocreaterole nobypassrls noreplication"
            code, _ = psql(f"create role {role} {spec} connection limit {lim} password '{PW[role]}'")
            assert code == 0
        sh("docker", "cp", str(mig), f"{NAME}:/tmp/migration.sql")
        r = sh(
            "docker",
            "exec",
            "-e",
            f"PGPASSWORD={PW['postgres']}",
            NAME,
            "psql",
            "-h",
            "127.0.0.1",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "/tmp/migration.sql",
        )
        assert r.returncode == 0, r.stderr[:300]
        assert psql("select assist.init('INITIALISE-ASSIST-SPEND')")[0] == 0
        yield mig.read_text()
    finally:
        sh("docker", "rm", "-f", "-v", NAME)


def store(role: str):
    import pg8000.dbapi as drv

    from pravrudhi.application.a0_assist import store as ST

    dsn = f"postgresql://{role}:{PW[role]}@127.0.0.1:{PORT}/postgres"

    def connect(**kw):
        kw.pop("ssl_context", None)  # a throwaway loopback container has no certificate to verify
        return drv.connect(**kw)

    return ST.PgSpendStore(dsn, timeout_s=10, connect=connect)


class Transport:
    def __init__(self):
        self.deleted = []

    def delete_session(self, sid):
        self.deleted.append(sid)
        return 204

    def interrupt(self, sid):
        return None

    def workspace_ok(self):
        return True


def cfg(tmp_path):
    from pravrudhi.application.a0_assist import config as C

    raw = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs" / "assistant_tier.yaml").write_text(yaml.safe_dump(raw))
    return C.parse(raw), tmp_path


def test_the_grants_parsed_from_the_migration_are_the_ones_the_sweepers_are_built_for(db):
    g = grants_of(db)
    assert g["assist_sweeper_rpc"] == {"list_pending", "mark_deleted", "prune", "canary", "spend_summary"}
    assert g["assist_spend_rpc"] == {
        "reserve",
        "finish",
        "abandon_stale",
        "sweep_ok",
        "status",
        "canary",
        "session_created",
        "session_deleted",
        "list_pending",
    }


def test_the_backstop_pass_with_canary_and_prune_exits_0_as_the_sweeper_role(db, tmp_path, monkeypatch):
    from pravrudhi.application.a0_assist import backstop as B
    from pravrudhi.application.a0_assist import config as C

    cfg_, root = cfg(tmp_path)
    monkeypatch.setattr(C, "config_file", lambda r, n: root / "configs" / n)
    code, out = B.run(root, {}, store=store("assist_sweeper_rpc"), transport=Transport(), canary=True, prune=True)
    assert code == 0 and out["ok"] is True and out["canary"] is True and out["alert"] is None, out


def test_the_backstop_deletes_a_session_pending_over_an_hour_and_records_it(db, tmp_path, monkeypatch):
    from pravrudhi.application.a0_assist import backstop as B
    from pravrudhi.application.a0_assist import config as C

    _, root = cfg(tmp_path)
    monkeypatch.setattr(C, "config_file", lambda r, n: root / "configs" / n)
    key = "00000000-0000-4000-8000-0000000000aa"
    eng = store("assist_spend_rpc")
    assert eng.reserve(key, 25, 400)["ok"] is True
    assert eng.session_created("sesn_it_1", key)["ok"] is True
    assert eng.session_deleted("sesn_it_1", 500)["ok"] is True  # a failed delete: pending
    assert (
        psql("update assist.session set created_at = now() - interval '3 hours' where anthropic_session_id = 'sesn_it_1'")[0] == 0
    )
    tr = Transport()
    code, out = B.run(root, {}, store=store("assist_sweeper_rpc"), transport=tr, canary=False, prune=False)
    state = psql(
        "select state || ':' || coalesce(delete_status::text, '') from assist.session where anthropic_session_id = 'sesn_it_1'"
    )[1]
    assert tr.deleted == ["sesn_it_1"] and state == "deleted:204"
    assert (
        code == 2 and "pending_past_retention" in out["alert"]
    )  # the window was breached: an alert even though this pass cleared it


def test_the_engine_sweeper_pass_uses_only_the_engine_roles_functions(db, tmp_path):
    from pravrudhi.application.a0_assist import guard as G
    from pravrudhi.application.a0_assist import sweeper as SW

    cfg_, _ = cfg(tmp_path)
    st = store("assist_spend_rpc")
    p = G.Pauses()
    out = SW.Sweeper(cfg_, st, Transport(), p, engine_store=st, mode="engine").run_once()
    assert out["error"] is None and "sweeper" not in p.snapshot()
    assert st.status()["sweep_last_ok_age_s"] is not None  # the engine marked the sweep


def test_spend_summary_runs_as_the_sweeper_role(db, tmp_path, monkeypatch):
    from pravrudhi.application.a0_assist import backstop as B
    from pravrudhi.application.a0_assist import config as C

    _, root = cfg(tmp_path)
    monkeypatch.setattr(C, "config_file", lambda r, n: root / "configs" / n)
    code, out = B.spend(root, {}, store=store("assist_sweeper_rpc"))
    assert code == 0 and out["ok"] is True and isinstance(out["spent_today_cents"], int)
