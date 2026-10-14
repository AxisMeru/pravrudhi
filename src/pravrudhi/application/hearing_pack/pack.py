"""The hearing-preparation PACK: a pure function of the engine's own decide result (#415).

`build_pack(contracts, facts, narrative)` takes the ContractResult dicts exactly as `decide` (assisted) or the core run (not
assisted) produce them, plus the ingested facts, and returns a checklist for a hearing: the ingredients still open with the
engine's reason code, a FIXED prompt id per open ingredient, the cited facts that may defeat the claim (shown as prompts for
review, never as findings) and the legal citations found in the facts, ready to run through the existing verifier.

NOTHING in the result is written by a model or computed by this module beyond a selection and a copy:
  * every ingredient, status, reason and flag is copied from the decide fields; the pack SHOWS the plain reason sentence of the
    signed reason table (REASON_TEXT; a reason it has no sentence for shows GENERIC_REVIEW_TEXT) in the `*_text` display
    fields, and carries the machine `reason_code` in a separate field for tests and the app's mapping; the outcome word is not
    in the pack;
  * `prompt_id` is one of PROMPT_IDS, chosen from the element status by a fixed table; the default strings are a constant
    (signed wording is the reviewers' and the app's; the app maps `prompt_id` to its own signed text);
  * citations come from `citations.parse_citations` over the facts and the narrative, never from a model;
  * no fact, no law and no question is generated.
A contract the app shows by reference only (reference-only contracts) must not have its element wording spelled out, so the
pack carries element ids and 1-based ORDINALS and never any description of the conduct.
"""

from __future__ import annotations

from typing import Any

from pravrudhi.application.citations import parse_citations

PACK_VERSION = 1
GENERIC_REVIEW_TEXT = "Needs your review."  # the sentence for a reason the signed table has no sentence for (never the raw code)
MAX_CITATIONS = 50

# status of a non-denial element -> the fixed prompt id; `established` is never open.
OPEN_PROMPT_BY_STATUS: dict[str, str] = {
    "not_established": "no_fact_found",
    "not_confirmed": "cited_fact_unconfirmed",
    "not_evaluated_second_unavailable": "not_assessed",
    "not_evaluated_gate1_unavailable": "not_assessed",
}
DEFENCE_PROMPT_ID = "defence_cited"
GENERIC_PROMPT_ID = "needs_review"  # a status this module has no fixed prompt for (never a crash)
PROMPT_IDS: tuple[str, ...] = ("no_fact_found", "cited_fact_unconfirmed", "not_assessed", DEFENCE_PROMPT_ID, GENERIC_PROMPT_ID)

# Default strings for a client without the app; the app maps `prompt_id` to its own signed wording (R1). No outcome words.
PROMPT_DEFAULTS: dict[str, str] = {
    GENERIC_PROMPT_ID: GENERIC_REVIEW_TEXT,
    "no_fact_found": "No cited fact was found for this ingredient. Look for a fact that bears on it, or confirm there is none.",
    "cited_fact_unconfirmed": (
        "A fact was cited for this ingredient, but the cited words could not be confirmed in your facts. "
        "Check it against the source."
    ),
    "not_assessed": (
        "This ingredient was not assessed because a check was unavailable. Run the screening again or review it yourself."
    ),
    DEFENCE_PROMPT_ID: "A cited fact may defeat the claim. It is a prompt for your review, not a finding.",
}

# The plain reason SENTENCES of the signed reason table (docs/api/reason-codes.md, contract `reason`): the pack shows these, never
# the codes. A test pins every string to that file, so the table cannot drift.
REASON_TEXT: dict[str, str] = {
    "all_elements_established": (
        "The judge found every condition this provision requires shown in your facts, citing a fact for each "
        "(see each element's `citation_note`), and found no fact that defeats the claim."
    ),
    "denial_established": "The judge found a fact in your case that defeats this claim, and cited it.",
    "missing_element": "The judge did not find at least one required condition shown in your facts.",
    "no_training_statute_text": "We have no statute text for this provision, so we did not judge it.",
    "judge_error": "A judge call failed on at least one condition, so we give no answer.",
    "assembly_lean_mismatch": "Two internal checks disagreed, so we give no answer.",
    "denial_unquotable": (
        "The judge thinks a defeating fact exists but could not cite it in a way we can check. Please have a lawyer look."
    ),
    "second_judge_defeater_disagreement": (
        "The two judges disagree on whether a defeating fact exists. Please have a lawyer look. (deployments "
        "that use two judges only)"
    ),
    "uncertain": "The judge is not sure whether a condition holds. Please have a lawyer look.",
    "uncertain_second_judge": (
        "The second judge is not sure whether a condition holds. Please have a lawyer look. (deployments that "
        "use two judges only)"
    ),
    "second_judge_unavailable": (
        "The second judge was unavailable, so we give a referral, not an answer. (deployments that use two judges only)"
    ),
    "second_judge_not_confirmed": (
        "A check found support in your facts for an ingredient, but a second check did not confirm it, so no "
        "answer is given. Please have a lawyer look. (Only in deployments that run a second check.)"
    ),
    "gate1_unavailable": (
        "The entailment check (a separate check of the cited fact against the claim) was unavailable, so we "
        "give a referral, not an answer."
    ),
    "gate1_not_entailed": (
        "The entailment check (a separate check of the cited fact against the claim) did not find enough "
        "support for it. Please have a lawyer look."
    ),
    "gate1_contradiction": (
        "The entailment check (a separate check of the cited fact against the claim) found the fact "
        "contradicts it. Please have a lawyer look."
    ),
    "contract_not_validated": "This provision is not on the validated list, so we give a referral, not a proof or denial.",
    "input_too_long": (
        "The facts and question together are too long for the checker to read in one go, so we give a "
        "referral, not an answer. Please shorten them or have a lawyer look."
    ),
}

