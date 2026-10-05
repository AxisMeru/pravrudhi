"""Fail-closed auth and edition (R1/Lead-2, from the fail-open audit).

`PRAVRUDHI_AUTH` set to an unrecognised value ("requried", "off", "true") used to mean `disabled`, which makes every
anonymous caller the operator. It is now `required` and the boot refuses it. A hosted image must name its auth mode (an
unset value means `disabled` only on a local install), an unrecognised `PRAVRUDHI_HOSTED_IMAGE` refuses to start so a typo
cannot reopen that path, and one `resolved_edition()` decides the edition for the routes, the Studio gate and the vendor
carve-out."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pravrudhi import deployment
from pravrudhi.api import edition, identity, roles
from pravrudhi.api.identity import AuthMode, auth_mode
from pravrudhi.api.server import create_app
from pravrudhi.application import tenant_vendors
from pravrudhi.deployment import DeploymentConfigError, hosted_image, hosted_marker_or_raise, resolved_edition, validate

ENV = ("PRAVRUDHI_AUTH", "PRAVRUDHI_EDITION", "PRAVRUDHI_HOSTED_IMAGE", "PRAVRUDHI_DISABLE_LOCAL_GUARD", "SUPABASE_URL",
       "VERCEL", "RENDER")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for v in ENV:
        monkeypatch.delenv(v, raising=False)
    real = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: False if str(self) == "/.dockerenv" else real(self))


def _container(monkeypatch: pytest.MonkeyPatch) -> None:
    real = Path.exists
    monkeypatch.setattr(Path, "exists", lambda self: True if str(self) == "/.dockerenv" else real(self))


# -- auth_mode ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["requried", "off", "true", "yes", "1", "enabled", "none", "require", "disable", "x"])
def test_an_unrecognised_auth_value_is_required_never_disabled(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", value)
    assert auth_mode() is AuthMode.REQUIRED


@pytest.mark.parametrize("value", ["requried", "off", "true", "Required2"])
def test_the_anonymous_caller_is_not_admin_for_an_unrecognised_auth_value(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", value)
    monkeypatch.setenv("PRAVRUDHI_ADMINS", "op-1")
    assert roles.role_of(None) is roles.USER and not roles.is_admin(None)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, AuthMode.DISABLED), ("", AuthMode.DISABLED), ("  ", AuthMode.DISABLED), ("disabled", AuthMode.DISABLED),
     ("Disabled", AuthMode.DISABLED), ("optional", AuthMode.OPTIONAL), ("required", AuthMode.REQUIRED),
     (" Required ", AuthMode.REQUIRED)],
)
def test_the_documented_values_and_unset_keep_their_meaning_on_a_local_install(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: AuthMode
) -> None:
    if value is not None:
        monkeypatch.setenv("PRAVRUDHI_AUTH", value)
    assert auth_mode() is expected
    validate()  # a local install accepts every one of them, unset included


def test_the_anonymous_local_operator_is_still_admin_with_auth_unset_or_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    assert roles.role_of(None) is roles.ADMIN
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")
    assert roles.role_of(None) is roles.ADMIN


# -- the boot refuses --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["requried", "off", "true", "1", "x"])
def test_validate_and_guard_boot_refuse_an_unrecognised_auth_value(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_AUTH", value)
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    with pytest.raises(DeploymentConfigError, match="PRAVRUDHI_AUTH"):
        validate()
    with pytest.raises(DeploymentConfigError):
        identity.guard_boot()


@pytest.mark.parametrize("value", ["prod", "Studi0", "staging", "both", "null"])
def test_the_boot_refuses_an_unrecognised_edition(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", value)
    with pytest.raises(DeploymentConfigError, match="PRAVRUDHI_EDITION"):
        validate()
    with pytest.raises(DeploymentConfigError):
        identity.guard_boot()
    with pytest.raises(DeploymentConfigError):
        resolved_edition()


@pytest.mark.parametrize("value", ["studio", "product", "dev", "Studio", " PRODUCT "])
def test_the_recognised_editions_boot(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_EDITION", value)
    validate()


# -- a hosted image must name its auth mode ----------------------------------------------------------------------------


def test_a_hosted_image_with_auth_unset_or_blank_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_HOSTED_IMAGE", "1")
    with pytest.raises(DeploymentConfigError, match="unset on a hosted image"):
        validate()
    with pytest.raises(DeploymentConfigError):
        identity.guard_boot()
    monkeypatch.setenv("PRAVRUDHI_AUTH", "")
    with pytest.raises(DeploymentConfigError, match="unset on a hosted image"):
        validate()


def test_an_older_hosted_image_without_the_marker_is_recognised_by_its_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _container(monkeypatch)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    with pytest.raises(DeploymentConfigError, match="unset on a hosted image"):
        validate()


@pytest.mark.parametrize("auth", ["required", "optional", "disabled"])
def test_a_hosted_image_that_names_its_auth_mode_starts(monkeypatch: pytest.MonkeyPatch, auth: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_HOSTED_IMAGE", "1")
    monkeypatch.setenv("PRAVRUDHI_AUTH", auth)
    validate()  # (an explicit `disabled` is the operator's decision; the Studio loopback guard still applies to it)


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE"])
def test_an_explicit_false_marker_keeps_local_semantics_even_in_a_container_with_the_guard_off(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    _container(monkeypatch)
    monkeypatch.setenv("PRAVRUDHI_DISABLE_LOCAL_GUARD", "1")
    monkeypatch.setenv("PRAVRUDHI_HOSTED_IMAGE", value)
    validate()  # AUTH unset is fine: this is a local container that said so
    assert auth_mode() is AuthMode.DISABLED and not hosted_image()


@pytest.mark.parametrize("value", ["yess", "treu", "2", "hosted", "enabled"])
def test_an_unrecognised_hosted_marker_refuses_to_start_whatever_auth_says(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PRAVRUDHI_HOSTED_IMAGE", value)
    with pytest.raises(DeploymentConfigError, match="PRAVRUDHI_HOSTED_IMAGE"):
        hosted_marker_or_raise()
    with pytest.raises(DeploymentConfigError):
        validate()  # AUTH unset
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    with pytest.raises(DeploymentConfigError):
        validate()  # and even with AUTH named: a mislabelled marker is never guessed at


@pytest.mark.parametrize("value", ["yess", "treu", "2"])
def test_the_display_label_keeps_its_lenient_meaning_for_the_same_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("PRAVRUDHI_HOSTED_IMAGE", value)
    assert not hosted_image()  # wording only: unrecognised is "not hosted" (the strict check is the boot's)


# -- one resolved edition -----------------------------------------------------------------------------------------------


def test_an_unlabelled_hosted_image_is_the_product_everywhere(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRAVRUDHI_HOSTED_IMAGE", "1")
    monkeypatch.setenv("PRAVRUDHI_AUTH", "required")
    assert resolved_edition() == "product"
    assert edition.engine_edition() == edition.PRODUCT and not edition.is_studio_engine()
    assert not roles._studio_edition() and not tenant_vendors.is_studio_edition()


def test_an_unlabelled_hosted_image_does_not_serve_the_studio_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRAVRUDHI_HOSTED_IMAGE", "1")
    monkeypatch.setenv("PRAVRUDHI_AUTH", "disabled")  # local-style caller: would be admin by construction
    client = TestClient(create_app(tmp_path), base_url="http://localhost", raise_server_exceptions=False)
    assert client.get("/api/agents").status_code == 404  # an admin-only Studio route does not exist on the product


def test_every_reader_agrees_for_each_edition_value(monkeypatch: pytest.MonkeyPatch) -> None:
    for declared, studio in (("studio", True), ("product", False), ("dev", False)):
        monkeypatch.setenv("PRAVRUDHI_EDITION", declared)
        assert (resolved_edition() == "studio") is studio
        assert roles._studio_edition() is studio
        assert (edition.engine_edition() == edition.STUDIO) is (declared in ("studio", "dev"))
        assert tenant_vendors.resolved_edition is deployment.resolved_edition


def test_resolution_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolved_edition() == "dev"  # an unlabelled development checkout
    monkeypatch.setenv("PRAVRUDHI_EDITION", "dev")
    assert resolved_edition() == "dev"
    monkeypatch.setenv("PRAVRUDHI_HOSTED_IMAGE", "1")
    assert resolved_edition() == "product"  # dev is ignored on a hosted image
    monkeypatch.delenv("PRAVRUDHI_HOSTED_IMAGE")
    monkeypatch.setattr(deployment, "is_release_install", lambda: True)
    assert resolved_edition() == "product"  # and on a release install
    monkeypatch.setenv("PRAVRUDHI_EDITION", "studio")
    assert resolved_edition() == "studio"  # a deliberate Studio on a release install stays Studio
