"""The spend store adapter: DSN safety, TLS, bound parameters, fail-closed errors, lazy optional driver. A fake DB-API
connection: no network, no driver needed."""

from __future__ import annotations

import ssl
import uuid
from pathlib import Path

import pytest

from pravrudhi.application.a0_assist import store as ST

PW = "p@ss/w0rd:secret"
DSN = "postgresql://assist_spend_rpc:p%40ss%2Fw0rd%3Asecret@db.example.supabase.co:5432/postgres"
KEY = str(uuid.uuid4())


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        if self.conn.fail_execute:
            raise self.conn.fail_execute
        self.conn.executed.append((sql, tuple(params)))

    def fetchall(self):
        return self.conn.rows

    def close(self):
        self.conn.cursor_closed = True


class FakeConn:
    def __init__(self, rows=None, fail_execute=None):
        self.rows, self.executed, self.closed, self.cursor_closed, self.autocommit, self.fail_execute = (
            rows or [],
            [],
            False,
            False,
            False,
            fail_execute,
        )

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True


def make(rows=None, fail_execute=None, fail_connect=None, dsn=DSN, timeout_s=3):
    conns, kwargs = [], []

    def connect(**kw):
        kwargs.append(kw)
        if fail_connect:
            raise fail_connect
        c = FakeConn(rows, fail_execute)
        conns.append(c)
        return c

    return ST.PgSpendStore(dsn, timeout_s=timeout_s, connect=connect), conns, kwargs


# ---- DSN ----------------------------------------
def test_parse_dsn_ok_with_encoded_password_and_default_port():
    p = ST.parse_dsn("postgresql://u:p%40ss@h.example/db")
    assert p == {"user": "u", "password": "p@ss", "host": "h.example", "port": 5432, "database": "db", "sslrootcert": None}
    assert ST.parse_dsn(DSN)["password"] == PW


@pytest.mark.parametrize(
    "dsn",
    [
        "",
        "mysql://u:p@h/db",
        "postgresql://h/db",
        "postgresql://u@h/db",
        "postgresql://u:p@/db",
        "postgresql://u:p@h",
        "postgresql://u:p@h/",
        "not a url",
        "postgresql://u:p@h:abc/db",
        "postgresql://u:p@h:6543/db",
        "postgresql://u:p@h:5432/db?sslmode=disable",
        "postgresql://u:p@h:5432/db?sslmode=allow",
        "postgresql://u:p@h:5432/db?sslmode=prefer",
        "postgresql://u:p@h:5432/db?sslmode=bogus",
        "postgresql://u:p@h:5432/db?sslrootcert=/no/such/file.pem",
    ],
)
def test_unsafe_or_malformed_dsn_is_refused_and_the_error_never_contains_it(dsn):
    with pytest.raises(ST.StoreNotConfigured) as e:
        ST.parse_dsn(dsn)
    assert "p@h" not in str(e.value) and "u:p" not in str(e.value)


@pytest.mark.parametrize("mode", ["require", "verify-ca", "verify-full"])
def test_strong_sslmodes_are_accepted(mode):
    assert ST.parse_dsn(f"postgresql://u:p@h:5432/db?sslmode={mode}")["host"] == "h"


def test_session_pooler_and_direct_5432_ok_but_the_transaction_pooler_port_is_refused():
    assert ST.parse_dsn("postgresql://u:p@aws-0-eu-west-1.pooler.supabase.com:5432/postgres")["port"] == 5432
    with pytest.raises(ST.StoreNotConfigured) as e:
        ST.parse_dsn("postgresql://u:p@aws-0-eu-west-1.pooler.supabase.com:6543/postgres")
    assert "transaction pooler" in str(e.value)


def test_sslrootcert_file_is_accepted_when_it_exists(tmp_path):
    f = tmp_path / "ca.pem"
    f.write_text("x")
    assert ST.parse_dsn(f"postgresql://u:p@h/db?sslrootcert={f}")["sslrootcert"] == str(f)


def test_tls_context_always_verifies_certificate_and_host():
    c = ST.tls_context(None)
    assert c.verify_mode == ssl.CERT_REQUIRED and c.check_hostname is True


# ---- the connection ----------------------------------------
def test_connect_gets_a_verifying_tls_context_a_timeout_autocommit_and_an_application_name():
    s, conns, kw = make(rows=[[{"ok": True}]])
    s.status.__func__  # noqa: B018
    s.sweep_ok()
    k = kw[0]
    assert (
        k["ssl_context"].verify_mode == ssl.CERT_REQUIRED
        and k["ssl_context"].check_hostname
        and k["timeout"] == 3.0
        and k["application_name"] == "pravrudhi-assist"
    )
    assert (k["user"], k["host"], k["port"], k["database"], k["password"]) == (
        "assist_spend_rpc",
        "db.example.supabase.co",
        5432,
        "postgres",
        PW,
    )
    assert conns[0].autocommit is True and conns[0].closed and conns[0].cursor_closed


