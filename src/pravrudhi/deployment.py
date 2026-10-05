"""What this process is deployed as: one place that reads `PRAVRUDHI_EDITION`, `PRAVRUDHI_AUTH` and the hosted-image
marker, so the engine's edition, its whole-surface Studio gate and the tenant-vendor carve-out can never disagree about it.

No imports from the rest of the package: this is read at boot, before anything that depends on it.

Fail closed. An unrecognised value is never mapped to the permissive meaning: `PRAVRUDHI_AUTH` set to anything but the
documented values is `required`, and the boot refuses it outright; an unrecognised `PRAVRUDHI_EDITION` refuses to
start. Only a LOCAL install may leave `PRAVRUDHI_AUTH` unset (meaning `disabled`, the single-operator machine); a
hosted image must name it.
"""

from __future__ import annotations

import os
from pathlib import Path

EDITION_ENV = "PRAVRUDHI_EDITION"
AUTH_ENV = "PRAVRUDHI_AUTH"
HOSTED_IMAGE_ENV = "PRAVRUDHI_HOSTED_IMAGE"
RELEASE_MARKER = ".pravrudhi/releases/"
HOSTED_FILE = Path("/etc/pravrudhi/hosted-image")
"""Baked into the hosted image by the Dockerfile. Unlike an environment variable it cannot be switched off by the
deployment's own environment (`PRAVRUDHI_HOSTED_IMAGE=0`, blank, or missing), and it does not depend on `/.dockerenv`
(absent on containerd, Kubernetes and RunPod): its presence IS the proof that this is the hosted image."""

EDITIONS = frozenset({"studio", "product", "dev"})
AUTH_MODES = frozenset({"disabled", "optional", "required"})
_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


class DeploymentConfigError(RuntimeError):
    """The environment names a deployment this engine will not guess at."""


def hosted_image() -> bool:
    """The DISPLAY (and edition-resolution) answer: True inside the hosted engine image, which ships no agent CLIs by
    design (the coding agents run on the host loop).

    `PRAVRUDHI_HOSTED_IMAGE` decides when it says anything: a true value (the image sets `1`) is hosted and an explicit
    false value (`0`, `false`, `no`, `off`) is NOT, whatever else is set, so a local container can always opt out. An
    unset or empty marker falls back to the older images' defaults: a container (`/.dockerenv`) with
    `PRAVRUDHI_DISABLE_LOCAL_GUARD=1`, which the hosted image sets and a local install does not. Any other value is
    not trusted as a label and counts as not hosted."""
    if HOSTED_FILE.exists():
        return True
    marker = os.environ.get(HOSTED_IMAGE_ENV, "").strip().lower()
    if marker in _TRUE:
        return True
    if marker:
        return False
    return os.environ.get("PRAVRUDHI_DISABLE_LOCAL_GUARD", "").strip() == "1" and Path("/.dockerenv").exists()


def hosted_marker_or_raise() -> bool:
    """The strict answer for the boot and auth path: is this the hosted image?

    Decided by the baked-in file (`HOSTED_FILE`), which wins over everything: when it exists this is the hosted image
    whatever the environment says (`PRAVRUDHI_HOSTED_IMAGE=0`, blank or garbage cannot switch the guard off). Without
    the file, a true `PRAVRUDHI_HOSTED_IMAGE` (an operator asking for the strict rules) is hosted. An unrecognised
    value raises `DeploymentConfigError`, so a typo in the marker (`yess`, `treu`, `2`) cannot quietly reopen the
    "unset PRAVRUDHI_AUTH means disabled" path. The older-image heuristic of `hosted_image` (a container with the local
    guard off) is deliberately NOT used here: a local development container must never be refused by a guess."""
    if HOSTED_FILE.exists():
        return True
    marker = os.environ.get(HOSTED_IMAGE_ENV, "").strip().lower()
    if marker and marker not in _TRUE and marker not in _FALSE:
        raise DeploymentConfigError(
            f"{HOSTED_IMAGE_ENV}={os.environ.get(HOSTED_IMAGE_ENV)!r} is not a recognised value "
            f"({sorted(_TRUE)} or {sorted(_FALSE)}); a mislabelled marker is not guessed at. Refusing to start."
        )
    return marker in _TRUE


def is_release_install() -> bool:
    """Whether this package is running from an installed release rather than a development checkout."""
    return RELEASE_MARKER in Path(__file__).resolve().as_posix()


def declared_edition() -> str | None:
    """The edition the environment names (`studio`, `product` or `dev`), None when it names none (unset or blank).
    Raises `DeploymentConfigError` for any other value: a mislabelled edition is never guessed at."""
    raw = os.environ.get(EDITION_ENV, "").strip().lower()
    if not raw:
        return None
    if raw not in EDITIONS:
        raise DeploymentConfigError(
            f"{EDITION_ENV} must be one of {sorted(EDITIONS)}, not an unrecognised value; refusing to start"
        )
    return raw


def resolved_edition() -> str:
    """The one answer to "which edition is this process": `studio`, `product` or `dev`.

    `studio` and `product` when declared. A release install or a hosted image is the product whatever else is said
    (including `dev`, and including nothing at all), so an unlabelled container can never serve the Studio routes. Only
    an unlabelled or explicitly `dev` development checkout is `dev`, which serves the Studio surfaces locally."""
    declared = declared_edition()
    if declared in ("studio", "product"):
        return declared
    if is_release_install() or hosted_image():
        return "product"
    return "dev"


def auth_setting() -> str | None:
    """The `PRAVRUDHI_AUTH` value, lowercased and stripped; None when unset or blank."""
    raw = os.environ.get(AUTH_ENV, "").strip().lower()
    return raw or None


def validate() -> None:
    """Refuse to start on a deployment that would fail open. Called from the identity boot guard."""
    declared_edition()  # raises on an unrecognised edition
    hosted = hosted_marker_or_raise()  # raises on an unrecognised hosted marker, whatever PRAVRUDHI_AUTH says
    setting = auth_setting()
    if setting is not None and setting not in AUTH_MODES:
        raise DeploymentConfigError(
            f"{AUTH_ENV}={os.environ.get(AUTH_ENV)!r} is not one of {sorted(AUTH_MODES)}; an unrecognised value used to mean "
            "`disabled` (every caller the operator). Refusing to start."
        )
    if hosted and setting is None:
        raise DeploymentConfigError(
            f"{AUTH_ENV} is unset on a hosted image. Unset means `disabled` only on a local install: name it "
            "(the image default is `required`). Refusing to start."
        )
    if hosted and setting == "disabled":
        raise DeploymentConfigError(
            f"{AUTH_ENV}=disabled on a hosted image: every caller would be the operator. A hosted image runs `required` or "
            "`optional` only; there is no opt-in for `disabled` here. Refusing to start."
        )
