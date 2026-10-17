# ruff: noqa: E501
"""S2 route support: the matter tabular table for ONE A2 matter (flag `wf_s2_tabular_fact_table`). Imported only when the flag is on AND A2 is on.

Touches A2's store only through `MatterStore.list_documents` (tenant-checked) and `MatterStore.read_passages` (one read transaction, tenant-checked, ingest_done
papers only). The route is registered ONLY when A2 is on: with S2 on and A2 off there is no route (a request gets the 404 of any unknown path).

Order of checks (design v7 section 4): the label map is checked on the INPUT ALONE first (size, then length), before any lookup; then ownership (a foreign, deleted
or unknown matter is the same 404, even when it holds no papers); then the label ids against the matter's own papers (control characters, unknown or foreign ids: ONE
neutral string); then the empty check (409). Every request-validation failure (extra keys, wrong types, a bad matter id, bad JSON) answers ONE fixed body.
"""

from __future__ import annotations

import threading
import unicodedata
from datetime import date
from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from pravrudhi.application import tabular as T
from pravrudhi.application.matter_spans import MatterError, MatterStore
from pravrudhi.application.tabular_config import TabularConfig

ROUTE = T.ROUTE
STORE_OFF = T.STORE_OFF
BAD_BODY = {"error": "bad_request"}  # a machine body, not page text; the app must not show it
_inflight: set[tuple[str, str]] = set()
_inflight_lock = threading.Lock()


class TabularRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matter_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    type_labels: dict[StrictStr, Any] | None = None  # the VALUES are checked in code, so a bad label gets R1's strings, not a schema body


class FixedValidationRoute(APIRoute):
    """Request-validation failures answer ONE fixed 422 body that echoes nothing (the framework's own body repeats the input and the field path)."""

    def get_route_handler(self) -> Any:
        original = super().get_route_handler()

        async def handler(request: Any) -> Any:
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content=BAD_BODY)

        return handler


def open_route_lists() -> None:
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE}


def check_label_input(labels: dict[str, Any] | None) -> None:
    """Checks that need the INPUT ALONE, run before any lookup of a doc id: (0) a map over 500 entries; (1) a value longer than 80 characters. The map-size case gets
    the neutral string (a separate string would tell the caller how many labels were counted); the length case gets the length string."""
    if not labels:
        return
    if len(labels) > T.LABELS_MAX:
        raise MatterError("label_unusable", T.LABEL_UNUSABLE, 422)
    if any(isinstance(v, str) and len(v) > T.LABEL_MAX for v in labels.values()):
        raise MatterError("label_too_long", T.LABEL_TOO_LONG.replace("{cap}", str(T.LABEL_MAX)), 422)


def _bad_text(v: str) -> bool:
    return any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in v)


def check_label_ids(labels: dict[str, Any] | None, known: set[str]) -> dict[str, str]:
    """After ownership: ONE neutral string for every other case: a value that is not a string, a control character or newline, an unknown or foreign doc id."""
    out: dict[str, str] = {}
    for k, v in (labels or {}).items():
        if k not in known or not isinstance(v, str) or _bad_text(v):
            raise MatterError("label_unusable", T.LABEL_UNUSABLE, 422)
        out[k] = v
    return out


def table_for_matter(
    store: MatterStore, tenant: str, req: TabularRequest, run_id: str, cfg: TabularConfig, today: date | None = None
) -> dict[str, Any]:
    check_label_input(req.type_labels)
    store.list_documents(tenant, req.matter_id)  # tenant-checked FIRST: a foreign, deleted or unknown matter is the same 404, even when it holds no papers
    key = (tenant, req.matter_id)
    with _inflight_lock:
        if key in _inflight:
            raise MatterError("busy", T.BUSY, 409)
        _inflight.add(key)
    try:
        docs, spans = store.read_passages(tenant, req.matter_id)
        labels = check_label_ids(req.type_labels, {d["doc_id"] for d in docs})
        if not docs:
            raise MatterError("no_papers", T.NO_PAPERS, 409)
        records = [T.Doc(d["doc_id"], d["name"], int(d["n_pages"]), tuple(int(p) for p in d["pages_without_text"])) for d in docs]
        passages = [T.Passage(int(s["span_id"]), s["doc_id"], int(s["page"]), int(s["start"]), int(s["end"]), s["text"]) for s in spans]
        try:
            out = T.run_table(records, passages, cfg, labels, run_id=run_id, today=today or date.today())
        except T.TableRefused as e:
            raise MatterError(e.code, str(e), e.status_code) from e
        store.list_documents(tenant, req.matter_id)  # the matter must still exist at the end (404 otherwise, nothing returned)
        return {**out, "matter_id": req.matter_id}
    finally:
        with _inflight_lock:
            _inflight.discard(key)
