"""Track A P1/P1b (ADR-0003): the live-path Lean checker, generalized to any contract prabhasa-nyaya's
compiled `score` binary knows via the `A3N` wire tag (`lean/Score.lean`'s `contractFor`), not hard-wired to
one fixture.

Calls prabhasa-nyaya's compiled `score` binary as a subprocess -- never imports its Python: pravrudhi has no
runtime dependency on that repo (ADR-0001's independence rule, the same boundary `nyaya_gold_score.py`
already holds for the gold-set scoring path).

`contract_id` is the seed index `contractFor` in `lean/Score.lean` knows (currently "0" through "7"), as a
string matching the wire protocol's own `seedSpec` field. Unknown ids raise `UnknownContractError` rather
than silently falling back to any default -- P1b's fix for exactly that failure mode.

`not_formalisable_count` is the Lean binary's OWN reported output (the `A3N` tag's 6th field,
`lean/Score.lean`'s `scoreA3NLine`), never a Python-side constant -- P1b fixed a CHARTER §6 violation where
an earlier version of this module (P1) hardcoded the count instead of reading it from the ledger's own
computation. The `A3N` tag is additive alongside the existing `A3` tag every prior gate already depends on;
neither this module nor P1b's change touches that wire contract.

Extraction, deliberately narrow (unchanged in spirit from P1, generalized in mechanism): one claim type (a
case citation), looked up per contract_id from `_CONTRACTS` below. Extending to more claim types
(establishes/binds/satisfies extracted from free text generally) is P2's Navya-Nyaya trace work, not this
module's.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pravrudhi.application.nyaya_gold_score import score_bin_path


class UnknownContractError(KeyError):
    """`contract_id` is not one `_CONTRACTS` knows -- raised rather than falling back to any default."""


@dataclass(frozen=True)
class _ContractInfo:
    #: `contractFor seedIdx` in `lean/Score.lean` -- the seed index this contract_id maps to on the wire.
    seed_index: int
    case_name: str
    reporter: str
    #: The `.satisfies` claim `contractFor`'s own `required` list needs in addition to the citation, keyed
    #: by a literal needle phrase in the answer text -- see module doc's extraction scope note. `None` when
    #: the contract's `required` is citation-only (not true for either fixture below, but kept general).
    required_satisfies_needle: str | None
    required_satisfies_claim_field: str | None


#: `Tests.lean`'s IPC 378 example (contractFor 0) and `Seeds/Seed5.lean`'s example (contractFor 5) -- the
#: two contracts this module currently knows. Adding a contract_id means adding an entry here, once its
#: case/reporter/required-claim shape is read off the corresponding .lean file, the same way these two were.
_CONTRACTS: dict[str, _ContractInfo] = {
    "0": _ContractInfo(
        seed_index=0, case_name="Pierce v. State", reporter="F.2d",
        required_satisfies_needle="dishonest intention",
        required_satisfies_claim_field="SATISFIES:taking the purse:dishonest intention",
    ),
    "5": _ContractInfo(
        seed_index=5, case_name="Rowan v. Fisk", reporter="U.S.",
        required_satisfies_needle="made in the ordinary course of business",
        required_satisfies_claim_field="SATISFIES:the statement:made in the ordinary course of business",
    ),
}

#: Public, for callers (e.g. the API layer) that need to validate a contract_id before dispatching any
#: work, rather than only discovering it's unknown after a check() call raises.
KNOWN_CONTRACT_IDS: frozenset[str] = frozenset(_CONTRACTS)


def _run_scorer(line: str, score_bin: Path) -> str:
    proc = subprocess.run(
        [str(score_bin)], input=line + "\n", capture_output=True, text=True, check=True, timeout=30,
    )
    out = proc.stdout.rstrip("\n")
    if not out:
        raise RuntimeError(f"score binary produced no output for input {line!r}; stderr: {proc.stderr[-500:]}")
    # A trailing tab (an empty final field, e.g. nothing omitted) is significant and must survive splitting
    # into lines/fields -- only the newline is stripped here, never `.strip()`'s broader whitespace trim,
    # which would silently eat that tab and misalign every field after it (the bug P1's own TDD caught).
    return out.split("\n")[0]


def check(answer_text: str, contract_id: str, *, root: Path | None = None, score_bin: Path | None = None) -> dict[str, Any]:
    """Check one vendor's free-text answer against the contract named by `contract_id`, over prabhasa-nyaya's
    compiled Lean scorer's `A3N` wire tag.

    Raises `UnknownContractError` if `contract_id` is not one this module knows -- never falls back to a
    default contract.

    Returns `{verdict, unlicensed_claims, not_formalisable_count}`. `verdict` is one of:
    `"licensed"` (a citation was found and the contract licenses it, and nothing else was flagged),
    `"unlicensed"` (a citation was found that the contract does not license -- named in `unlicensed_claims`),
    `"refuted"` (the answer asserted something the contract's sources explicitly deny),
    `"omitted"` (the contract requires a claim this answer never states),
    `"no_citation_found"` (the answer never names this contract's case with a citation in its reporter at
    all -- a gap in what was checked, not a false grounding of something never asserted).
    """
    info = _CONTRACTS.get(contract_id)
    if info is None:
        raise UnknownContractError(f"unknown contract_id {contract_id!r}; known ids: {sorted(_CONTRACTS)}")

    bin_path = score_bin or score_bin_path(root or Path.cwd())
    cite_pattern = re.compile(re.escape(info.case_name) + r",?\s*(\d+)\s+" + re.escape(info.reporter) + r"\s*(\d+)")
    match = cite_pattern.search(answer_text)

    claim_fields: list[str] = []
    if match is not None:
        volume, page = int(match.group(1)), int(match.group(2))
        claim_fields.append(f"CITES:{info.case_name}:{volume}:{info.reporter}:{page}")
    if info.required_satisfies_needle and info.required_satisfies_needle in answer_text:
        claim_fields.append(info.required_satisfies_claim_field)  # type: ignore[arg-type]

    # Always call the scorer, even with no citation claim: not_formalisable_count is the checked contract's
    # own property, real either way -- reporting it only reflects what the contract itself declares, never a
    # stand-in for a call this function chose to skip (CHARTER §6, the same discipline the A3N wire tag
    # itself exists to satisfy).
    line = "\t".join(["A3N", "live", str(info.seed_index), *claim_fields])
    out = _run_scorer(line, bin_path)
    parts = out.split("\t")
    if len(parts) != 6 or parts[0] == "MALFORMED":
        raise RuntimeError(f"unexpected score output: {out!r}")
    _item_id, _lean_verdict, refuted, ungrounded, omitted, not_formalisable_count = parts
    nf_count = int(not_formalisable_count)

    if match is None:
        return {"verdict": "no_citation_found", "unlicensed_claims": [], "not_formalisable_count": nf_count}
    if ungrounded:
        return {"verdict": "unlicensed", "unlicensed_claims": ungrounded.split(";"), "not_formalisable_count": nf_count}
    if refuted:
        return {"verdict": "refuted", "unlicensed_claims": [], "not_formalisable_count": nf_count}
    if omitted:
        return {"verdict": "omitted", "unlicensed_claims": [], "not_formalisable_count": nf_count}
    return {"verdict": "licensed", "unlicensed_claims": [], "not_formalisable_count": nf_count}