# The decide lists that name an element for review, copied under these flag names.
# (decide list name, key in REASON_TEXT whose sentence says why that element needs review)
FLAG_LISTS: tuple[tuple[str, str], ...] = (
    ("uncertain", "uncertain"),
    ("uncertain_second", "uncertain_second_judge"),
    ("unavailable_second", "second_judge_unavailable"),
    ("gate1_unavailable", "gate1_unavailable"),
    ("gate1_failed", "gate1_not_entailed"),
    ("gate1_contradiction", "gate1_contradiction"),
)

# The `pack` driver tool's JSON schema, frozen here so the agent definition can be built from it (L2A) and a test pins it.
PACK_TOOL_NAME = "pack"
PACK_TOOL_DESCRIPTION = (
    "Build the hearing-preparation pack from the screening decisions. Call it once, last, after decide has run for every "
    "contract. Pass the contract ids exactly as select_contracts returned them. The pack is computed by the engine; "
    "you cannot change it."
)
PACK_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"contract_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
    "required": ["contract_ids"],
    "additionalProperties": False,
}


def _citations(facts: list[dict[str, Any]], narrative: str) -> tuple[list[dict[str, Any]], bool]:
    """Case-law citations in the facts and the narrative, deduplicated by their printed text; each is one ready body for the
    existing existence-only verifier (a single citation, no quote)."""
    seen: dict[str, dict[str, Any]] = {}
    sources = [(str(f["id"]), str(f["text"])) for f in facts] + [("narrative", narrative or "")]
    for src_id, text in sources:
        for c in parse_citations(text):
            if c.year is None:  # a bracket tag, not a printed case citation
                continue
            printed = text[c.span[0] : c.span[1]]
            if len(parse_citations(printed)) != 1:
                continue  # the verifier takes exactly one parseable citation
            entry = seen.setdefault(printed, {"citation": printed, "source_ids": [], "verify_request": {"citation": printed}})
            if src_id not in entry["source_ids"]:
                entry["source_ids"].append(src_id)
    found = list(seen.values())
    return found[:MAX_CITATIONS], len(found) > MAX_CITATIONS


def _contract_pack(c: dict[str, Any]) -> dict[str, Any]:
    elements = list(c.get("elements") or [])
    required = [e for e in elements if not e.get("is_denial")]
    ordinal = {e["element"]: i + 1 for i, e in enumerate(required)}
    denials = [e for e in elements if e.get("is_denial")]
    d_ordinal = {e["element"]: i + 1 for i, e in enumerate(denials)}
    open_ingredients = [
        {
            "element": e["element"],
            "ordinal": ordinal[e["element"]],
            "status": e["status"],
            "prompt_id": OPEN_PROMPT_BY_STATUS.get(e["status"], GENERIC_PROMPT_ID),
            "prompt_text": PROMPT_DEFAULTS[OPEN_PROMPT_BY_STATUS.get(e["status"], GENERIC_PROMPT_ID)],
        }
        for e in required
        if e["status"] != "established"
    ]
    defences = [
        {
            "element": e["element"],
            "ordinal": d_ordinal[e["element"]],
            "fact_id": e.get("fact_id"),
            "prompt_id": DEFENCE_PROMPT_ID,
            "prompt_text": PROMPT_DEFAULTS[DEFENCE_PROMPT_ID],
        }
        for e in denials
        if e["status"] == "established"
    ]
    flags = [
        {
            "element": name,
            "ordinal": ordinal.get(name) or d_ordinal.get(name),
            "flag_text": REASON_TEXT.get(reason_key, GENERIC_REVIEW_TEXT),
        }
        for key, reason_key in FLAG_LISTS
        for name in (c.get(key) or [])
    ]
    return {
        "contract_id": c["contract_id"],
        "reason_code": c["reason"],  # machine field for tests and the app's mapping, NOT for display
        "reason_text": REASON_TEXT.get(c["reason"], GENERIC_REVIEW_TEXT),
        "open_ingredients": open_ingredients,
        "defences_cited": defences,
        "review_flags": flags,
    }


def build_pack(contracts: list[dict[str, Any]], facts: list[dict[str, Any]], narrative: str = "") -> dict[str, Any]:
    """The pack for these decided contracts, in the order given. Pure and deterministic."""
    citations, truncated = _citations(facts, narrative)
    return {
        "pack_version": PACK_VERSION,
        "provenance": "agama",
        "contracts": [_contract_pack(c) for c in contracts],
        "citations_to_check": citations,
        "citations_truncated": truncated,
        "prompt_defaults": {k: PROMPT_DEFAULTS[k] for k in PROMPT_IDS},
    }


def attach_pack(body: Any, fact_texts: list[str], narrative: str) -> Any:
    """The ordinary (core) result plus the pack built from it by the same pure function the `pack` tool uses, for a hearing_prep
    request whose assisted run did not finish. It can never cost the user the ordinary result: anything that is not a screening
    result (an error response) passes through unchanged, and a pack that cannot be built leaves the body as it was."""
    if not (isinstance(body, dict) and isinstance(body.get("contracts"), list)):
        return body
    try:
        facts = [{"id": f"F{i + 1}", "text": t.strip()} for i, t in enumerate(fact_texts)]
        return {**body, "pack": build_pack(body["contracts"], facts, narrative or "")}
    except Exception:  # noqa: BLE001 - the ordinary result is never lost to the pack
        return body
