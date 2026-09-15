"""T5b atomic wire (Track A's own input-format call, per `contractForId`'s own doc comment on the
prabhasa-nyaya side): the Python consumer for the `REG` wire tag, which scores an answer's element
assertions against one of the eleven BNS/IPC registry Contracts (`contractForId` in `lean/Score.lean`).

Distinct from `nyaya_lean.py`'s citation-checking path (`A3N` tag, `_CONTRACTS`): these Contracts' required
lists are element-satisfaction claims, not case citations, so `nyaya_lean.check` does not fit them --
this module is the sibling `contractForId`'s own doc comment asked for.

Input format, decided here (B1 arm-(c)'s principle, not a new invention): the CALLER supplies an explicit
Met/Not-Met assertion per element (`assertions: dict[str, bool]`) -- never a free-text answer this module
parses itself, which would put model-read extraction behind a "Lean-checked" label. Only `True` (Met)
entries become `SATISFIES` claims sent to the checker; a `False` (Not Met) or simply-absent element is
never sent, and surfaces through the checker's own `omitted` bucket -- there is no separate "this is a
denial" wire marker; Lean decides that from the Contract's own `denials` data (`Score.lean`'s
`scoreREGLine` doc comment states this on the Lean side too).

Calls prabhasa-nyaya's compiled `score` binary as a subprocess -- never imports that repo's Python
(ADR-0001 independence), same as `nyaya_lean.py` and `nyaya_gold_score.py`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from pravrudhi.application.nyaya_gold_score import score_bin_path

#: The eleven registry Contract ids `contractForId` in `lean/Score.lean` knows, per the T5b exit criterion.
#: Hand-listed here for fast validation before a subprocess call -- kept honest by
#: `tests/test_nyaya_lean_registry.py`'s drift test, which reads the binary's OWN `--list-contracts` output
#: and asserts it equals this set exactly, rather than trusting this list on its own (the lead's explicit
#: no-drift requirement: the test reads from the binary, this constant is not the source of truth).
KNOWN_CONTRACT_IDS: frozenset[str] = frozenset(
    {
        "ipc405_misappropriation", "ipc405_use_or_disposal", "ipc405_wilfully_suffers",
        "ipc415_property", "ipc415_damaging_act", "ipc416",
        "ipc182_misdirected_act", "ipc182_abuse_of_power",
        "bns69", "bns47", "bns85",
    }
)

#: Every registry Contract's own `conduct` entity is literally this string (confirmed identical across all
#: seven `Seeds/*.lean` files before choosing the wire design, not assumed) -- fixed here rather than
#: threaded as a parameter, since a caller supplying a different conduct string would build a claim that
#: can never match any registry Contract's axioms, a footgun with no legitimate use.
_CONDUCT = "the conduct in the facts"


class UnknownContractError(KeyError):
    """`contract_id` is not one of `KNOWN_CONTRACT_IDS` -- raised rather than falling back to any default,
    same discipline `nyaya_lean.UnknownContractError` already established."""


def _esc(s: str) -> str:
    """Percent-escape the grammar's four reserved characters, in the SAME order `Score.lean`'s `escField`
    uses (`%` first, so an escape sequence this function introduces is never itself re-escaped by a later
    step) -- a Python-side re-derivation must match the Lean side exactly or the wire silently miscommunicates,
    so this is tested by round-tripping a hostile string through the real binary, not just by inspection."""
    return s.replace("%", "%25").replace("(", "%28").replace(")", "%29").replace(",", "%2C")


def _run_scorer(line: str, score_bin: Path) -> str:
    proc = subprocess.run(
        [str(score_bin)], input=line + "\n", capture_output=True, text=True, check=True, timeout=30,
    )
    out = proc.stdout.rstrip("\n")
    if not out:
        raise RuntimeError(f"score binary produced no output for input {line!r}; stderr: {proc.stderr[-500:]}")
    return out.split("\n")[0]


def describe_contract(contract_id: str, *, root: Path | None = None, score_bin: Path | None = None) -> list[str]:
    """The element names `contract_id`'s `required` list names, read live from the binary's own
    `--describe-contract` (never hand-copied -- the same no-drift principle `KNOWN_CONTRACT_IDS`'s test
    applies to the id set, extended to element names). Raises `UnknownContractError` if the binary itself
    reports the id unknown (`UNKNOWN_CONTRACT_ID` on stdout), never silently returning an empty list."""
    if contract_id not in KNOWN_CONTRACT_IDS:
        raise UnknownContractError(f"unknown contract_id {contract_id!r}; known ids: {sorted(KNOWN_CONTRACT_IDS)}")
    bin_path = score_bin or score_bin_path(root or Path.cwd())
    proc = subprocess.run(
        [str(bin_path), "--describe-contract", contract_id],
        capture_output=True, text=True, check=True, timeout=30,
    )
    lines = proc.stdout.rstrip("\n").split("\n") if proc.stdout.strip() else []
    if lines and lines[0].startswith("UNKNOWN_CONTRACT_ID"):
        raise UnknownContractError(f"binary refused contract_id {contract_id!r}: {lines[0]}")
    return lines


def check_registry(
    assertions: dict[str, bool],
    contract_id: str,
    *,
    root: Path | None = None,
    score_bin: Path | None = None,
) -> dict[str, Any]:
    """Score `assertions` (element name -> Met=`True`/Not-Met-or-unaddressed=`False`) against
    `contract_id`'s registry `Contract`, over the `REG` wire tag.

    Raises `UnknownContractError` if `contract_id` is not one of `KNOWN_CONTRACT_IDS` -- never falls back
    to a default contract.

    Returns `{verdict, denied_claims, unlicensed_claims, omitted_claims}`. `verdict` is `"grounded"`
    (nothing flagged) or `"flagged"` (one or more of the three lists below is non-empty). `denied_claims`
    is non-empty only when an asserted-Met element happens to match one of the Contract's own `denials` --
    a REFUTATION (the sources say the opposite), stronger than `unlicensed_claims` (an element outside the
    axiom list -- a hallucination) or `omitted_claims` (a required element never asserted Met at all). The
    product must not present these three as one finding, per `Adequacy.lean`'s own `Contract.Refuted` doc.
    """
    if contract_id not in KNOWN_CONTRACT_IDS:
        raise UnknownContractError(f"unknown contract_id {contract_id!r}; known ids: {sorted(KNOWN_CONTRACT_IDS)}")

    bin_path = score_bin or score_bin_path(root or Path.cwd())
    claims = [
        f"G_SATISFIES(E({_esc(_CONDUCT)},AC),E({_esc(element)},EL))"
        for element, met in assertions.items() if met
    ]
    line = "\t".join(["REG", "live", contract_id, *claims])
    out = _run_scorer(line, bin_path)
    parts = out.split("\t")
    if len(parts) != 5 or parts[0] == "MALFORMED":
        raise RuntimeError(f"unexpected REG score output: {out!r}")
    _item_id, verdict, denied, unlicensed, omitted = parts

    def _split(field: str) -> list[str]:
        return field.split(";") if field else []

    return {
        "verdict": verdict,
        "denied_claims": _split(denied),
        "unlicensed_claims": _split(unlicensed),
        "omitted_claims": _split(omitted),
    }
