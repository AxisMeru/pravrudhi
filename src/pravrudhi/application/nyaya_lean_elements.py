"""Track A P3 (`research/prereg/B1-tier1.md`'s PRIMARY arm, corrected by `B1-tier1-amendment-1.md`): the
Python consumer for prabhasa-nyaya's `A3E` wire tag (`lean/Score.lean`'s `scoreA3ELine`, merged main
78bf844). That tag builds a `Contract` entirely from the wire line -- no per-section `Seeds/SectionN.lean`
file, no lookup table the way `nyaya_lean._CONTRACTS` keeps one per case citation -- because P3's corpus is
a 100-section element-list batch and hand-authoring 100 Lean files does not scale. Nothing called that tag
before this module.

Calls prabhasa-nyaya's compiled `score` binary as a subprocess, same as `nyaya_lean.py` -- never imports
that repo's Python (ADR-0001 independence).

Input contract is deliberately "a list of required element name strings", not a path inside
prabhasa-nyaya's `research/nyaya-elements/` worktree layout: this module has no opinion on where a
section's `verified_element_list.json` lives, only on how to read one (`load_required_elements`) and how to
score an answer against the names it contains (`check_elements`). That keeps the ADR-0001 boundary honest
in both directions -- no path assumption into the other repo's tree, same as no Python import from it.

Extraction, deliberately narrow, same limitation `nyaya_lean.check`'s module doc already states for the
citation path: an answer "states" a required element if that element's exact name (case-insensitive)
appears as a substring of the answer text. This can find `grounded` and `omitted` (a required element
whose name never appears), but it structurally CANNOT find `unlicensed` -- a substring search over the
required-element list can never surface a name that isn't in that list. A real hallucinated-element check
needs the answer's own claimed element names extracted independently of the required list (Navya-Nyaya
trace extraction, P2's scope, not this module's). `unlicensed_elements` is returned because the wire
protocol always carries the field and CHARTER §6 says report what the ledger actually computed -- it will
be empty under this extractor by construction, and that emptiness is not evidence of nothing hallucinated.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class NoRequiredElementsError(ValueError):
    """A section's element list (or the caller's argument) has zero required elements -- refused rather
    than silently scoring an answer against an empty contract, which `Contract.omitted`/`.unlicensed` would
    otherwise accept without complaint (everything is vacuously grounded against no requirements)."""


def load_required_elements(verified_element_list_path: Path) -> list[str]:
    """Read the element names off one section's `verified_element_list.json`
    (`research/nyaya-elements/packets/section-<N>/verify/verified_element_list.json` in prabhasa-nyaya's
    worktree, per file, not by path convention -- see module doc)."""
    data = json.loads(verified_element_list_path.read_text())
    names = [e["name"] for e in data.get("elements", [])]
    if not names:
        raise NoRequiredElementsError(f"{verified_element_list_path} has no elements")
    return names


def _run_scorer(line: str, score_bin: Path) -> str:
    proc = subprocess.run(
        [str(score_bin)], input=line + "\n", capture_output=True, text=True, check=True, timeout=30,
    )
    out = proc.stdout.rstrip("\n")
    if not out:
        raise RuntimeError(f"score binary produced no output for input {line!r}; stderr: {proc.stderr[-500:]}")
    return out.split("\n")[0]


def check_elements(
    answer_text: str,
    required_elements: list[str],
    *,
    conduct: str = "the conduct",
    score_bin: Path,
) -> dict[str, Any]:
    """Score `answer_text` against `required_elements` over the `A3E` wire tag.

    Raises `NoRequiredElementsError` if `required_elements` is empty -- never silently scores against an
    empty contract (see the class doc).

    Returns `{verdict, unlicensed_elements, omitted_elements}`. `verdict` is `"grounded"` (every required
    element the extractor found present, nothing flagged) or `"flagged"` (`omitted_elements` and/or
    `unlicensed_elements` non-empty -- see module doc for why `unlicensed_elements` is structurally always
    empty under this extractor).
    """
    if not required_elements:
        raise NoRequiredElementsError("required_elements must be non-empty")

    req_specs = [f"SATISFIES:{conduct}:{el}" for el in required_elements]
    answer_lower = answer_text.lower()
    satisfied = [el for el in required_elements if el.lower() in answer_lower]
    ans_specs = [f"SATISFIES:{conduct}:{el}" for el in satisfied]

    line = "\t".join(["A3E", "live", str(len(req_specs)), *req_specs, *ans_specs])
    out = _run_scorer(line, score_bin)
    parts = out.split("\t")
    if len(parts) != 4 or parts[1] not in ("grounded", "flagged"):
        raise RuntimeError(f"unexpected A3E score output: {out!r}")
    _item_id, verdict, unlicensed_field, omitted_field = parts

    def _element_names(field: str) -> list[str]:
        # Each entry is "SATISFIES:{conduct}:{element}"; the element name is everything after the second
        # ':' (element names may themselves contain ':' in principle -- none in the batch do today, but
        # splitting with maxsplit=2 rather than assuming no further colons is the cheap-to-get-right choice).
        names = []
        for spec in field.split(";") if field else []:
            _tag, _conduct, element = spec.split(":", 2)
            names.append(element)
        return names

    return {
        "verdict": verdict,
        "unlicensed_elements": _element_names(unlicensed_field),
        "omitted_elements": _element_names(omitted_field),
    }
