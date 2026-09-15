"""Composition operator §9 wire (dual-signed design, `docs/composition-scoring-wire-design.md` in
prabhasa-nyaya): the Python consumer for the `COMP` wire tag, which scores an answer's element
assertions against one of the two composed cross-Contract predicates (`compositionForId` in
`lean/Score.lean`) -- `ipc416_composed` (`IPC416.cheats` -> IPC 415's two limb Contracts) and
`bns47_composed` (`BNS47.abetsCommission` -> BNS 46's three route Contracts).

Sibling to `nyaya_lean_registry.py`'s `REG` consumer, same input-format discipline (B1 arm-(c)'s
principle): the caller supplies an explicit Met/Not-Met assertion per element across BOTH the outer
Contract's own elements and every inner route's own elements it wants to claim -- never free text
this module parses itself. Only `True` (Met) entries become `SATISFIES` claims sent to the checker.

Calls prabhasa-nyaya's compiled `score` binary as a subprocess -- never imports that repo's Python
(ADR-0001 independence), same as `nyaya_lean_registry.py`.

Until this module lands and both reviewers sign it, `--list-compositions` on the Lean side is
drift-only: no composition id is scorable through any wire path. This module is the wire path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from pravrudhi.application.nyaya_gold_score import score_bin_path
from pravrudhi.application.nyaya_lean_registry import _CONDUCT, _esc

#: The two composition ids `compositionForId` in `lean/Score.lean` knows. Hand-listed here for fast
#: validation before a subprocess call -- kept honest by `tests/test_nyaya_lean_composition.py`'s
#: drift test, which reads the binary's OWN `--list-compositions` output and asserts it equals this
#: set exactly, the same no-drift discipline `KNOWN_CONTRACT_IDS` already established.
KNOWN_COMPOSITION_IDS: frozenset[str] = frozenset({"ipc416_composed", "bns47_composed"})


class UnknownCompositionError(KeyError):
    """`composition_id` is not one of `KNOWN_COMPOSITION_IDS` -- raised rather than falling back to
    any default, same discipline `nyaya_lean_registry.UnknownContractError` already established."""


def _run_scorer(line: str, score_bin: Path) -> str:
    proc = subprocess.run(
        [str(score_bin)], input=line + "\n", capture_output=True, text=True, check=True, timeout=30,
    )
    out = proc.stdout.rstrip("\n")
    if not out:
        raise RuntimeError(f"score binary produced no output for input {line!r}; stderr: {proc.stderr[-500:]}")
    return out.split("\n")[0]


def check_composition(
    assertions: dict[str, bool],
    composition_id: str,
    *,
    root: Path | None = None,
    score_bin: Path | None = None,
) -> dict[str, Any]:
    """Score `assertions` (element name -> Met=`True`/Not-Met-or-unaddressed=`False`) against
    `composition_id`'s `Composition`, over the `COMP` wire tag.

    Raises `UnknownCompositionError` if `composition_id` is not one of `KNOWN_COMPOSITION_IDS` --
    never falls back to a default composition, and never calls the subprocess for an id it already
    knows is bad (same discipline `check_registry` already established).

    Returns `{verdict, route_of_record, route_omission_counts, tie_break_rule, ungrounded_claims,
    omitted_claims, refuted_claims}`. `verdict` is `"grounded"` (composed adequacy: nothing
    unlicensed, nothing omitted, not refuted) or `"flagged"`. `route_of_record` is the `contract_id`
    of the single route that grounds or is reported against, a comma-joined list when more than one
    route is independently available (declared order, no precedence meaning), or `"none"` when the
    reading is silent on the inner family. `route_omission_counts` is every declared route's own
    omission count (`{contract_id: count}`), for auditing the tie-break, not just trusting it.
    `omitted_claims`/`refuted_claims` carry their SOURCE label (`"outer"`, `"bridge"`, or the inner
    route's own `contract_id`) alongside the wire-encoded claim -- never presented as one
    undifferentiated list, per the dual-signed design's provenance requirement.
    """
    if composition_id not in KNOWN_COMPOSITION_IDS:
        raise UnknownCompositionError(
            f"unknown composition_id {composition_id!r}; known ids: {sorted(KNOWN_COMPOSITION_IDS)}"
        )

    bin_path = score_bin or score_bin_path(root or Path.cwd())
    claims = [
        f"G_SATISFIES(E({_esc(_CONDUCT)},AC),E({_esc(element)},EL))"
        for element, met in assertions.items() if met
    ]
    line = "\t".join(["COMP", "live", composition_id, *claims])
    out = _run_scorer(line, bin_path)
    parts = out.split("\t")
    if len(parts) != 8 or parts[0] == "MALFORMED":
        raise RuntimeError(f"unexpected COMP score output: {out!r}")
    _item_id, verdict, route_of_record, route_counts_field, tie_break_rule, ungrounded, omitted, refuted = parts

    def _split(field: str) -> list[str]:
        return field.split(";") if field else []

    def _parse_counts(field: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in _split(field):
            cid, _, count = entry.partition("=")
            out[cid] = int(count)
        return out

    def _parse_refuted(field: str) -> list[dict[str, str]]:
        """`<label>:<wire claim>` pairs -- `label` is `"outer"` or the bare `contract_id` (no
        further prefix; contract_ids never contain `:`, the reserved-character guard on the Lean
        side proves it, so a first-`:` split is safe)."""
        result = []
        for entry in _split(field):
            label, _, claim = entry.partition(":")
            result.append({"source": label, "claim": claim})
        return result

    def _parse_omitted(field: str) -> list[dict[str, str]]:
        """Same shape as `_parse_refuted`, except the Lean side's `inner` label is itself
        `inner:<contract_id>` (two colons before the claim: `inner:ipc415_property:G_SATISFIES(...)`)
        -- stripped here so `source` is consistently either `"outer"`, `"bridge"`, or a bare
        `contract_id`, matching `refuted_claims`' own source convention rather than leaking the
        wire's `inner:` marker into the Python-side shape."""
        result = []
        for entry in _split(field):
            if entry.startswith("inner:"):
                contract_id, _, claim = entry[len("inner:") :].partition(":")
                result.append({"source": contract_id, "claim": claim})
            else:
                label, _, claim = entry.partition(":")
                result.append({"source": label, "claim": claim})
        return result

    return {
        "verdict": verdict,
        "route_of_record": route_of_record,
        "route_omission_counts": _parse_counts(route_counts_field),
        "tie_break_rule": tie_break_rule,
        "ungrounded_claims": _split(ungrounded),
        "omitted_claims": _parse_omitted(omitted),
        "refuted_claims": _parse_refuted(refuted),
    }
