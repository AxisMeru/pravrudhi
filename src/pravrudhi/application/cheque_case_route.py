# ruff: noqa: E501
"""A6 route support: the cheque-case check of ONE A2 matter (flag `wf_a6_cheque_case_check`). Imported only when the flag is on.

One call runs three existing pieces over the matter's stored papers, as functions (no other flag is needed, no model is called): A3's chronology
(dates as written, with the paper and page), A5's papers agenda for an NI Act s.138 matter, and, only when the request carries the dates the person
confirmed (`b3`), B3's date checklist. Track A's `cheque_case` turns the chronology rows into date CANDIDATES per step and composes the one page;
nothing is filled in or decided here. Needs A2 ON (its flag, never the store folder). Reads only through `MatterStore.read_passages` and
`MatterStore.list_documents` (tenant-checked); stores nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from pravrudhi.application import agenda_matter as AM
from pravrudhi.application import cheque_case
from pravrudhi.application import chronology_matter as CM
from pravrudhi.application import s138_checklist as B3
from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.matter_spans import MatterError, MatterStore
from pravrudhi.application.s138_checklist_config import S138ChecklistConfig

ROUTE = "/api/v1/cheque-case-check"
RUN_PREFIX = "cheque-"
STORE_OFF = "Stored papers are not available."
BAD_BODY = {"error": "bad_request"}


class ChequeCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matter_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    #: the dates the person picked and confirmed (B3's own request); absent = candidates and agenda only
    b3: B3.ChecklistRequest | None = None
    #: as A5's request: the user's own word for what each added paper is, and papers held without a file
    paper_types: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=500)
    papers_held_without_file: list[StrictStr] = Field(default_factory=list, max_length=20)


class FixedValidationRoute(APIRoute):
    """Validation failures answer ONE fixed 422 body (the framework default echoes the input and the field path back)."""

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


def check_for_matter(store: MatterStore, tenant: str, req: ChequeCaseRequest, run_id: str) -> dict[str, Any]:
    """The one page. Ownership is decided first by the chronology's tenant-checked read (a foreign, deleted or unknown matter is the same 404,
    409 `no_papers` when it holds none); A5's own check of the same matter then runs on the same tenant."""
    chron = CM.chronology_for_matter(store, tenant, req.matter_id, ChronologyConfig(), run_id)
    candidates = cheque_case.date_candidates(chron)
    agenda = AM.agenda_for_matter(
        store, tenant, AM.AgendaRequest(matter_id=req.matter_id, paper_types=dict(req.paper_types), papers_held_without_file=list(req.papers_held_without_file)), run_id
    )
    checklist: dict[str, Any] | None = None
    if req.b3 is not None:
        try:
            checklist = B3.check(req.b3, S138ChecklistConfig(), run_id=run_id)
        except B3.ChecklistRefused as e:
            raise MatterError(e.code, str(e), e.status_code) from e
    return cheque_case.compose(
        candidates, agenda, checklist, run_id=run_id, matter_id=req.matter_id, chronology_not_assessed=chron.get("not_assessed")
    )
