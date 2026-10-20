# ruff: noqa: E501
"""S1 route support: the citation sweep of ONE A2 matter (flag `wf_s1_matter_citation_sweep`). Imported only when the flag is on.

Touches A2's store only through `MatterStore.list_documents` (names, tenant-checked) and `MatterStore.read_passages` (one read transaction,
tenant-checked, ingest_done papers only). The sweep needs A2 ON (`wf_a2_span_index` true, not merely a store folder) and a configured citation
index. A foreign or deleted matter is the same 404 as none, BEFORE any other check. One sweep per tenant+matter at a time (`busy`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from pravrudhi.application import matter_sweep as S
from pravrudhi.application.matter_spans import MatterError, MatterStore
from pravrudhi.application.matter_sweep_config import SweepConfig

ROUTE = S.ROUTE
STORE_OFF = S.STORE_OFF
_inflight: set[tuple[str, str]] = set()
_inflight_lock = threading.Lock()


class SweepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matter_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")


def open_route_lists() -> None:
    """Name the route in the two classification lists that must name it while it exists (as the other optional routes do)."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE}


def sweep_for_matter(
    store: MatterStore,
    tenant: str,
    req: SweepRequest,
    run_id: str,
    cfg: SweepConfig,
    lookup: Callable[[str], tuple[str, bool]] | None,
) -> dict[str, Any]:
    listing = store.list_documents(
        tenant, req.matter_id
    )  # tenant-checked FIRST: a foreign matter reads as not found, never as a 409
    if lookup is None:
        raise MatterError("index_off", S.INDEX_OFF, 409)
    not_ready = sum(1 for d in listing["documents"] if not d["ingest_done"])
    key = (tenant, req.matter_id)
    with _inflight_lock:
        if key in _inflight:
            raise MatterError("busy", S.BUSY, 409)
        _inflight.add(key)
    try:
        docs, spans = store.read_passages(tenant, req.matter_id)
        if not docs:
            raise MatterError("no_papers", S.NO_PAPERS, 409)
        records = [S.Doc(d["doc_id"], d["name"], int(d["n_pages"]), tuple(int(p) for p in d["pages_without_text"])) for d in docs]
        passages = [
            S.Passage(int(s["span_id"]), s["doc_id"], int(s["page"]), int(s["start"]), int(s["end"]), s["text"]) for s in spans
        ]
        try:
            out = S.run_sweep(records, passages, cfg, lookup, run_id=run_id, not_ready=not_ready)
        except S.SweepRefused as e:
            raise MatterError(e.code, str(e), e.status_code) from e
        store.list_documents(
            tenant, req.matter_id
        )  # the matter must still exist: a snapshot of a deleted matter is not returned (404)
        from pravrudhi.application.ocr_marks import mark_tree, ocr_page_map

        pages = ocr_page_map(docs)
        mark_tree(out, pages)  # citation rows, occurrences and cut rows: say if the page was read from an image
        if pages:  # a distinct citation is marked when ANY of its occurrences is from a scanned page (R1 ADVISE on #605)
            from pravrudhi.application.scanned_intake_strings import OCR_MARKER_LINE

            cut = bool(
                out.get("listing_truncated")
            )  # occurrences may have been dropped for size: read the entry's papers instead
            for entry in out.get("distinct_citations", []):
                entry["from_ocr"] = any(o.get("from_ocr") for o in entry.get("occurrences", [])) or (
                    cut and any(pages.get(p["doc_id"]) for p in entry.get("papers", []))  # conservative: a scanned paper holds it
                )
                if entry["from_ocr"]:
                    entry["ocr_marker"] = OCR_MARKER_LINE
        return {**out, "matter_id": req.matter_id}
    finally:
        with _inflight_lock:
            _inflight.discard(key)
