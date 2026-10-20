# ruff: noqa: E501
"""B1 route support: look up an old section (IPC or CrPC) in the Corresponding Section Table the National Crime Records Bureau
appends to the official BNS and BNSS texts (flag `wf_b1_section_lookup`). Imported only when the flag is on.

No model, no matter store, no network, no write. Three assets are joined by id and never copied into each other: the mapping
(refs and kinds only), the IPC bodies (reliable sections only) and the BNS and BNSS bodies the engine already ships. A one-to-one
line is shown ONLY for a row whose `display_ok_as_one_to_one` is true AND that the loader recomputes as true; every other row says
"check both texts"; an absent section says it is not in the table. Nothing is inferred. CrPC bodies are not held.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, StrictStr

ROUTE = "/api/v1/section-lookup"
BAD_BODY = {"error": "bad_request"}
UNAVAILABLE = "The section look-up is not available just now."
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "section_lookup"
NYAYA_DIR = Path(__file__).resolve().parent.parent / "assets" / "nyaya"
#: sha256 of the two shipped files: a changed file fails closed (503) until the pin and the tests are updated on purpose
PIN_MAPPING = "ec2e7e7efd35bf5526b1c3b6209e1a7cbd4d0a5a7e8c100dc8a95dde06517cad"
PIN_IPC_BODIES = "027465874105287a5d4e32419248aa3c99e681d39c46033ad27b5381710a2039"
REQUIRED_STRINGS = frozenset(
    {
        "b1_source",
        "b1_one_to_one",
        "b1_check_both",
        "b1_check_old_only",
        "b1_check_new_only",
        "b1_no_match",
        "b1_not_in_table",
        "b1_lawyer",
        "b1_ipc_source",
        "b1_ipc_currency",
        "b1_ipc_marks",
        "b1_body_not_held",
        "b1_bns_source",
    }
)
KINDS = frozenset({"one_to_one", "merged", "split", "deleted", "new_subsection", "needs_review_blank_cell"})
CODES = ("IPC", "CrPC")
_TABLE = {"IPC": "IPC_to_BNS", "CrPC": "CrPC_to_BNSS"}
_NEW_BODY_FILE = {"BNS": "bns_sections.json", "BNSS": "bnss_sections.json"}
SECTION_RE = r"[0-9]{1,3}[A-Za-z]{0,3}"  # matched with fullmatch: a trailing newline is not a section


class AssetChanged(Exception):
    """A shipped asset does not match its pin or its own rules: the look-up answers its fixed 503 and says nothing else."""


class SectionLookupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: StrictStr
    section: StrictStr


class FixedValidationRoute(APIRoute):
    """A route whose request-validation failures answer ONE fixed 422 body: the framework default echoes the input and the field path."""

    def get_route_handler(self) -> Any:
        original = super().get_route_handler()

        async def handler(request: Any) -> Any:
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content=BAD_BODY)

        return handler


def open_route_lists() -> None:
    """Name the route in the two classification lists that must name it while it exists (as the other optional routes do)."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _recompute_display_ok(row: dict[str, Any]) -> bool:
    return (
        row["kind"] == "one_to_one"
        and row["cross_check"] == "agrees_with_secondary"
        and not row["hand_check_required"]
        and len(row["new_ids"]) == 1
    )


def build_assets(
    mapping: dict[str, Any], ipc: dict[str, Any], new_bodies: dict[str, dict[str, dict[str, Any]]]
) -> dict[str, Any]:
    """Validate the three assets against each other and return the look-up structure. Raises AssetChanged on ANY disagreement (pure: the tests call it)."""
    strings = {k: v["text"] for k, v in mapping["strings_pending_R1"].items()}
    if not set(strings) >= REQUIRED_STRINGS:
        raise AssetChanged("a required string is missing")
    for k, v in mapping["strings_pending_R1"].items():
        if hashlib.sha256(v["text"].encode()).hexdigest()[:16] != v["sha256_16"]:
            raise AssetChanged(f"string {k} does not match its recorded hash")
    held = {d["id"]: d for d in ipc["documents"]}
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    for code in CODES:
        t = mapping["tables"][_TABLE[code]]
        by: dict[str, dict[str, Any]] = {}
        for r in t["rows"]:
            if r["kind"] not in KINDS:
                raise AssetChanged("a row has a kind outside the closed list")
            if r["old_id"] in by:
                raise AssetChanged("a section appears twice in the table")
            if _recompute_display_ok(r) != r["display_ok_as_one_to_one"]:
                raise AssetChanged("a row's one-to-one flag disagrees with its own evidence")
            for n in r["new_ids"]:
                if n not in new_bodies[t["new_code"]]:
                    raise AssetChanged("a row names a section the body asset does not hold")
            by[r["old_id"]] = r
        rows[code] = by
    return {"strings": strings, "rows": rows, "ipc": held, "new": new_bodies}


