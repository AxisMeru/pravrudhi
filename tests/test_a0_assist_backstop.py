"""The backstop sweeper process: sweeper role only, min age 60, one JSON line, exit codes, alerts, no values printed."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from pravrudhi.application.a0_assist import backstop as B
from pravrudhi.application.a0_assist import store as ST
from tests.test_a0_assist_sweeper import FakeStore, FakeTransport

REPO = Path(__file__).resolve().parent.parent
NOW = 1_700_000_000.0
MARKER = "sweeper-marker-77"


def iso(age_s):
    return datetime.fromtimestamp(NOW - age_s, UTC)


def root_with(tmp_path):
    cfg = yaml.safe_load((REPO / "configs" / "assistant_tier.yaml").read_text())
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs" / "assistant_tier.yaml").write_text(yaml.safe_dump(cfg))
    return tmp_path


@pytest.fixture(autouse=True)
def _packaged(monkeypatch):
    import pravrudhi.application.config_files as CF

    monkeypatch.setattr(CF, "PACKAGED_CONFIG_DIR", Path("/nonexistent"))


class Store(FakeStore):
    def __init__(self, rows=(), **kw):
        super().__init__(**kw)
        self.rows, self.asked = list(rows), []

    def list_pending(self, n):
        self._do("list_pending")
        self.asked.append(n)
        return [{"anthropic_session_id": i, "created_at": c, "state": "pending_delete"} for i, c in self.rows]


def go(tmp_path, store, tr, **kw):
    return B.run(
        root_with(tmp_path), {}, store=store, transport=tr, now=NOW, canary=kw.get("canary", False), prune=kw.get("prune", False)
    )


def test_a_healthy_pass_deletes_records_with_mark_deleted_and_exits_0(tmp_path):
    st, tr = Store([("sesn_a", iso(4000)), ("sesn_b", iso(3700))]), FakeTransport()
    code, out = go(tmp_path, st, tr)
    assert code == 0 and out["ok"] is True and out["deleted"] == 2 and out["alert"] is None
    assert st.marked == [("sesn_a", 204), ("sesn_b", 204)] and "session_deleted" not in st.calls


def test_only_sessions_pending_more_than_an_hour_are_asked_for(tmp_path):
    st = Store()
    go(tmp_path, st, FakeTransport())
    assert set(st.asked) == {B.BACKSTOP_MIN_AGE_MIN} == {60}


def test_a_failed_delete_is_an_alert_with_exit_2_and_the_row_stays_pending(tmp_path):
    st, tr = Store([("sesn_a", iso(4000))]), FakeTransport({"sesn_a": 500})
    code, out = go(tmp_path, st, tr)
    assert code == 2 and "delete_failed" in out["alert"] and st.marked == []


def test_a_session_pending_past_the_retention_window_is_an_alert_even_when_this_pass_clears_it(tmp_path):
    cfg_hours = 2
    code, out = go(tmp_path, Store([("sesn_a", iso(cfg_hours * 3600 + 5))]), FakeTransport())
    assert code == 2 and "pending_past_retention" in out["alert"] and out["deleted"] == 1


def test_a_row_just_inside_the_window_is_not_an_alert(tmp_path):
    code, out = go(tmp_path, Store([("sesn_a", iso(2 * 3600 - 1))]), FakeTransport())
    assert code == 0 and out["alert"] is None


@pytest.mark.parametrize("created", [None, "garbage", 5, datetime(2023, 1, 1)])
def test_an_unreadable_created_time_never_crashes_the_pass(tmp_path, created):
    code, out = go(tmp_path, Store([("sesn_a", created)]), FakeTransport())
    assert code == 0 and out["deleted"] == 1


def test_a_store_error_is_an_alert(tmp_path):
    code, out = go(tmp_path, Store(fail={"list_pending": 1}), FakeTransport())
    assert code == 2 and "store" in out["alert"]


def test_the_canary_runs_only_with_the_flag_and_a_failure_or_error_is_an_alert(tmp_path):
    st = Store()
    go(tmp_path, st, FakeTransport())
    assert "canary" not in st.calls
    code, out = go(tmp_path, Store(canary={"ok": True, "bad_functions": 0}), FakeTransport(), canary=True)
    assert code == 0 and out["canary"] is True
    code, out = go(tmp_path, Store(canary={"ok": False, "bad_functions": 1}), FakeTransport(), canary=True)
    assert code == 2 and out["alert"] == "canary_failed" and out["canary"] is False
    code, out = go(tmp_path, Store(fail={"canary": 1}), FakeTransport(), canary=True)
    assert code == 2 and "canary_error" in out["alert"]


def test_the_prune_runs_only_with_the_flag(tmp_path):
    st = Store()
    go(tmp_path, st, FakeTransport())
    assert "prune" not in st.calls
    st2 = Store()
    code, out = go(tmp_path, st2, FakeTransport(), prune=True)
    assert "prune" in st2.calls and out["pruned"] == 0


def test_the_output_has_counts_and_flags_only_never_an_id(tmp_path):
    _, out = go(tmp_path, Store([("sesn_secretid", iso(4000))]), FakeTransport())
    assert set(out) == {"ok", "alert", "pending", "deleted", "failed", "canary", "pruned"} and "sesn_secretid" not in json.dumps(
        out
    )


def test_no_store_or_no_key_is_exit_3(tmp_path):
    assert B.run(root_with(tmp_path), {}, canary=False, prune=False)[0] == 3
    code, out = B.run(root_with(tmp_path), {B.KEY_ENV: MARKER}, store=Store(), canary=False, prune=False)
    assert code == 3 and out["alert"] == "not_configured"


def test_an_unreadable_config_is_exit_3(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "assistant_tier.yaml").write_text("- not a mapping")
    assert B.run(tmp_path, {}, store=Store(), transport=FakeTransport(), canary=False, prune=False)[0] == 3


def test_main_prints_one_json_line_and_returns_the_exit_code_and_never_the_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(B.KEY_ENV, MARKER)
    monkeypatch.setenv("SUPABASE_ASSIST_DB_URL_SWEEPER", "postgresql://u:pw-marker-88@h:5432/d")
    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (None, "not_configured"))
    code = B.main(["--root", str(root_with(tmp_path))])
    cap = capsys.readouterr()
    assert code == 3 and len(cap.out.strip().splitlines()) == 1 and json.loads(cap.out)["ok"] is False
    assert MARKER not in cap.out + cap.err and "pw-marker-88" not in cap.out + cap.err


def test_the_module_never_reads_the_engine_dsn_or_the_engine_key():
    src = Path(B.__file__).read_text()
    assert "store_dsn_env" not in src and "key_env_names" not in src


def test_a_canary_timeout_is_its_own_alert_not_a_generic_error(tmp_path, caplog):
    class Slow(Store):
        def canary(self):
            self.calls.append("canary")
            raise ST.StoreTimeout("t")

    with caplog.at_level("WARNING"):
        code, out = go(tmp_path, Slow(), FakeTransport(), canary=True)
    assert code == 2 and "canary_timeout" in out["alert"] and "canary_error" not in out["alert"]
    assert "timed out" in caplog.text


# ---- --spend: the read-only totals for the daily billing close -------------------------------------------------------------
class SpendStore(Store):
    def __init__(self, doc=None, **kw):
        super().__init__(**kw)
        self.doc = doc

    def spend_summary(self):
        self._do("spend_summary")
        return self.doc


GOOD = {
    "day": "2026-10-14",
    "month_start": "2026-10-01",
    "spent_today_cents": 40,
    "spent_month_cents": 65,
    "reserved_open_cents": 25,
}


def test_spend_prints_only_the_totals_and_exits_0(tmp_path):
    code, out = B.spend(root_with(tmp_path), {}, store=SpendStore({**GOOD, "extra_secret": "x"}))
    assert code == 0 and out == {"ok": True, **GOOD}


def test_spend_needs_no_anthropic_key_no_transport_and_makes_no_write_call(tmp_path):
    st = SpendStore(GOOD)
    B.spend(root_with(tmp_path), {}, store=st)
    assert st.calls == ["spend_summary"]


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {**GOOD, "spent_today_cents": -1},
        {**GOOD, "spent_month_cents": True},
        {**GOOD, "spent_today_cents": "40"},
        {**GOOD, "day": None},
        None,
    ],
)
def test_a_malformed_answer_is_an_alert_not_a_number(tmp_path, bad):
    code, out = B.spend(root_with(tmp_path), {}, store=SpendStore(bad if bad is not None else {}))
    assert code == 2 and out["ok"] is False and out["alert"] == "unexpected_shape"


def test_a_store_error_is_an_alert_and_no_store_is_exit_3(tmp_path):
    assert B.spend(root_with(tmp_path), {}, store=SpendStore(GOOD, fail={"spend_summary": 1}))[0] == 2
    assert B.spend(root_with(tmp_path), {})[0] == 3


def test_main_spend_prints_one_json_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ST, "from_env", lambda *a, **k: (SpendStore(GOOD), None))
    code = B.main(["--root", str(root_with(tmp_path)), "--spend"])
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0 and len(out) == 1 and json.loads(out[0])["spent_month_cents"] == 65
