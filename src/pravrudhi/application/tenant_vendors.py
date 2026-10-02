"""Which vendors an API caller may use. Policy is `configs/tenant_vendors.yaml`; this module only enforces it.

Default-closed on every axis: a vendor not listed, a missing or malformed config, and every cli vendor (it
runs on the operator's seat, so it is never an API caller's to use) all refuse. Enforced only inside the
`serving_api` context; the CLI and research entrypoints are unaffected.
"""

from __future__ import annotations

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


def _ids(v: Any) -> set[str] | None:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        return None
    return set(v)


def allowed_ids(org: str | None = None, *, path: Path | None = None) -> frozenset[str]:
    """The vendor ids this caller may use. Empty on any config problem. A cli vendor in the file empties it too."""
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
    if any(i in panel.VENDORS and panel.VENDORS[i].interface == "cli" for i in ids):
        return frozenset()
    return frozenset(ids)


def is_allowed(vendor_id: str, org: str | None = None, *, path: Path | None = None) -> bool:
    return vendor_id in allowed_ids(org, path=path)


def require(vendor_id: str, org: str | None = None, *, path: Path | None = None) -> None:
    if not is_allowed(vendor_id, org, path=path):
        raise VendorNotAllowed(f"{VENDOR_NOT_ALLOWED}: {vendor_id}")