@pytest.mark.parametrize("t", [0, -1, 31, True, "3", None])
def test_timeout_must_be_in_range(t):
    with pytest.raises(ST.StoreNotConfigured):
        ST.PgSpendStore(DSN, timeout_s=t)


def test_repr_never_shows_the_dsn_or_password():
    s, _, _ = make()
    assert PW not in repr(s) and "supabase" not in repr(s) and "redacted" in repr(s)


def test_a_connect_failure_is_a_store_error_naming_only_the_exception_class():
    s, _, _ = make(fail_connect=OSError(f"could not connect to db.example.supabase.co user assist_spend_rpc password {PW}"))
    with pytest.raises(ST.StoreError) as e:
        s.status()
    assert (
        "OSError" in str(e.value)
        and PW not in str(e.value)
        and "supabase" not in str(e.value)
        and "assist_spend_rpc" not in str(e.value)
    )


def test_an_execute_failure_is_a_store_error_and_the_connection_is_still_closed():
    s, conns, _ = make(fail_execute=TimeoutError(f"statement timeout {PW}"))
    with pytest.raises(ST.StoreError) as e:
        s.status()
    assert PW not in str(e.value) and conns[0].closed


def test_a_missing_driver_is_not_configured_and_does_not_break_import():
    s = ST.PgSpendStore(DSN, timeout_s=3)  # no injected connect: the lazy import is tried at the first call
    import importlib.util

    if importlib.util.find_spec("pg8000") is not None:
        pytest.skip("pg8000 is installed here: the missing-driver arm is covered by the monkeypatched test below")
    with pytest.raises(ST.StoreNotConfigured):
        s.status()


def test_missing_driver_via_import_block(monkeypatch):
    import builtins

    real = builtins.__import__

    def block(name, *a, **k):
        if name.startswith("pg8000"):
            raise ImportError("blocked")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", block)
    with pytest.raises(ST.StoreNotConfigured):
        ST.PgSpendStore(DSN, timeout_s=3).status()


def test_the_driver_is_imported_lazily_only_inside_the_store():
    src = Path(ST.__file__).read_text()
    top = [ln for ln in src.splitlines() if ln.startswith(("import pg8000", "from pg8000"))]
    assert top == [] and "import pg8000.dbapi as driver" in src


# ---- the calls ----------------------------------------
def test_reserve_uses_bound_parameters_and_parses_the_json_answer():
    s, conns, _ = make(rows=[[{"ok": True, "spent_cents": 0, "reserved_cents": 25}]])
    assert s.reserve(KEY, 25, 400) == {"ok": True, "spent_cents": 0, "reserved_cents": 25}
    sql, params = conns[0].executed[0]
    assert sql == "select assist.reserve(%s, %s, %s)" and params == (KEY, 25, 400) and KEY not in sql and "25" not in sql


def test_a_json_text_answer_is_parsed_too():
    s, _, _ = make(rows=[['{"ok": false, "reason": "ceiling"}']])
    assert s.reserve(KEY, 25, 400)["reason"] == "ceiling"


@pytest.mark.parametrize("rows", [[], [[None]], [["not json"]], [[[1, 2]]], [[{"surprise": 1}]], [[5]], None])
def test_malformed_answers_are_store_errors(rows):
    s, _, _ = make(rows=rows)
    with pytest.raises(ST.StoreError):
        s.reserve(KEY, 25, 400)


@pytest.mark.parametrize("key", ["", "My client's matter", "123", str(uuid.uuid1()), KEY.upper(), None, 5, KEY + " "])
def test_a_key_that_is_not_a_lower_case_uuid4_is_refused_before_any_connection(key):
    s, conns, kw = make()
    with pytest.raises(ST.StoreError):
        s.reserve(key, 25, 400)
    assert kw == []


@pytest.mark.parametrize("args", [(0, 400), (-1, 400), (True, 400), (2.5, 400), (25, 0), (25, True)])
def test_reserve_refuses_bad_integers_before_any_connection(args):
    s, _, kw = make()
    with pytest.raises(ST.StoreError):
        s.reserve(KEY, *args)
    assert kw == []


def test_finish_passes_none_for_an_unreadable_cost_and_refuses_a_negative():
    s, conns, _ = make(rows=[[{"ok": True, "charged_cents": 25}]])
    assert s.finish(KEY, None)["charged_cents"] == 25 and conns[0].executed[0] == ("select assist.finish(%s, %s)", (KEY, None))
    s.finish(KEY, 40)
    assert conns[1].executed[0][1] == (KEY, 40)
    with pytest.raises(ST.StoreError):
        s.finish(KEY, -1)
    with pytest.raises(ST.StoreError):
        s.finish(KEY, True)