def _load() -> dict[str, Any]:
    mp, ip = ASSET_DIR / "corresponding_sections_v1.json", ASSET_DIR / "ipc_bodies_v1.json"
    if _sha(mp) != PIN_MAPPING or _sha(ip) != PIN_IPC_BODIES:
        raise AssetChanged("a shipped asset does not match its pin")
    new = {}
    for code, fn in _NEW_BODY_FILE.items():
        new[code] = {d["id"]: d for d in json.loads((NYAYA_DIR / fn).read_text(encoding="utf-8"))["documents"]}
    return build_assets(json.loads(mp.read_text(encoding="utf-8")), json.loads(ip.read_text(encoding="utf-8")), new)


_RESULT: list[
    Any
] = []  # one entry once loaded: the assets, or the AssetChanged that a failed pin or check raised (a failure is cached, not re-hashed per request)


def load_assets() -> dict[str, Any]:
    if not _RESULT:
        try:
            _RESULT.append(_load())
        except AssetChanged as e:
            _RESULT.append(e)
        except Exception as e:  # noqa: BLE001 - an unreadable or malformed asset (a changed byte in a body file included) fails closed like a changed pin, never a 500
            _RESULT.append(AssetChanged(f"an asset could not be read: {type(e).__name__}"))
    got = _RESULT[0]
    if isinstance(got, AssetChanged):
        raise AssetChanged(str(got))
    return got  # type: ignore[no-any-return]


def _clear_cache() -> None:
    _RESULT.clear()


load_assets.cache_clear = _clear_cache  # type: ignore[attr-defined]  # the tests reset it after changing a pin


def _body(doc: dict[str, Any] | None, source_keys: tuple[str, ...], strings: dict[str, str]) -> dict[str, Any]:
    if doc is None:
        return {"held": False, "lines": [strings["b1_body_not_held"]]}
    return {
        "held": True,
        "section": doc["section"],
        "title": doc["title"],
        "text": doc["text"],
        "lines": [strings[k] for k in source_keys],
    }


def lookup(req: SectionLookupRequest, assets: dict[str, Any] | None = None) -> dict[str, Any]:
    """The look-up answer. Raises ValueError for an unknown code or a malformed section (the route maps both to the fixed 422); never echoes the input."""
    a = assets if assets is not None else load_assets()
    if req.code not in CODES or not re.fullmatch(SECTION_RE, req.section):
        raise ValueError("bad request")
    sec = req.section.upper()
    S = a["strings"]
    old_id = f"{req.code}/Section {sec}"
    old = _body(a["ipc"].get(old_id) if req.code == "IPC" else None, ("b1_ipc_source", "b1_ipc_currency", "b1_ipc_marks"), S)
    out: dict[str, Any] = {"table_source_line": S["b1_source"], "lawyer_line": S["b1_lawyer"], "old": old, "new": []}
    row = a["rows"][req.code].get(old_id)
    if row is None:
        out.update(state="not_in_table", line=S["b1_not_in_table"])
        return out
    if row["kind"] == "deleted":
        out.update(state="no_match", line=S["b1_no_match"])
        return out
    if row["display_ok_as_one_to_one"]:
        n = row["new_ids"][0]
        out.update(state="one_to_one", line=S["b1_one_to_one"].replace("{new}", n.split("/", 1)[1]), new=[_new(a, n, S)])
        return out
    # an unresolved or hand-check row never lists a section: the table's own cells are not trusted there
    if not row["hand_check_required"]:
        out["new"] = [_new(a, n, S) for n in row["new_ids"]]
    # one line per answer, naming exactly which text(s) are on screen (R1's rule): "side by side" only when two texts are shown
    old_held = out["old"]["held"]
    if out["new"]:
        line = S["b1_check_both"] if old_held else S["b1_check_new_only"]
    else:
        line = S["b1_check_old_only"] if old_held else S["b1_body_not_held"]
    out.update(state="check_both", line=line)
    return out


def _new(a: dict[str, Any], new_id: str, S: dict[str, str]) -> dict[str, Any]:
    code = new_id.split("/", 1)[0]
    return {"id": new_id, **_body(a["new"][code][new_id], ("b1_bns_source",), S)}
