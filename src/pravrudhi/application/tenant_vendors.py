"""Which vendors an API caller may use. Policy is `configs/tenant_vendors.yaml`; this module only enforces it.

Default-closed on every axis: a vendor not listed and a missing or malformed config refuse. A cli vendor runs on
the operator's seat, so it is allowed only in the Studio edition (the operator's own localhost-only deployment),
and only if the release config's `studio:` section lists it. The edition is a property of the deployment
(`PRAVRUDHI_EDITION`), never of the request, so a forged token or role cannot unlock it. Enforced only inside the
`serving_api` context; the CLI and research entrypoints are unaffected.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.config_files import PACKAGED_CONFIG_DIR

CONFIG_NAME = "tenant_vendors.yaml"
VENDOR_NOT_ALLOWED = "vendor not allowed for API callers"


class VendorNotAllowed(RuntimeError):
    pass


SOURCE_CONFIG_DIR = Path(__file__).resolve().parents[3] / "configs"


def _default_path() -> Path:
    # The policy belongs to the release, never to a caller's workspace: a tenant's project root must not be able to
    # supply its own allowlist, so the root a request carries is deliberately not consulted.
    packaged = PACKAGED_CONFIG_DIR / CONFIG_NAME
    return packaged if packaged.is_file() else SOURCE_CONFIG_DIR / CONFIG_NAME


def _load(path: Path | None) -> dict[str, Any] | None:
    try:
        p = path or _default_path()
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


def is_studio_edition() -> bool:
    """True only when the deployment says `studio` outright and is not an installed release.

    Deliberately not `edition.engine_edition()`: that one calls an unlabelled development checkout Studio, which
    is right for naming the product and wrong for a security carve-out, where a missing or unknown value must be
    closed.
    """
    from pravrudhi.api import edition

    declared = os.environ.get(edition.EDITION_ENV, "").strip().lower()
    return declared == "studio" and not edition.is_release_install()


def _ids(v: Any) -> set[str] | None:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        return None
    return set(v)


def _is_cli(vendor_id: str, panel: Any) -> bool:
    return vendor_id in panel.VENDORS and panel.VENDORS[vendor_id].interface == "cli"


def allowed_ids(org: str | None = None, *, path: Path | None = None) -> frozenset[str]:
    """The vendor ids this caller may use. Empty on any config problem. A cli vendor in `default` or an org entry
    empties it; one is allowed only via `studio:` and only in the Studio edition."""
    from pravrudhi.application import panel

    cfg = _load(path)
    if cfg is None:
        return frozenset()
    ids = _ids(cfg.get("default"))
    if ids is None:
        return frozenset()
    orgs = cfg.get("orgs") or {}
    if not isinstance(orgs, dict):
        return frozenset()
    if org is not None and org in orgs:
        o = orgs[org]
        if not isinstance(o, dict):
            return frozenset()
        if "allow" in o:
            ids = _ids(o["allow"])
        if ids is not None and "extend" in o:
            ext = _ids(o["extend"])
            ids = None if ext is None else ids | ext
        if ids is None:
            return frozenset()
    if any(_is_cli(i, panel) for i in ids):
        return frozenset()
    if "studio" in cfg and is_studio_edition():
        extra = _ids(cfg["studio"])
        if extra is None:
            return frozenset()
        ids = ids | extra
    return frozenset(ids)


def is_allowed(vendor_id: str, org: str | None = None, *, path: Path | None = None) -> bool:
    return vendor_id in allowed_ids(org, path=path)


def require(vendor_id: str, org: str | None = None, *, path: Path | None = None) -> None:
    if not is_allowed(vendor_id, org, path=path):
        raise VendorNotAllowed(f"{VENDOR_NOT_ALLOWED}: {vendor_id}")
