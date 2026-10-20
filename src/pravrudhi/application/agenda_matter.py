"""A5 route support: the review agenda for ONE A2 matter (flag `wf_a5_ingredient_agenda`). Imported only when the flag is on.

This module touches A2's store through `MatterStore.list_documents` (names and page counts) and `MatterStore.read_passages`
(the passages the ingredient pointers are looked up in: a pointer is a place to start reading, never a finding), both
tenant-checked exactly as every A2 read. The agenda needs A2 ON (`wf_a2_span_index` true in the config), not merely a
store folder: with A2 off the route refuses and reads nothing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from pravrudhi.application import agenda as G
from pravrudhi.application.matter_spans import MatterError, MatterStore

ROUTE = G.ROUTE
STORE_OFF = "Stored papers are not available."


class AgendaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matter_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    offence: StrictStr = Field(default=G.OFFENCE_ID, max_length=64)
    #: the user's own word for what each added paper is: document id -> a type id from the page's list
    paper_types: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=500)
    #: paper types the user holds but has not added as a file (a scan with no text layer is not kept by A2): closed type ids
    papers_held_without_file: list[StrictStr] = Field(default_factory=list, max_length=20)


def open_route_lists() -> None:
    """Name the route in the two classification lists that must name it while it exists (as the other optional routes do)."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE}


def agenda_for_matter(
    store: MatterStore, tenant: str, req: AgendaRequest, run_id: str, ownership: bool = False
) -> dict[str, Any]:
    if req.offence != G.OFFENCE_ID:
        raise MatterError("no_agenda", G.REFUSAL, 422)
    listing = store.list_documents(tenant, req.matter_id)  # tenant-checked: another tenant's matter reads as not found
    docs = [d for d in listing["documents"] if d["ingest_done"]]
    if not docs:
        raise MatterError("no_papers", G.NO_PAPERS, 409)
    _docs, spans = store.read_passages(tenant, req.matter_id)
    names = {d["doc_id"]: d["name"] for d in docs}
    passages = [
        G.Passage(
            sp["doc_id"], names.get(sp["doc_id"], ""), int(sp["page"]), int(sp["start"]), int(sp["end"]), sp["text"]
        )
        for sp in spans
        if sp["doc_id"] in names
    ]
    records = [G.Doc(d["doc_id"], d["name"], int(d["n_pages"]), tuple(int(p) for p in d["pages_without_text"])) for d in docs]
    try:
        page = G.build_agenda(
            records, dict(req.paper_types), req.offence, ownership, list(req.papers_held_without_file), passages
        )
        from pravrudhi.application.ocr_marks import mark_tree, ocr_page_map

        mark_tree(page, ocr_page_map(listing["documents"]))  # a pointer quotes a passage: say if its page was read from an image
        return {"run_id": run_id, "matter_id": req.matter_id, **page}
    except G.AssetChanged as e:
        raise MatterError("agenda_asset_changed", "The agenda is not available.", 500) from e
    except ValueError as e:
        raise MatterError("bad_paper_type", str(e), 422) from e
