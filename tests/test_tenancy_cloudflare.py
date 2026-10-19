"""#1018 step 1: the engine side of the Cloudflare tenancy store (the Worker is faked): the same 16 operations as PgTenancyStore;
retry only for idempotent operations; fail closed to TenancyStoreDown; https only; no secret in repr or errors. Invented data."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from pravrudhi.application import tenancy_cloudflare as C
from pravrudhi.application import tenancy_store as T


class Fake:
    """A scripted Worker: records (op, body); replies per op: a value, an exception, or a tuple of replies in order."""

    def __init__(self, replies: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.replies = replies or {}

    def _seq(self, op: str, r: tuple[Any, ...]) -> Any:
        n = sum(1 for c in self.calls if c[0] == op) - 1
        return r[min(n, len(r) - 1)]

    def __call__(self, op: str, body: dict[str, Any]) -> Any:
        self.calls.append((op, body))
        r = self.replies.get(op, {"ok": True})
        if isinstance(r, tuple):  # a sequence of replies, consumed in order
            r = list(r).pop(0) if False else self._seq(op, r)
        if isinstance(r, Exception):
            raise r
        return r


def store(fake: Fake) -> C.CloudflareTenancyStore:
    return C.CloudflareTenancyStore("https://tenancy.example.test", "tok-secret-value", transport=fake)


def test_it_has_exactly_the_same_public_operations_and_signatures_as_the_postgres_store():
    for op in C.OPERATIONS:
        a, b = inspect.signature(getattr(C.CloudflareTenancyStore, op)), inspect.signature(getattr(T.PgTenancyStore, op))
        assert list(a.parameters) == list(b.parameters), op
    assert set(C.OPERATIONS) == {n for n in dir(T.PgTenancyStore) if not n.startswith("_") and n not in ("close", "clone")}


def test_each_operation_sends_its_name_named_args_and_a_request_id_and_maps_the_result():
    f = Fake({"get_org": {"org_id": "o1", "name": "N"}, "list_orgs": [{"org_id": "o1"}], "usage_counts": {"calls": 3,
    "failed": 1},
              "audit_page": {"rows": [{"run_id": "r"}], "total": 7}, "canary": {"ok": True}})  # fmt: skip
    s = store(f)
    assert s.create_org("o1", "N", "2026-10-19T00:00:00.000000Z") == {"ok": True}
    assert s.get_org("o1") == {"org_id": "o1", "name": "N"} and s.list_orgs() == [{"org_id": "o1"}]
    assert s.usage_counts("k") == (3, 1) and s.audit_page("k", 0, 10, 60) == ([{"run_id": "r"}], 7) and s.canary() == {"ok": True}
    s.create_key({"key_id": "k", "org_id": "o1", "label": "l", "hash": "h", "created": "t", "rate_limit_per_minute": 5})
    s.audit_record(
        {
            "ts": "t",
            "key_id": "k",
            "mode": "intake",
            "status_code": 200,
            "run_id": "r",
            "contract_ids": ("a",),
            "outcomes": {"x": 1},
        },
        3600,
    )
    names = [c[0] for c in f.calls]
    assert names[0] == "create_org" and f.calls[0][1]["args"] == {
        "org_id": "o1",
        "name": "N",
        "created": "2026-10-19T00:00:00.000000Z",
    }
    assert all(len(c[1]["request_id"]) == 32 for c in f.calls)
    assert f.calls[-1][1]["args"]["contract_ids"] == ["a"] and f.calls[-1][1]["args"]["retention_s"] == 3600


def test_a_missing_org_is_none_and_a_bad_shape_is_store_down():
    s = store(Fake({"get_org": None, "usage_counts": ["bad"], "audit_page": {"rows": "x"}, "canary": {"nope": 1}}))
    assert s.get_org("zz") is None
    for call in (lambda: s.usage_counts("k"), lambda: s.audit_page("k", 0, 1, 1), lambda: s.canary()):
        with pytest.raises(T.TenancyStoreDown):
            call()


def test_only_idempotent_operations_are_retried_once_and_a_retry_keeps_the_request_id():
    f = Fake({"get_key": (OSError("reset"), {"key_id": "k"}), "record_usage": (OSError("lost reply"), {"ok": True})})
    s = store(f)
    assert s.get_key("k") == {"key_id": "k"} and [c[0] for c in f.calls] == ["get_key", "get_key"]
    assert f.calls[0][1]["request_id"] == f.calls[1][1]["request_id"]
    with pytest.raises(T.TenancyStoreDown):
        s.record_usage("k", "2026-10-19", False)  # a write whose reply was lost must not run twice
    assert [c[0] for c in f.calls].count("record_usage") == 1


def test_two_failures_in_a_row_fail_closed_and_the_error_carries_no_url_token_or_text():
    f = Fake({"get_org": OSError("https://tenancy.example.test tok-secret-value boom")})
    s = store(f)
    with pytest.raises(T.TenancyStoreDown) as e:
        s.get_org("o1")
    assert "tok-secret-value" not in str(e.value) and "tenancy.example.test" not in str(e.value) and "boom" not in str(e.value)
    assert "tok-secret-value" not in repr(s) and "tenancy.example.test" not in repr(s)
    assert [c[0] for c in f.calls] == ["get_org", "get_org"]


def test_the_worker_must_be_https_except_on_this_machine_and_needs_a_token():
    for bad in ("http://t.example.test", "t.example.test", "ftp://x", ""):
        with pytest.raises(T.TenancyStoreDown):
            C.CloudflareTenancyStore(bad, "t")
    with pytest.raises(T.TenancyStoreDown):
        C.CloudflareTenancyStore("https://t.example.test", "")
    assert C.CloudflareTenancyStore("http://localhost:8787", "t") and C.CloudflareTenancyStore("https://t.example.test", "t")


def test_from_env_is_none_unless_both_the_url_and_the_token_are_set_and_names_only_the_environment():
    assert (
        C.from_env({}) is None
        and C.from_env({C.URL_ENV: "https://t.example.test"}) is None
        and C.from_env({C.TOKEN_ENV: "x"}) is None
    )
    assert C.from_env({C.URL_ENV: "https://t.example.test", C.TOKEN_ENV: "x"}, transport=Fake()) is not None


def test_an_unknown_operation_is_refused():
    with pytest.raises(T.TenancyStoreDown):
        store(Fake())._call("drop_table")


def test_a_redirect_is_never_followed_and_userinfo_or_a_padded_token_is_handled():
    import urllib.request

    assert C._OPENER.handlers and any(isinstance(h, C._NoRedirect) for h in C._OPENER.handlers)
    req = urllib.request.Request("https://a.example.test")
    assert C._NoRedirect().redirect_request(req, None, 302, "x", {}, "https://b.example.test") is None
    with pytest.raises(T.TenancyStoreDown):
        C.CloudflareTenancyStore("https://user:pw@t.example.test", "t")
    with pytest.raises(T.TenancyStoreDown):
        C.CloudflareTenancyStore("https://user@t.example.test", "t")
    s = C.from_env({C.URL_ENV: "https://t.example.test", C.TOKEN_ENV: "  tok \n"}, transport=Fake())
    assert s is not None and s._token == "tok"


def test_the_cloudflare_backend_is_selected_by_the_env_and_never_falls_back(monkeypatch: pytest.MonkeyPatch):
    from pravrudhi.application import tenancy

    monkeypatch.setattr(tenancy, "_pg_cache", {})
    monkeypatch.setenv(tenancy.BACKEND_ENV, "cloudflare")
    for k in (C.URL_ENV, C.TOKEN_ENV, "SUPABASE_TENANCY_DB_URL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(tenancy.TenancyUnavailable):  # selected but not configured: 503, not the files, not Supabase
        tenancy._pg()
    monkeypatch.setenv(C.URL_ENV, "https://t.example.test")
    monkeypatch.setenv(C.TOKEN_ENV, "tok")
    s = tenancy._pg()
    assert isinstance(s, C.CloudflareTenancyStore) and tenancy._pg() is s  # one store per Worker URL
    assert tenancy._backend_is_supabase() is True  # hosted
    monkeypatch.setenv(tenancy.BACKEND_ENV, "cloudflair")  # a typo fails closed
    with pytest.raises(tenancy.TenancyUnavailable):
        tenancy._pg()
    monkeypatch.setenv(tenancy.BACKEND_ENV, "file")
    assert tenancy._pg() is None
