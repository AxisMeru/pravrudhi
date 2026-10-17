# ruff: noqa: E501
"""A7 route support: the matter hearing pack of ONE A2 matter (flag `wf_a7_matter_hearing_pack`). Imported only when the flag is on.

One call gathers three existing pieces over the matter's stored papers, as functions (no model is called): A3's chronology, A5's papers agenda and,
ONLY when the S1 citation sweep is also switched on and the citation index is available, S1's sweep (otherwise the page says the sweep is not
switched on). Track A's `matter_hearing_pack.compose` puts them on one page; it does not say what matters for the hearing. The hearing date, when
the user gives one, is echoed back as typed (ISO) and never inferred. Needs A2 ON (its flag, never the store folder); stores nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from pravrudhi.application import agenda_matter as AM
from pravrudhi.application import chronology_matter as CM
from pravrudhi.application import matter_hearing_pack as HP
from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.matter_spans import MatterError, MatterStore

ROUTE = "/api/v1/matter-hearing-pack"
RUN_PREFIX = "hearing-"
STORE_OFF = "Stored papers are not available."
BAD_BODY = {"error": "bad_request"}
#: runs A1's sweep for a matter when S1 is on and the index is available, else None (the route layer builds it)
SweepRunner = Callable[[MatterStore, str, str, str], dict[str, Any]]


class MatterHearingPackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matter_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    #: the hearing date the user gives (ISO yyyy-mm-dd), echoed back and never inferred
    hearing_date: StrictStr | None = Field(default=None, min_length=10, max_length=10)
    paper_types: dict[StrictStr, StrictStr] = Field(default_factory=dict, max_length=500)
    papers_held_without_file: list[StrictStr] = Field(default_factory=list, max_length=20)

    @field_validator("hearing_date")
    @classmethod
    def _iso(cls, v: str | None) -> str | None:
        if v is not None:
            date.fromisoformat(v)  # a ValueError is a validation failure: the route answers its one fixed body
        return v


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


def pack_for_matter(
    store: MatterStore, tenant: str, req: MatterHearingPackRequest, run_id: str, sweep_runner: SweepRunner | None = None
) -> dict[str, Any]:
    """The one page. The chronology's tenant-checked read decides ownership first (a foreign, deleted or unknown matter is the same 404; 409
    `no_papers` when it holds none). The sweep is optional: absent, or refused as `index_off` or `busy`, the page carries no sweep and says so."""
    chron = CM.chronology_for_matter(store, tenant, req.matter_id, ChronologyConfig(), run_id)
    agenda = AM.agenda_for_matter(
        store, tenant, AM.AgendaRequest(matter_id=req.matter_id, paper_types=dict(req.paper_types), papers_held_without_file=list(req.papers_held_without_file)), run_id
    )
    sweep: dict[str, Any] | None = None
    if sweep_runner is not None:
        try:
            sweep = sweep_runner(store, tenant, req.matter_id, run_id)
        except MatterError as e:
            if e.code not in ("index_off", "busy"):
                raise
    return HP.compose(chron, agenda, sweep, run_id=run_id, matter_id=req.matter_id, hearing_date=req.hearing_date)
