"""RJ-Bench row -> `JudgeRequest` (Track-C, 2026-09-26, pinned by Lead-2 for v2.2).

An RJ-Bench kept-instance row (`rjbench-rules-v2.6-freeze`@`1b27e01` schema: `instance_id`, `source_path`,
`contract_id`, `accused_identifier`, `unit`, `verdict`, `lower_court_verdict`, `lower_court_quote`,
`appeal_disposition`, `appeal_quote`, `scrubbed_facts_section`, `leak_scan_mechanical_clean`,
`attribution_path` -- see `T2-DELTA-VALIDATION-PREREG-RJBENCH-v1.1-ERRATUM-2026-09-24.md` §2) carries no
`F1..Fn` fact decomposition the way the constructed eval set does -- one scrubbed block of case text is all
there is. That block is `scrubbed_facts_section`.

**The pinned rule**: `JudgeRequest.narrative` is `row["scrubbed_facts_section"]` VERBATIM, byte-for-byte --
never re-encoded, stripped, truncated, or re-derived. This is the only free-text context an RJ-Bench instance
gives the judge; it feeds `nyaya_judges.build_house_prompt`'s "Scenario:" line directly. The narrative-
sensitivity investigation (2026-09-25/26) found this judge's answers can move substantially with narrative
content -- RJ-Bench has no curated placeholder to fall back on, so the scrubbed section IS the narrative,
exactly as leak-scanned and redacted by Track-B's pipeline, with no additional processing here that could
reintroduce or alter what was scrubbed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pravrudhi.application.nyaya_judges import JudgeRequest


def build_rjbench_judge_request(
    row: Mapping[str, Any],
    *,
    contract_id: str,
    element: str,
    statute: str,
    facts: Sequence[tuple[str, str]] = (),
) -> JudgeRequest:
    """Builds one `JudgeRequest` for one (RJ-Bench instance, element) pair.

    `narrative` is pinned to `row["scrubbed_facts_section"]` verbatim (the rule this module exists to
    enforce -- see the module docstring and `test_narrative_is_scrubbed_facts_section_verbatim` below).
    `contract_id`/`element`/`statute`/`facts` are the caller's own resolution (contract/element lookup via
    the pinned Lean registry, per the δ-validation prereg's "Mapping RJ-Bench items to contracts/elements")
    -- this function's only job is the narrative pin, not the full mapping pipeline.
    """
    return JudgeRequest(
        contract_id=contract_id,
        element=element,
        is_denial=False,
        statute=statute,
        narrative=row["scrubbed_facts_section"],
        facts=tuple(facts),
    )
