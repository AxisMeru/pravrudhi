# ruff: noqa: E501
"""A4 bail drafting, the deterministic front half: the request, the refusals (rules only, no model) and the statute framing by date.

The engine guesses nothing: the kind, the forum, every date and the applicant-side tick of each fact are declared by the lawyer. This module
decides only (1) whether the request is REFERRED to a lawyer without any draft (a sensitive matter, no applicant-side fact, a bail request
with no forum, dates that straddle the Code change) and (2) which Act and section the heading carries, from `assets/bail_draft/framing.json`
(a table a lawyer confirms; read on every request so an edit takes effect without a release). Nothing here calls a model.

The strings below are PENDING R1's wording (customer-visible, flag off): they are constants in ONE place so R1 can rule them and a hash test can pin them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, field_validator

ASSET = Path(__file__).resolve().parent.parent / "assets" / "bail_draft" / "framing.json"
SENSITIVE_ASSET = Path(__file__).resolve().parent.parent / "assets" / "bail_draft" / "sensitive.json"
ROUTE = "/api/v1/bail-draft"
RUN_PREFIX = "bail-"

GROUND_IDS = ("accusation_nature", "custody_period", "antecedents", "flight", "tampering", "co_accused")
#: matter tags the lawyer may declare; ANY of them refers the matter (liberty at stake, sensitive offences are out of scope for a first draft)
SENSITIVE_TAGS = ("pocso", "minor_involved", "sexual_offence", "marked_sensitive", "trafficking")

# ---- strings: PENDING R1 (flag off; none is shown to a user until R1 rules it) -----------------------------------------------------------
DECLARATION_REQUIRED = "sensitive_declaration_required"
#: R1's a4_declaration_missing (4c30a82379c58f15): the 422 detail and the app's line when nothing is chosen.
DECLARATION_REFUSAL = "Say whether this matter involves a person under 18, a sexual offence or trafficking before a draft can be made."
DECLARATION_VALUES = ("none", "sensitive")


def check_declaration(value: Any) -> str:
    """"none" or "sensitive" (the lawyer's declaration), else raises ValueError: no default, no coercion (not "None", not True, not "NONE")."""
    if isinstance(value, str) and value in DECLARATION_VALUES:
        return value
    raise ValueError("sensitive_declaration is required")


DRAFT_LABEL = "A first draft for a lawyer to rewrite. A lawyer decides what to use."
REFER_SENSITIVE = "This matter is marked sensitive, so no draft was made. Refer it to a lawyer."
REFER_SENSITIVE_SECTION = (
    "A section you listed is one this draft tool does not handle (trafficking, sexual offences or an offence against a child), "
    "so no draft was made. Refer it to a lawyer."
)
REFER_NO_FACTS = "No fact was ticked as the applicant's side, so no draft was made."
REFER_NO_FORUM = "A bail request needs the forum (trial court, or sessions court or High Court), so no draft was made."
REFER_STRADDLE = "The dates fall on both sides of 1 July 2024, so no draft was made. Both framings are shown below; a lawyer decides which Act governs."
FRAMING_UNCONFIRMED = "The Act and section shown come from a table a lawyer has not yet confirmed."
CONSENT_REQUIRED = "consent_required"


class FactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr = Field(min_length=1, max_length=1500)
    side: Literal["applicant", "other"]  # an explicit tick: court-voice, complainant-side and adverse facts cannot enter the prompt
    grounds: list[Literal["accusation_nature", "custody_period", "antecedents", "flight", "tampering", "co_accused"]] = Field(
        default_factory=list, max_length=6
    )


class BailDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consent: StrictBool
    #: the lawyer's mandatory declaration: "none" = no person under 18 and no sexual offence or trafficking is involved; "sensitive" = one is.
    #: Typed Any on purpose: any other value reaches the route's coded refusal instead of the one fixed validation body.
    sensitive_declaration: Any = None
    kind: Literal["bail", "anticipatory_bail"]
    forum: Literal["trial_court", "sessions_or_high_court"] | None = None
    offence_date: StrictStr = Field(min_length=10, max_length=10)
    fir_date: StrictStr = Field(min_length=10, max_length=10)
    arrest_date: StrictStr | None = Field(default=None, min_length=10, max_length=10)
    application_date: StrictStr | None = Field(default=None, min_length=10, max_length=10)
    applicant: StrictStr | None = Field(default=None, max_length=200)
    state: StrictStr | None = Field(default=None, max_length=200)
    court: StrictStr | None = Field(default=None, max_length=200)
    facts: list[FactIn] = Field(min_length=1, max_length=200)
    authorities: list[StrictStr] = Field(default_factory=list, max_length=20)
    tags: list[Literal["pocso", "minor_involved", "sexual_offence", "marked_sensitive", "trafficking"]] = Field(default_factory=list, max_length=10)
    #: the sections the matter is charged under, as the lawyer writes them ("IPC 370", "Section 376(2) IPC", "BNS 143"); only used to REFER
    charged_sections: list[StrictStr] = Field(default_factory=list, max_length=30)

    @field_validator("offence_date", "fir_date", "arrest_date", "application_date")
    @classmethod
    def _iso_date(cls, v: str | None) -> str | None:
        if v is not None:
            date.fromisoformat(v)  # a ValueError is a validation failure: the route answers its one fixed body
        return v

    @field_validator("charged_sections")
    @classmethod
    def _section_length(cls, v: list[str]) -> list[str]:
        if any(not a.strip() or len(a) > 60 for a in v):
            raise ValueError("section length")
        return v

    @field_validator("authorities")
    @classmethod
    def _authority_length(cls, v: list[str]) -> list[str]:
        if any(not a.strip() or len(a) > 300 for a in v):
            raise ValueError("authority length")
        return v


@dataclass(frozen=True)
class Fact:
    fact_id: str
    text: str
    grounds: tuple[str, ...] = ()


@dataclass(frozen=True)
class BailCase:
    """The validated, applicant-side-only case the drafting module sees. Facts keep the lawyer's own order and are numbered F1.. over the
    applicant-side facts only; `facts_set_aside` counts the ticked-other facts that never entered the pack."""

    kind: str  # "regular" | "anticipatory": the drafting module's vocabulary (the request says "bail" | "anticipatory_bail")
    forum: str | None
    dates: dict[str, str]
    applicant: str | None
    state: str | None
    court: str | None
    facts: tuple[Fact, ...]
    authorities: tuple[str, ...]
    facts_set_aside: int = 0
    matter_id: str = ""  # the run id of this request (the engine stores no matter; it is the label the draft carries)

    @property
    def n_facts_excluded(self) -> int:  # the drafting module's name for `facts_set_aside`
        return self.facts_set_aside

    @property
    def parties(self) -> dict[str, str]:
        return {k: v for k, v in (("applicant", self.applicant), ("state", self.state)) if v}

    @property
    def drafting_kind(self) -> str:  # kept as the name the first tests used
        return self.kind


@dataclass(frozen=True)
class Referral:
    code: str  # sensitive_matter | no_applicant_facts | forum_required
    text: str


@dataclass(frozen=True)
class FramingOption:
    act_id: str
    act_name: str
    act_short: str
    section: str
    line: str  # the heading line the engine writes: "Under Section 480 of the Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS)."


@dataclass(frozen=True)
class FramingResult:
    state: Literal["single", "straddle"]
    options: tuple[FramingOption, ...]  # one option when single, both when the deciding dates straddle the cutoff
    deciding: dict[str, str] = field(default_factory=dict)
    cutoff: str = ""
    confirmed_by_lawyer: bool = False
    title: str = ""

    @property
    def chosen(self) -> FramingOption | None:
        return self.options[0] if self.state == "single" else None

    @property
    def status(self) -> str:  # the drafting module's names: status / lines
        return self.state

    @property
    def lines(self) -> list[str]:
        return [o.line for o in self.options]


def load_framing(path: Path | None = None) -> dict[str, Any]:
    """The table, read on every call (a lawyer's edit applies at once). Raises only if the shipped asset is broken."""
    data = json.loads((path or ASSET).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("framing table is not an object")
    return data


_SECTION_NO = re.compile(r"(?<![0-9A-Za-z])(\d{1,3}(?:AB|DA|DB|[A-E])?)(?![0-9])", re.I)


def load_sensitive(path: Path | None = None) -> dict[str, Any]:
    """The sensitive-section table, read on every call (a lawyer's edit applies at once)."""
    data = json.loads((path or SENSITIVE_ASSET).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sensitive table is not an object")
    return data


def sensitive_section(sections: list[str], table: dict[str, Any] | None = None) -> bool:
    """True when any section the lawyer wrote is on the sensitive list. The Act is read from the words ("IPC", "BNS", "POCSO", "IT Act", "ITPA" and
    their long names); a section with no Act named is checked against the IPC and BNS lists (the cautious reading; the table's `named_only` Acts count only when named). Sub-clauses are ignored: 376(2)(n) is 376."""
    t = table or load_sensitive()
    acts = {a: {x.upper() for x in lst} for a, lst in t["acts"].items()}
    aliases = {a: tuple(w.casefold() for w in ws) for a, ws in t["aliases"].items()}
    by_name = set(t.get("refer_by_name", ["POCSO"]))
    bare = [s for a, s in acts.items() if a not in set(t.get("named_only", []))]
    for raw in sections:
        low = raw.casefold()
        named = {a for a, ws in aliases.items() if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low) for w in ws)}
        if named & by_name:
            return True
        m = _SECTION_NO.search(raw)
        if m is None:
            continue
        number = m.group(1).upper()
        pool = [acts[a] for a in (named & acts.keys())] or bare
        if any(number in s for s in pool):
            return True
    return False


def parse_case(req: BailDraftRequest, max_facts: int, matter_id: str = "") -> BailCase | Referral:
    """Rules only. Order: sensitive tag, bail forum, applicant-side facts (the cap on facts is the config's, applied to ticked facts)."""
    if any(t in SENSITIVE_TAGS for t in req.tags):
        return Referral("sensitive_matter", REFER_SENSITIVE)
    if req.charged_sections and sensitive_section(req.charged_sections):
        return Referral("sensitive_section", REFER_SENSITIVE_SECTION)
    if req.kind == "bail" and req.forum is None:
        return Referral("forum_required", REFER_NO_FORUM)
    own = [f for f in req.facts if f.side == "applicant"]
    if not own:
        return Referral("no_applicant_facts", REFER_NO_FACTS)
    own = own[:max_facts]
    facts = tuple(Fact(f"F{i}", f.text.strip(), tuple(dict.fromkeys(f.grounds))) for i, f in enumerate(own, 1))
    dates = {k: v for k, v in (("offence_date", req.offence_date), ("fir_date", req.fir_date), ("arrest_date", req.arrest_date), ("application_date", req.application_date)) if v}
    return BailCase(
        "regular" if req.kind == "bail" else "anticipatory",
        req.forum if req.kind == "bail" else None,
        dates,
        (req.applicant or "").strip() or None,
        (req.state or "").strip() or None,
        (req.court or "").strip() or None,
        facts,
        tuple(a.strip() for a in req.authorities),
        facts_set_aside=sum(1 for f in req.facts if f.side != "applicant"),
        matter_id=matter_id,
    )


def frame(case: BailCase, table: dict[str, Any] | None = None) -> FramingResult:
    """The Act and section by the declared dates. The deciding dates and the cutoff are the TABLE's (a lawyer confirms them); the engine
    only compares ISO dates. All deciding dates before the cutoff: the earlier Act; all on or after it: the later Act; mixed: BOTH, as a straddle."""
    t = table or load_framing()
    cutoff = date.fromisoformat(t["cutoff_date"])
    deciding = {k: case.dates[k] for k in t["deciding_dates"] if k in case.dates}
    eras = {("pre" if date.fromisoformat(v) < cutoff else "post") for v in deciding.values()}
    key = f"bail:{case.forum}" if case.kind == "regular" else "anticipatory_bail"
    sections = t["sections"][key]

    def option(era: str) -> FramingOption:
        act = t["acts"][era]
        return FramingOption(act["id"], act["name"], act["short"], sections[era], f"Under Section {sections[era]} of the {act['name']} ({act['short']}).")

    order = ["pre", "post"] if len(eras) != 1 else sorted(eras)
    return FramingResult(
        "single" if len(eras) == 1 else "straddle",
        tuple(option(e) for e in order),
        deciding,
        t["cutoff_date"],
        bool(t.get("confirmed_by_lawyer")),
        t["titles"]["bail" if case.kind == "regular" else "anticipatory_bail"],
    )
