# ruff: noqa: F811
"""R2's post-#454 survivors, test-only: (a) a reader that fills the key cache between the revoke's pre-forget and its commit must
not leave the revoked key accepted (only the POST-call forget in `revoke_key` covers it); (b) the TenancyStoreDown guard of
`create_key`, reached only after `get_org` has answered, so a route never gets there with the store down."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from pravrudhi.application import tenancy
from pravrudhi.application.tenancy_store import TenancyStoreDown
from tests.test_tenancy_supabase_backend import sb  # noqa: F401  (the fixture)


def test_a_reader_filling_the_cache_before_the_revoke_commits_does_not_keep_the_key_alive(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    real = store.revoke_key
    filled: list[bool] = []

    def racy(key_id: str, at: str) -> dict[str, Any]:
        # a concurrent request authenticates NOW: the database has not committed the revoke, so it reads (and caches) the live row
        filled.append(tenancy.verify_key(root, k.secret).key_id == key_id)
        assert key_id in tenancy._key_cache  # the racing reader really wrote the cache (the mutant would keep this entry)
        return real(key_id, at)

    store.revoke_key = racy  # type: ignore[method-assign]
    tenancy.revoke_key(root, k.record.key_id)
    assert filled == [True]  # the racing reader did fill the cache after the pre-forget
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, k.secret)  # only the post-call forget removes the entry the reader just wrote


def test_the_same_race_with_a_real_reader_thread(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    real = store.revoke_key
    reader_done = threading.Event()
    in_revoke = threading.Event()

    def racy(key_id: str, at: str) -> dict[str, Any]:
        in_revoke.set()
        assert reader_done.wait(5)  # hold the commit until the reader has cached the live row
        return real(key_id, at)

    store.revoke_key = racy  # type: ignore[method-assign]

    errors: list[BaseException] = []

    def reader() -> None:
        try:
            assert in_revoke.wait(5)
            tenancy.verify_key(root, k.secret)
            assert k.record.key_id in tenancy._key_cache
        except BaseException as exc:  # noqa: BLE001 - surfaced in the main thread below
            errors.append(exc)
        finally:
            reader_done.set()

    t = threading.Thread(target=reader)
    t.start()
    tenancy.revoke_key(root, k.record.key_id)
    t.join(5)
    assert not t.is_alive() and errors == []
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, k.secret)


def test_create_key_reports_a_store_failure_after_the_org_check_as_503_and_writes_no_file(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")  # get_org answers, then create_key's own store call fails
    store.create_key = lambda row: (_ for _ in ()).throw(TenancyStoreDown("down"))  # type: ignore[method-assign]
    with pytest.raises(tenancy.TenancyUnavailable) as e:
        tenancy.create_key(root, "acme", label="x")
    assert e.value.status_code == 503 and e.value.detail == "key service unavailable"
    assert not (root / ".pravrudhi" / "tenancy").exists()


def test_create_key_refused_by_the_database_is_a_tenancy_error_not_a_503(sb: Any) -> None:
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    store.create_key = lambda row: {"ok": False, "reason": "bad_args"}  # type: ignore[method-assign]
    with pytest.raises(tenancy.TenancyError):
        tenancy.create_key(root, "acme")


def test_a_reader_that_fetched_the_live_row_before_the_revoke_and_writes_it_after_does_not_cache_it(sb: Any) -> None:
    """The case the epoch guard exists for: the row is read BEFORE the forget and the cache write happens AFTER it."""
    store, root = sb
    tenancy.create_org(root, "acme", "Acme")
    k = tenancy.create_key(root, "acme")
    tenancy.reset_backend_for_tests()  # a cold cache
    real_get = store.get_key
    fetched = threading.Event()
    release = threading.Event()
    seen: list[Any] = []

    def slow_get(key_id: str) -> Any:
        row = real_get(key_id)  # the LIVE row, read before the revoke
        fetched.set()
        assert release.wait(5)
        return row

    store.get_key = slow_get  # type: ignore[method-assign]
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            seen.append(tenancy.verify_key(root, k.secret).key_id)  # accepted: it holds the live row
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=reader)
    t.start()
    assert fetched.wait(5)
    store.get_key = real_get  # type: ignore[method-assign]  # later reads are fast
    tenancy.revoke_key(root, k.record.key_id)  # forgets and bumps the epoch while the reader still holds the old row
    release.set()
    t.join(5)
    assert not t.is_alive() and errors == [] and seen == [k.record.key_id]
    assert k.record.key_id not in tenancy._key_cache  # the stale row was NOT cached after the revoke
    with pytest.raises(tenancy.InvalidApiKey):
        tenancy.verify_key(root, k.secret)
