"""A3 route support: the chronology of ONE A2 matter (flag `wf_a3_chronology`). Imported only when the flag is on.

This module is the only place the chronology touches A2's store, and only to READ (`MatterStore.read_passages`: the committed
documents and their passages, one read transaction, tenant-checked). The chronology needs A2 ON (`wf_a2_span_index` true in the
config), not merely a store folder: with A2 off the route refuses and reads nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from pravrudhi.application import chronology as C
from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.matter_spans import MatterError, MatterStore

ROUTE = C.ROUTE
STORE_OFF = "Stored papers are not available."


class ChronologyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matter_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")


def open_route_lists() -> None:
    """Name the route in the two classification lists that must name it while it exists (as the other optional routes do)."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE}


def chronology_for_matter(
    store: MatterStore, tenant: str, matter_id: str, cfg: ChronologyConfig, run_id: str, today: date | None = None
) -> dict[str, Any]:
    docs, spans = store.read_passages(tenant, matter_id)
    if not docs:
        raise MatterError("no_papers", C.NO_PAPERS, 409)
    metas = [C.DocMeta(d["doc_id"], d["name"], d["n_pages"], tuple(d["pages_without_text"]), d["n_pages"] > 1) for d in docs]
    recs = [
        C.SpanRec(
            s["doc_id"],
            next(d["name"] for d in docs if d["doc_id"] == s["doc_id"]),
            s["page"],
            s["start"],
            s["end"],
            s["text"],
            s["span_id"],
        )
        for s in spans
    ]
    out = C.build_chronology(recs, metas, cfg, today)
    out["run_id"] = run_id
    out["matter_id"] = matter_id
    return out
