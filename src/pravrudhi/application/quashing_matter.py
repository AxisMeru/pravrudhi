# ruff: noqa: E501
"""S3 route support: the quashing categories agenda for ONE A2 matter (flag `wf_b2_quashing_agenda`). Imported only when the flag is on.

Touches A2's store only through `MatterStore.list_documents` (names, tenant-checked exactly as every A2 read): no passage is read or
searched. Needs A2 ON (`wf_a2_span_index` true), not merely a store folder. Answers the fixed UNAVAILABLE text (503, no id or title
echoed) whenever the pinned case cannot be confirmed in the case index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from pravrudhi.application import quashing as G
from pravrudhi.application.matter_spans import MatterError, MatterStore

ROUTE = G.ROUTE
HEALTH_ROUTE = G.ROUTE + "/health"
STORE_OFF = G.STORE_OFF


class QuashingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matter_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    relief: StrictStr = Field(default=G.RELIEF_ID, max_length=64)


def open_route_lists() -> None:
    """Name the route in the two classification lists that must name it while it exists (as the other optional routes do)."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE), ("GET", HEALTH_ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE, HEALTH_ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE, HEALTH_ROUTE}


def agenda_for_matter(
    store: MatterStore, tenant: str, req: QuashingRequest, run_id: str, index_path: Path | None
) -> dict[str, Any]:
    if req.relief != G.RELIEF_ID:
        raise MatterError("no_agenda", G.REFUSAL, 422)  # the relief string is never echoed
    if G.pin_status_cached(index_path) is not None:
        raise MatterError("agenda_unavailable", G.UNAVAILABLE, 503)  # a fixed text and a fixed code: no id, no title, no reason
    listing = store.list_documents(tenant, req.matter_id)  # tenant-checked: another tenant's matter reads as not found
    docs = [
        G.Doc(d["doc_id"], d["name"], int(d["n_pages"]), tuple(int(p) for p in d["pages_without_text"]))
        for d in listing["documents"]
        if d["ingest_done"]
    ]
    try:
        return {"run_id": run_id, "matter_id": req.matter_id, **G.build_page(docs)}
    except G.AssetChanged as e:
        raise MatterError("agenda_unavailable", G.UNAVAILABLE, 503) from e


def health(index_path: Path | None) -> dict[str, str | None]:
    """The pin as the health probe reads it: available, or unavailable with a FIXED reason code. No title, id or year is ever echoed."""
    reason = G.pin_status_cached(index_path)
    return {"state": "available", "reason": None} if reason is None else {"state": "unavailable", "reason": reason}
