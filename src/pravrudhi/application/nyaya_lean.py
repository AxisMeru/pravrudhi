"""Track A P1 (ADR-0003): the first live-path Lean checker, wired for gate A3's own IPC 378 example --
`lean/PrabhasaNyaya/Tests.lean` in `prabhasa-nyaya`, seed index 0 in `lean/Score.lean`'s `contractFor`.

Calls prabhasa-nyaya's compiled `score` binary as a subprocess, using the SAME `A3` wire protocol
`src/prabhasa_nyaya/score_a3.py` already uses for gate A3's own scoring -- this checker and that gate's
evidence share one wire encoding rather than two that could silently drift apart. Never imports
prabhasa-nyaya's Python: pravrudhi has no runtime dependency on that repo (ADR-0001's independence rule,
the same boundary `nyaya_gold_score.py` already holds for the gold-set scoring path).

Scope, deliberately narrow. This is P1 of the Track A redirect: one fixture (the IPC 378 theft example from
`Tests.lean`), one extracted claim type (a case citation). The done-when it answers to is "does the wire work
end to end, across two vendors" -- not "does this generalise to arbitrary legal questions". Extending to more
fixtures and claim types (establishes/binds/satisfies, `notFormalisable` computed rather than looked up) is
P2's Navya-Nyaya trace work, not this module's.

`not_formalisable_count` is NOT reported by the Lean wire protocol at all (`scoreA3Line` in `Score.lean` has
no field for it) -- it is looked up here from `Tests.lean`'s own committed `contractForA3.notFormalisable`,
which has exactly one entry as of this writing. If that contract's `notFormalisable` list ever changes, this
constant must change with it; there is no dynamic check catching a drift, which is exactly why this stays a
one-fixture slice rather than something the general case could rely on silently.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from pravrudhi.application.nyaya_gold_score import score_bin_path

#: `Tests.lean`'s own IPC 378 fixture, read directly off that file rather than re-derived: the case name and
#: reporter `contractForA3`'s axioms license, and the ONE volume/page pair that is the licensed citation --
#: anything else found in that reporter for that case name is, by construction, not what the axioms cite.
_CASE_NAME = "Pierce v. State"
_REPORTER = "F.2d"
_LICENSED_VOLUME = 470
_LICENSED_PAGE = 798
#: `Tests.lean`'s `contractForA3.notFormalisable` has exactly one entry as of this writing (see module
#: docstring): not computed from the wire, which has no field for it.
_NOT_FORMALISABLE_COUNT = 1
#: `contractFor 0` in `lean/Score.lean` -- the IPC 378 example is seed index 0.
_SEED_INDEX = 0

_CITE_PATTERN = re.compile(re.escape(_CASE_NAME) + r",?\s*(\d+)\s+" + re.escape(_REPORTER) + r"\s*(\d+)")

#: `contractForA3.required` in `Tests.lean` is exactly this one claim (the theft answer's dishonest-intention
#: element) -- so a citation-only Reading is correctly `omitted`, not `licensed`, unless the answer also
#: states this. Detected by the same needle phrase `Tests.lean`'s own `taking`/`dishonestly` entities name,
#: not derived from the answer text beyond that literal check (this fixture's narrow scope, see module doc).
_DISHONEST_INTENTION_NEEDLE = "dishonest intention"
_TAKING_CLAIM = "SATISFIES:taking the purse:dishonest intention"


def _run_scorer(line: str, score_bin: Path) -> str:
    proc = subprocess.run(
        [str(score_bin)], input=line + "\n", capture_output=True, text=True, check=True, timeout=30,
    )
    out = proc.stdout.rstrip("\n")
    if not out:
        raise RuntimeError(f"score binary produced no output for input {line!r}; stderr: {proc.stderr[-500:]}")
    # A trailing tab (an empty final field, e.g. nothing omitted) is significant and must survive splitting
    # into lines/fields -- only the newline `run` on stdin/stdout adds is stripped here, never `.strip()`'s
    # broader whitespace trim, which would silently eat that tab and misalign every field after it.
    return out.split("\n")[0]


def check_ipc378(answer_text: str, *, root: Path | None = None, score_bin: Path | None = None) -> dict[str, Any]:
    """Check one vendor's free-text answer to the IPC 378 theft question against `Tests.lean`'s own
    `contractForA3`, over prabhasa-nyaya's compiled Lean scorer.

    Returns `{verdict, unlicensed_claims, not_formalisable_count}`. `verdict` is one of:
    `"licensed"` (a citation was found and the contract licenses it, and nothing else was flagged),
    `"unlicensed"` (a citation was found that the contract does not license -- named in `unlicensed_claims`),
    `"refuted"` (the answer asserted something the contract's sources explicitly deny),
    `"omitted"` (the contract requires a claim this answer never states),
    `"no_citation_found"` (the answer never names `Pierce v. State` with a citation in the `F.2d` reporter at
    all -- a gap in what was checked, not a false grounding of something never asserted).
    """
    bin_path = score_bin or score_bin_path(root or Path.cwd())
    match = _CITE_PATTERN.search(answer_text)
    if match is None:
        return {"verdict": "no_citation_found", "unlicensed_claims": [], "not_formalisable_count": _NOT_FORMALISABLE_COUNT}

    volume, page = int(match.group(1)), int(match.group(2))
    claim_fields = [f"CITES:{_CASE_NAME}:{volume}:{_REPORTER}:{page}"]
    if _DISHONEST_INTENTION_NEEDLE in answer_text:
        claim_fields.append(_TAKING_CLAIM)
    line = "\t".join(["A3", "p1-live", str(_SEED_INDEX), *claim_fields])
    out = _run_scorer(line, bin_path)

    parts = out.split("\t")
    if len(parts) != 5 or parts[0] == "MALFORMED":
        raise RuntimeError(f"unexpected score output: {out!r}")
    _item_id, _lean_verdict, refuted, ungrounded, omitted = parts

    if ungrounded:
        return {
            "verdict": "unlicensed",
            "unlicensed_claims": ungrounded.split(";"),
            "not_formalisable_count": _NOT_FORMALISABLE_COUNT,
        }
    if refuted:
        return {
            "verdict": "refuted",
            "unlicensed_claims": [],
            "not_formalisable_count": _NOT_FORMALISABLE_COUNT,
        }
    if omitted:
        return {
            "verdict": "omitted",
            "unlicensed_claims": [],
            "not_formalisable_count": _NOT_FORMALISABLE_COUNT,
        }
    return {"verdict": "licensed", "unlicensed_claims": [], "not_formalisable_count": _NOT_FORMALISABLE_COUNT}