def test_session_calls_accept_only_anthropic_id_charset_and_integer_status():
    s, conns, _ = make(rows=[[{"ok": True}]])
    s.session_created("sesn_01AbC-d", KEY)
    s.session_deleted("sesn_01AbC-d", 204)
    assert conns[0].executed[0] == ("select assist.session_created(%s, %s)", ("sesn_01AbC-d", KEY)) and conns[1].executed[0] == (
        "select assist.session_deleted(%s, %s)",
        ("sesn_01AbC-d", 204),
    )
    for bad in ("", "has space", "x" * 65, "My client's matter", None, "a;b"):
        with pytest.raises(ST.StoreError):
            s.session_created(bad, KEY)
    for bad in (True, 99, "200", None):
        with pytest.raises(ST.StoreError):
            s.session_deleted("sesn_1", bad)


def test_abandon_stale_status_sweep_ok_and_list_pending():
    s, conns, _ = make(rows=[[{"ok": True, "abandoned": 0}]])
    assert s.abandon_stale(30)["abandoned"] == 0 and conns[0].executed[0] == ("select assist.abandon_stale(%s)", (30,))
    with pytest.raises(ST.StoreError):
        s.abandon_stale(0)
    s2, c2, _ = make(rows=[["sesn_a", "2026-10-14T08:00:00Z", "pending_delete"], ["sesn_b", "2026-10-14T07:00:00Z", "created"]])
    assert s2.list_pending(60) == [
        {"anthropic_session_id": "sesn_a", "created_at": "2026-10-14T08:00:00Z", "state": "pending_delete"},
        {"anthropic_session_id": "sesn_b", "created_at": "2026-10-14T07:00:00Z", "state": "created"},
    ]
    assert c2[0].executed[0][0] == "select anthropic_session_id, created_at, state from assist.list_pending(%s)"
    with pytest.raises(ST.StoreError):
        make(rows=[["only-one"]])[0].list_pending(10)
    with pytest.raises(ST.StoreError):
        s2.list_pending(-1)


def test_no_call_builds_sql_from_values_and_no_prepared_statement_is_created():
    src = Path(ST.__file__).read_text()
    assert (
        "PREPARE" not in src.upper().replace("PREPARED", "")
        and 'f"select' not in src
        and "f'select" not in src
        and ".format(" not in src
    )
    s, conns, _ = make(rows=[[{"ok": True}]])
    s.sweep_ok()
    s.reserve(KEY, 1, 1)
    assert all("%s" in sql or "()" in sql for c in conns for sql, _ in c.executed)


def test_new_session_key_is_a_lower_case_uuid4():
    k = ST.new_session_key()
    assert str(uuid.UUID(k)) == k and uuid.UUID(k).version == 4 and ST.new_session_key() != k


# ---- from_env ----------------------------------------
def test_from_env_unset_empty_or_refused_dsn_is_not_configured():
    for env in ({}, {"X": ""}, {"X": "  "}, {"X": "postgresql://u:p@h:6543/db"}, {"X": "garbage"}):
        assert ST.from_env(env, "X", timeout_s=3, connect=lambda **k: None) == (None, "not_configured")


def test_from_env_with_a_valid_dsn_and_driver_returns_a_store():
    s, why = ST.from_env({"X": DSN}, "X", timeout_s=3, connect=lambda **k: None)
    assert isinstance(s, ST.PgSpendStore) and why is None


def test_from_env_without_the_driver_is_not_configured(monkeypatch):
    import importlib.util

    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None)
    assert ST.from_env({"X": DSN}, "X", timeout_s=3) == (None, "not_configured")


def test_a_ca_file_overrides_and_must_exist(tmp_path, monkeypatch):
    f = tmp_path / "ca.crt"
    f.write_text("x")
    used = []
    monkeypatch.setattr(ST, "tls_context", lambda root: used.append(root) or ssl.create_default_context())
    s2 = ST.PgSpendStore(DSN, timeout_s=3, connect=lambda **k: FakeConn([[{"ok": True}]]), ca_file=str(f))
    s2.sweep_ok()
    assert s2._p["sslrootcert"] == str(f) and used == [str(f)]
    for bad in (str(tmp_path / "nope.crt"), 5, ""):
        with pytest.raises(ST.StoreNotConfigured):
            ST.PgSpendStore(DSN, timeout_s=3, ca_file=bad)


def test_from_env_passes_the_ca_file_through(tmp_path):
    f = tmp_path / "ca.crt"
    f.write_text("x")
    s, why = ST.from_env({"X": DSN}, "X", timeout_s=3, connect=lambda **k: None, ca_file=str(f))
    assert why is None and s._p["sslrootcert"] == str(f)
    assert ST.from_env({"X": DSN}, "X", timeout_s=3, connect=lambda **k: None, ca_file=str(tmp_path / "gone")) == (
        None,
        "not_configured",
    )
