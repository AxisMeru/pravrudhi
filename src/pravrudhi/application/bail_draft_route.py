# ruff: noqa: E501
"""A4 route support: the bail draft (flag `wf_a4_bail_drafting`). Imported only when the flag is on.

Glue between the request and the drafting module (`bail_draft`, Track A's): consent, the deterministic referrals and the statute framing
(`bail_draft_input`), the optional drafting completer built from the config, a per-day spend ceiling, and the response wrapper. The engine stores
no matter and no fact; nothing of the request is logged or audited (the audit row carries the run id only). A draft is a PREVIEW for a lawyer
to rewrite; the label, the referral strings and every other customer-visible string are pending R1 (see `bail_draft_input`).
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from pravrudhi.application import bail_draft_input as I
from pravrudhi.application.bail_draft_config import BailDraftConfig

ROUTE = I.ROUTE
BailDraftRequest = I.BailDraftRequest
BAD_BODY = {"error": "bad_request"}
Completer = Callable[[str, str, int], Any]


class DraftError(Exception):
    """A refusal with a stable machine code and an HTTP status for the route (the same shape as `MatterError`)."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code, self.status_code = code, status_code


class FixedValidationRoute(APIRoute):
    """Validation failures (size, type, extra keys, a bad date, bad JSON) answer ONE fixed 422 body: the framework default echoes the input and
    the field path back, and this request carries a lawyer's facts."""

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


class DayCeiling:
    """Counted spend of this process in one UTC day. Over the ceiling the next draft is the template only (no model call). In memory: a restart
    resets it, which is why the per-matter cap and the pinned prices, not this, are the hard guard; the pooled spend guards sit above it."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._day = ""
        self._spent = 0.0

    def _roll(self) -> None:
        today = datetime.fromtimestamp(self._clock(), UTC).strftime("%Y-%m-%d")
        if today != self._day:
            self._day, self._spent = today, 0.0

    def room(self, ceiling: float) -> bool:
        with self._lock:
            self._roll()
            return self._spent < ceiling

    def add(self, usd: float) -> None:
        with self._lock:
            self._roll()
            self._spent += max(0.0, usd)


def _drafter() -> Any:
    """The drafting module, imported on first use (a seam the tests replace)."""
    import importlib

    return importlib.import_module("pravrudhi.application.bail_draft")


def make_completer(cfg: BailDraftConfig, drafter: Any, env: dict[str, str] | None = None) -> Completer | None:
    """The configured completer, or None (template only). The key is read from the NAMED environment variable, never from the yaml."""
    if not cfg.drafting_configured:
        return None
    key = (env if env is not None else os.environ).get(cfg.api_key_env or "", "")
    if not key:
        return None
    from pravrudhi.models.openai_compat import ChatClient

    client = ChatClient(base_url=cfg.base_url or "", model=cfg.model or "", api_key=key, timeout_s=int(cfg.timeout_s), thinking=None)

    def complete(system: str, user: str, max_tokens: int) -> Any:
        r = client.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], temperature=1.0, max_tokens=max_tokens)
        return drafter.Completion(r.text, int(r.prompt_tokens or 0), int(r.completion_tokens or 0), r.model, str(r.finish_reason or ""))

    return complete


def _framing_block(fr: I.FramingResult) -> dict[str, Any]:
    return {
        "status": fr.state,
        "options": [{"act": o.act_short, "act_name": o.act_name, "section": o.section, "line": o.line} for o in fr.options],
        "deciding_dates": fr.deciding,
        "cutoff_date": fr.cutoff,
        "confirmed_by_lawyer": fr.confirmed_by_lawyer,
        "unconfirmed_line": None if fr.confirmed_by_lawyer else I.FRAMING_UNCONFIRMED,
    }


def draft_for_request(
    req: I.BailDraftRequest,
    cfg: BailDraftConfig,
    run_id: str,
    ceiling: DayCeiling,
    *,
    completer: Completer | None = None,
    table: dict[str, Any] | None = None,
    drafter: Any = None,
) -> dict[str, Any]:
    """The response for one request. Raises `DraftError` (400 consent_required); every other refusal is a 200 `referred` answer with no draft."""
    if req.consent is not True:
        raise DraftError(I.CONSENT_REQUIRED, "Consent is required before a draft is made.", 400)
    base: dict[str, Any] = {"run_id": run_id, "client_data": True, "preview": True, "label": I.PREVIEW_LABEL}
    case = I.parse_case(req, cfg.max_facts, matter_id=run_id)
    if isinstance(case, I.Referral):
        return {**base, "state": "referred", "referral": {"code": case.code, "text": case.text}, "draft": None}
    fr = I.frame(case, table)
    if fr.state == "straddle":
        return {**base, "state": "referred", "referral": {"code": "dates_straddle", "text": I.REFER_STRADDLE}, "framing": _framing_block(fr), "draft": None}
    mod = drafter or _drafter()
    use = completer
    if use is None and ceiling.room(cfg.usd_per_day):
        use = make_completer(cfg, mod)
    if use is not None and not ceiling.room(cfg.usd_per_day):
        use = None  # over the day ceiling: the template only, no call
    out = mod.draft(case, fr, use, cfg, run_id)
    ceiling.add(float(out.get("cost_usd") or 0.0))
    return {**base, "state": out.get("state"), "framing": _framing_block(fr), "facts_set_aside": case.facts_set_aside, "draft": out}
