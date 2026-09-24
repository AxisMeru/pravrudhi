"""`application/tenancy.py`: orgs, memberships and org-scoped API keys (L4, `docs/decisions/LEG-PLAN-2026-09-23.md`).

Core-module tests only -- no FastAPI, no HTTP. The router-level wiring (admin gating, cross-org 403, 429,
OpenAPI) lives in `test_api_partner_tenancy.py`.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from pravrudhi.application import tenancy


class TestOrgs:
    def test_create_and_get(self, tmp_path: Path) -> None:
        org = tenancy.create_org(tmp_path, "acme-legal", "Acme Legal LLP")
        assert org.id == "acme-legal"
        assert org.name == "Acme Legal LLP"
        got = tenancy.get_org(tmp_path, "acme-legal")
        assert got == org

    def test_duplicate_org_id_refused(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        with pytest.raises(tenancy.TenancyError):
            tenancy.create_org(tmp_path, "acme", "Acme Again")

    def test_bad_id_shape_refused(self, tmp_path: Path) -> None:
        with pytest.raises(tenancy.TenancyError):
            tenancy.create_org(tmp_path, "Not Valid!", "x")

    def test_unknown_org_is_none(self, tmp_path: Path) -> None:
        assert tenancy.get_org(tmp_path, "nope") is None

    def test_list_orgs_sorted(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "zeta", "Zeta")
        tenancy.create_org(tmp_path, "alpha", "Alpha")
        assert [o.id for o in tenancy.list_orgs(tmp_path)] == ["alpha", "zeta"]


class TestMemberships:
    def test_add_and_read_back(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        tenancy.add_member(tmp_path, "acme", "user-1", "owner")
        assert tenancy.membership_role(tmp_path, "acme", "user-1") == "owner"
        assert [m.user_id for m in tenancy.memberships_for_org(tmp_path, "acme")] == ["user-1"]

    def test_unknown_role_refused(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        with pytest.raises(tenancy.TenancyError):
            tenancy.add_member(tmp_path, "acme", "user-1", "superadmin")

    def test_no_membership_is_none(self, tmp_path: Path) -> None:
        assert tenancy.membership_role(tmp_path, "acme", "nobody") is None


class TestApiKeySecrecy:
    def test_secret_is_returned_once_and_never_stored(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        created = tenancy.create_key(tmp_path, "acme", label="prod")
        assert created.secret.startswith(f"{tenancy.KEY_PREFIX}_")
        # The secret never appears anywhere on disk -- not in the record's own dict, and not in the raw
        # store file this module wrote.
        assert created.secret not in json.dumps(created.record.to_dict())
        store_text = (tenancy.tenancy_dir(tmp_path) / "keys.json").read_text()
        assert created.secret not in store_text
        assert created.record.hash in store_text  # the argon2 hash IS persisted -- that's the point

    def test_public_dict_has_no_hash_field(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        created = tenancy.create_key(tmp_path, "acme")
        assert "hash" not in created.record.to_public_dict()

    def test_create_key_for_unknown_org_refused(self, tmp_path: Path) -> None:
        with pytest.raises(tenancy.TenancyError):
            tenancy.create_key(tmp_path, "ghost-org")


class TestApiKeyVerification:
    def test_a_freshly_created_key_verifies(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        created = tenancy.create_key(tmp_path, "acme")
        record = tenancy.verify_key(tmp_path, created.secret)
        assert record.key_id == created.record.key_id
        assert record.org_id == "acme"

    def test_a_malformed_token_is_invalid(self, tmp_path: Path) -> None:
        with pytest.raises(tenancy.InvalidApiKey):
            tenancy.verify_key(tmp_path, "not-a-real-token")

    def test_an_unknown_key_id_is_invalid(self, tmp_path: Path) -> None:
        with pytest.raises(tenancy.InvalidApiKey):
            tenancy.verify_key(tmp_path, f"{tenancy.KEY_PREFIX}_doesnotexist12345_whatever-secret")

    def test_wrong_secret_for_a_real_key_id_is_invalid(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        created = tenancy.create_key(tmp_path, "acme")
        prefix, key_id, _secret = created.secret.split("_", 2)
        with pytest.raises(tenancy.InvalidApiKey):
            tenancy.verify_key(tmp_path, f"{prefix}_{key_id}_totally-wrong-secret")

    def test_a_revoked_key_no_longer_verifies(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        created = tenancy.create_key(tmp_path, "acme")
        tenancy.revoke_key(tmp_path, created.record.key_id)
        with pytest.raises(tenancy.InvalidApiKey):
            tenancy.verify_key(tmp_path, created.secret)

    def test_revoking_an_unknown_key_id_raises_tenancy_error(self, tmp_path: Path) -> None:
        with pytest.raises(tenancy.TenancyError):
            tenancy.revoke_key(tmp_path, "no-such-key")

    def test_keys_for_org_excludes_other_orgs(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        tenancy.create_org(tmp_path, "globex", "Globex")
        tenancy.create_key(tmp_path, "acme", label="a")
        tenancy.create_key(tmp_path, "globex", label="g")
        acme_keys = tenancy.keys_for_org(tmp_path, "acme")
        assert [k.label for k in acme_keys] == ["a"]


class TestScoping:
    def test_admin_always_passes(self) -> None:
        tenancy.require_org_access(None, "acme", is_admin=True)
        tenancy.require_org_access("some-other-org", "acme", is_admin=True)

    def test_matching_org_key_passes(self) -> None:
        tenancy.require_org_access("acme", "acme", is_admin=False)

    def test_mismatched_org_key_is_403(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            tenancy.require_org_access("globex", "acme", is_admin=False)
        assert exc.value.status_code == 403

    def test_no_principal_and_not_admin_is_403(self) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            tenancy.require_org_access(None, "acme", is_admin=False)
        assert exc.value.status_code == 403


class TestPrincipalFromHeaders:
    def test_no_header_is_none(self, tmp_path: Path) -> None:
        assert tenancy.principal_from_headers(tmp_path, {}) is None

    def test_valid_header_resolves_org(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        created = tenancy.create_key(tmp_path, "acme")
        principal = tenancy.principal_from_headers(tmp_path, {tenancy.API_KEY_HEADER: created.secret})
        assert principal is not None
        assert principal.org_id == "acme"
        assert principal.key_id == created.record.key_id

    def test_invalid_header_raises_401(self, tmp_path: Path) -> None:
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            tenancy.principal_from_headers(tmp_path, {tenancy.API_KEY_HEADER: "garbage"})
        assert exc.value.status_code == 401


class TestConcurrentWrites:
    def test_two_threads_creating_keys_never_lose_a_row(self, tmp_path: Path) -> None:
        tenancy.create_org(tmp_path, "acme", "Acme")
        errors: list[Exception] = []

        def make(i: int) -> None:
            try:
                tenancy.create_key(tmp_path, "acme", label=f"k{i}")
            except Exception as e:  # noqa: BLE001 - recorded, not swallowed
                errors.append(e)

        threads = [threading.Thread(target=make, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(tenancy.keys_for_org(tmp_path, "acme")) == 8


class TestKeyRateLimiter:
    def test_allows_up_to_the_per_key_limit_then_refuses(self) -> None:
        limiter = tenancy.KeyRateLimiter()
        assert limiter.allow("key-1", per_minute=2) is True
        assert limiter.allow("key-1", per_minute=2) is True
        assert limiter.allow("key-1", per_minute=2) is False

    def test_usage_total_only_counts_admitted_calls(self) -> None:
        limiter = tenancy.KeyRateLimiter()
        limiter.allow("key-1", per_minute=1)
        limiter.allow("key-1", per_minute=1)  # refused, must not count
        assert limiter.usage_total("key-1") == 1

    def test_different_keys_have_independent_budgets(self) -> None:
        limiter = tenancy.KeyRateLimiter()
        assert limiter.allow("key-1", per_minute=1) is True
        assert limiter.allow("key-2", per_minute=1) is True
