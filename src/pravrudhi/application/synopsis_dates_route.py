# ruff: noqa: E501
"""A8 route support: the synopsis and list of dates of ONE A2 matter (flag `wf_a8_synopsis_list_of_dates`). Imported only when the flag is on.

Glue between the request and Track A's `synopsis_dates.draft`: consent, the deterministic referrals (sensitive tags and charged sections, shared
with bail drafting), A3's chronology of the matter read as a function (tenant-checked: a foreign, deleted or unknown matter is the same 404, 409
`no_papers` when it holds none), the guarded completer and the ONE shared day ceiling of the drafting slices, and the response wrapper. The engine
stores nothing; nothing of the request or the papers is logged or audited (the audit row carries the run id only). The output is a first draft for a
lawyer to rewrite; the label and the referral lines are R1's (bail_draft_input).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from pravrudhi.application import bail_draft_input as I
from pravrudhi.application import bail_draft_route as R
from pravrudhi.application import chronology_matter as CM
from pravrudhi.application.bail_draft_config import BailDraftConfig
from pravrudhi.application.chronology_config import ChronologyConfig
from pravrudhi.application.matter_spans import MatterStore

ROUTE = "/api/v1/synopsis-dates"
STORE_OFF = "Stored papers are not available."
RUN_PREFIX = "synopsis-"
MAX_ROW_IDS = 60
BAD_BODY = R.BAD_BODY
FixedValidationRoute = R.FixedValidationRoute
DraftError = R.DraftError


class SynopsisFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr = Field(min_length=1, max_length=1500)
    side: Literal["applicant", "other"]  # an explicit tick: only the client's side enters the prompt


class SynopsisParties(BaseModel):
    model_config = ConfigDict(extra="forbid")
    applicant: StrictStr | None = Field(default=None, max_length=200)
    state: StrictStr | None = Field(default=None, max_length=200)


class SynopsisDatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: StrictBool
    matter_id: StrictStr = Field(pattern=r"^[0-9a-f]{32}$")
    facts: list[SynopsisFact] = Field(default_factory=list, max_length=200)
    #: A3 row ids the person keeps in the list of dates; absent = every exact and two-reading row (the drafting module's cap applies)
    include_row_ids: list[StrictInt] | None = Field(default=None, max_length=500)
    parties: SynopsisParties | None = None
    #: a free label for the document ("writ petition", "application"): echoed, never interpreted
    document_label: StrictStr | None = Field(default=None, max_length=80)
    tags: list[Literal["pocso", "minor_involved", "sexual_offence", "marked_sensitive", "trafficking"]] = Field(default_factory=list, max_length=10)
    charged_sections: list[StrictStr] = Field(default_factory=list, max_length=30)


@dataclass(frozen=True)
class _Fact:
    fact_id: str
    text: str
    grounds: tuple[str, ...] = ()


@dataclass(frozen=True)
class SynopsisCase:
    """What `synopsis_dates.draft` sees: the client-side facts only (F1.. over those alone), the parties and the document label."""

    matter_id: str
    facts: tuple[_Fact, ...]
    n_facts_excluded: int
    authorities: tuple[str, ...] = ()
    parties: dict[str, str] = field(default_factory=dict)
    document_label: str | None = None


def open_route_lists() -> None:
    """Name the route in the two classification lists that must name it while it exists (as the other optional routes do)."""
    from pravrudhi.api import identity, roles

    identity.PARTNER_KEY_ROUTES = identity.PARTNER_KEY_ROUTES | {("POST", ROUTE)}
    identity.PARTNER_KEY_PATHS = identity.PARTNER_KEY_PATHS | {ROUTE}
    roles.USER_FACING = roles.USER_FACING | {ROUTE}


def parse_case(req: SynopsisDatesRequest, max_facts: int, matter_id: str) -> SynopsisCase | I.Referral:
    """Rules only: a sensitive tag or a listed sensitive section refers (no draft, no call, and the matter is not read)."""
    if any(t in I.SENSITIVE_TAGS for t in req.tags):
        return I.Referral("sensitive_matter", I.REFER_SENSITIVE)
    if req.charged_sections and I.sensitive_section(req.charged_sections):
        return I.Referral("sensitive_section", I.REFER_SENSITIVE_SECTION)
    own = [f for f in req.facts if f.side == "applicant"][:max_facts]
    parties = {k: v.strip() for k, v in (("applicant", (req.parties.applicant if req.parties else None) or ""), ("state", (req.parties.state if req.parties else None) or "")) if v.strip()}
    return SynopsisCase(
        matter_id,
        tuple(_Fact(f"F{i}", f.text.strip()) for i, f in enumerate(own, 1)),
        sum(1 for f in req.facts if f.side != "applicant"),
        (),
        parties,
        (req.document_label or "").strip() or None,
    )


def synopsis_for_request(
    store: MatterStore,
    tenant: str,
    req: SynopsisDatesRequest,
    cfg: BailDraftConfig,
    run_id: str,
    ceiling: R.DayCeiling,
    *,
    completer: R.Completer | None = None,
    synopsis_module: Any = None,
) -> dict[str, Any]:
    """The response for one request. Raises `DraftError` (400 consent_required) or the matter errors (404 / 409); a referral is a 200 answer."""
    if req.consent is not True:
        raise DraftError(I.CONSENT_REQUIRED, "Consent is required before a draft is made.", 400)
    base: dict[str, Any] = {"run_id": run_id, "client_data": True, "label": I.DRAFT_LABEL}
    case = parse_case(req, cfg.max_facts, run_id)
    if isinstance(case, I.Referral):
        return {**base, "state": "referred", "referral": {"code": case.code, "text": case.text}, "draft": None}
    chron = CM.chronology_for_matter(store, tenant, req.matter_id, ChronologyConfig(), run_id)  # 404 / 409 here
    if synopsis_module is None:
        from pravrudhi.application import synopsis_dates as synopsis_module
    bail = R._drafter()  # Completion lives with the bail drafting module
    use = completer
    if use is None and ceiling.room(cfg.usd_per_day):
        use = R.make_completer(cfg, bail)
    if use is not None and not ceiling.room(cfg.usd_per_day):
        use = None  # over the day ceiling: the table only, no call
    out = synopsis_module.draft(case, chron, use, cfg, run_id, req.include_row_ids)
    ceiling.add(float(out.get("cost_usd") or 0.0))
    return {**base, "state": out.get("state"), "facts_set_aside": case.n_facts_excluded, "draft": out}
