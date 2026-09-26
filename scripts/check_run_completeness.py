"""CI guard (adopted standing rule, 2026-09-26): every scored result states `n_planned`, `n_scored` and its
error counts, and a run with `n_scored < n_planned` is reported as TRUNCATED and labelled with its walk
order. Lead-2's invitation, which this implements: refuse to let a headline number stand on a short
denominator unless the result says out loud that it is short.

WHY A GUARD AND NOT A CONVENTION. A truncated run is currently indistinguishable from a finished one: the
T2 harnesses catch their latency circuit breaker, print how far they got to stderr, and then fall through to
the same seal-and-write block -- same filename, a valid sha256 over the prefix, `RAW OUTPUTS SEALED`
printed, exit 0. Nothing downstream can tell 55 rows of a planned 1519 from 1519 of 1519. (That fall-through
is Track-C's fix, in their own PR, not this one; this guard is the reader-side half and lands independently.)

THE THREE DESIGN POINTS THIS GUARD IS BUILT AROUND, each one a thing a weaker version would get wrong:

1. `n_planned` IS NEVER TAKEN FROM THE RESULT'S OWN CLAIM. A truncated run that writes `n_planned: 55`
   would pass its own check. The planned count is DERIVED from the committed selection/manifest artefact
   the result names, after that artefact's sha256 is verified against the pin the result carries -- the same
   fail-closed discipline as prabhasa-nyaya's `p2b_preflight._load_component` (`src/prabhasa_nyaya/
   p2b_preflight.py:166-169`, on-disk row count vs manifest pin, and `load_and_verify_eval_set` at
   `:173-199`, where one mismatched component refuses the WHOLE load), applied to OUTPUTS instead of INPUTS,
   which is the gap that survey found. The result must ALSO state `n_planned`, because the standing rule
   says it must and because both denominators travelling together is what makes a short one impossible to
   miss (prabhasa-nyaya's `research/gates/P2b/configC/4b_alone_tau_sweep_results.json` publishes
   `checker_pass_n: 371` beside `checker_pass_377: 377` for exactly that reason) -- and a stated `n_planned`
   that DISAGREES with the manifest is a violation, not a preference.

2. A MISSING FIELD IS A REJECTION, NEVER A PASS. Every required field absent, wrong-typed, or negative
   fails. This is deliberately the opposite of a default: the fail-open-default class this repo spent
   2026-09-26 auditing (`scripts/check_fail_open_defaults.py`) is precisely "the field was not there, so it
   became a number, so the item counted as fine". A guard whose own missing-field path is a pass is
   decorative, and every artefact in existence would sail through it.

3. `n_scored` COUNTS USABLE SCORES, NOT ROWS PRESENT. A row whose score field is absent, `None`, an empty
   or whitespace string, a bool, a non-finite float, or an unparseable string is NOT scored. It is counted
   as a TERMINAL error, reported separately from a row that erred, was retried, and then succeeded -- the
   distinction the 32B Config C run needed and could not express, where two timeouts were individually
   retried to success and the run still reported `n_error: 0`.

WHAT IT STILL CANNOT CHECK, said plainly rather than implied away: that the artefact `planned_from` names
is the RIGHT selection artefact. It must be a different file from the output (that is enforced -- aiming
both at the output was a real hole in this guard's own first draft), its sha256 must match the pin, and its
row count must match the stated `n_planned`; but choosing some other committed file that happens to hold the
convenient number is a review question, not a mechanical one. The guard narrows the room for that; it does
not close it.

AND THE FOURTH, ADDED BY THE TEAM AFTER THIS GUARD WAS SPECIFIED: a number and the file it came from travel
together. The result records the sha256 of the file whose rows were counted, this guard verifies it, and a
mismatch is a violation -- so `n_scored` can never be read as describing a file it was not computed over.

WHAT THE GUARD READS. A "result artefact" is a tracked `.json` file carrying a top-level
`run_completeness` block; see `SCHEMA` below for the exact shape and `check_artefact` for every rule. It is
also, and separately, every tracked file whose NAME says it is a run's result (`RESULT_NAME_PATTERNS`): such
a file carrying no `run_completeness` block at all is itself a violation, because otherwise the one way to
escape this guard forever is to not opt in to it. That second prong is what the baseline exists for.

VACUITY, STATED RATHER THAN HIDDEN. This repository commits ZERO result artefacts today: results never
write inside the repo (`PRAVRUDHI_T2_RESULTS_DIR` is mandatory in every T2 script and has no in-repo
default), so there is no `research/gates/` or `gates/` tree here to scan. The guard therefore currently
finds two name-matched files, both baselined fabricated examples, and zero `run_completeness` blocks. It
prints that count on every run and says so in as many words, because a guard that prints a crisp `OK` while
having examined nothing is the third of the three self-inflicted bugs this repo shipped in one day. Its
teeth are prospective (any result artefact committed from now on) and immediate for anyone running it by
hand over a results directory: `check_run_completeness.py <path> [<path> ...]`.

Usage:
  check_run_completeness.py [--root DIR] [--baseline FILE] [PATH ...]
Exit 1 and print `path: message` for every violation, exit 0 clean -- the same shape as
`scripts/check_fail_open_defaults.py`, `scripts/import_guard.py` and `scripts/check_no_private_data.py`.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The schema string a result artefact's `run_completeness` block must carry. An UNKNOWN schema is a
#: violation, not something to interpret optimistically: a future v2 that moved a field would otherwise read
#: as a v1 with that field missing, and "missing" must never be silently negotiated (design point 2).
SCHEMA = "run-completeness/v1"

#: The shape, for the docstring's reference and for the error messages to quote back. Every key here is
#: REQUIRED unless this comment says otherwise.
#:
#:   {
#:     "run_completeness": {
#:       "schema": "run-completeness/v1",
#:       "planned_from": {"path": "<selection or manifest artefact>", "sha256": "<64 hex>",
#:                        "count_field": "<dotted path, ONLY for an object-shaped .json manifest>"},
#:       "n_planned": 1519,
#:       "counted_file": {"path": "<the .jsonl whose rows were counted>", "sha256": "<64 hex>"},
#:       "score_field": "frozen_p_established",
#:       "n_scored": 1519,
#:       "n_error_terminal": 0,
#:       "n_error_retried": 2,
#:       "truncated": false,
#:       "walk_order": "<REQUIRED when truncated is true; forbidden-to-be-empty then>"
#:     }
#:   }
#:
#: `planned_from.count_field` and `walk_order` are the only conditionally-required keys. Paths are resolved
#: relative to the ARTEFACT'S OWN DIRECTORY first and then to `--root`, so a sealed results directory copied
#: somewhere else still verifies.
BLOCK_KEY = "run_completeness"

REQUIRED_BLOCK_FIELDS: tuple[str, ...] = (
    "schema",
    "planned_from",
    "n_planned",
    "counted_file",
    "score_field",
    "n_scored",
    "n_error_terminal",
    "n_error_retried",
    "truncated",
)

#: Names that say "this file is a run's result". A tracked file matching one of these and carrying no
#: `run_completeness` block is a violation (see the docstring's second prong). Deliberately kept WIDE:
#: narrowing a pattern until the existing tree goes green is how one of this repo's guards shipped blind to
#: the only real instance in its history. The two files this currently catches are waived, by exact bytes,
#: in the baseline -- visibly, with a reason each, rather than by a pattern nobody would think to re-check.
RESULT_NAME_PATTERNS: tuple[str, ...] = (
    "*_result.json",
    "*_results.json",
    "*_result.*.json",
    "*_results.*.json",
    "*_results.example.json",
    "*_scored*.json",
    "*_sweep_results*.json",
    "gate_*.json",
    "*_raw_outputs*.jsonl",
    "*.partial.json",
    "*.partial.jsonl",
)

#: Directory NAMES excluded from discovery anywhere in the tree. Only the guard's own deliberately-wrong
#: fixtures: they are named like result artefacts on purpose, so that the negative cases are exercised
#: against the real discovery walk rather than a stub, and they must never be able to fail the real build.
#: Same idiom, and the same reason, as `check_fail_open_defaults.py`'s handling of `tests/fail_open_fixtures`.
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({"run_completeness_fixtures"})

#: `_`-separated tokens that make a numeric leaf a HEADLINE number rather than a bookkeeping count. Used for
#: one rule only: a result marked truncated must not publish one. Counts (`n`, `count`, `total`, `rows`) are
#: deliberately absent -- a truncated record SHOULD state its counts, that is the whole point of it.
HEADLINE_TOKENS: frozenset[str] = frozenset({
    "rate", "mean", "avg", "average", "accuracy", "precision", "recall", "f1", "ece", "calibration",
    "median", "sd", "std", "stdev", "sigma", "var", "variance", "ci", "ci95", "interval", "bound",
    "upper", "lower", "delta", "kappa", "auc", "fraction", "proportion", "ratio", "headline",
    "hedges", "wilson", "pearson", "estimate", "effect",
})

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

DEFAULT_BASELINE = Path("scripts") / "run_completeness_baseline.txt"

#: A baseline line must carry this, or it waives nothing. Same rule, for the same reason, as
#: `scripts/secret_scan_baseline.txt`'s "A KEY WITH NO REASON WAIVES NOTHING": a bare key is a note to
#: nobody, and the next reader cannot tell a decision from an oversight.
ISSUE_MARKER = "issue:"


class ArtefactError(Exception):
    """A result artefact could not be read at all. Always becomes a violation, never a skip."""


@dataclass(frozen=True)
class Violation:
    path: str
    code: str
    message: str

    def render(self) -> str:
        return f"{self.path}: [{self.code}] {self.message}"


# -- score usability ---------------------------------------------------------------------------------------


def usable_score(value: Any) -> bool:
    """True only for a value that IS a score. Everything else is a row that could not be scored.

    Rejected, each one because it has been seen standing in for a measurement somewhere: `None`; absent
    (the caller passes the sentinel it gets from `dig`); `""` and any whitespace-only string; a bool (in
    Python `isinstance(True, int)` is True, so a `True` score would otherwise read as the number 1); NaN and
    the infinities; a string that does not parse as a float; a list, dict or anything else.
    """
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, str):
        if not value.strip():
            return False
        try:
            return math.isfinite(float(value))
        except ValueError:
            return False
    return False


_MISSING = object()


def dig(obj: Any, dotted: str) -> Any:
    """`dig(row, "free_text.p")`. Returns the `_MISSING` sentinel when any step is absent, which
    `usable_score` rejects -- an absent score is never a zero."""
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def _retried(row: dict[str, Any]) -> bool:
    """Did this row record a retry? `retries`/`n_retries` > 0, or `attempts`/`n_attempts` > 1."""
    for key in ("retries", "n_retries"):
        v = row.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            return True
    for key in ("attempts", "n_attempts"):
        v = row.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v > 1:
            return True
    return False


@dataclass(frozen=True)
class Counts:
    rows_present: int
    n_scored: int
    n_error_terminal: int
    n_error_retried: int


def count_usable(rows: list[dict[str, Any]], score_field: str) -> Counts:
    """Usable scores, terminal errors and retried-then-succeeded rows over already-parsed rows.

    A row is terminal-error when its score is not usable, WHATEVER its retry count -- a row that was
    retried and still came back unscorable is a terminal error, not a success story. `n_error_retried`
    counts only rows that recorded a retry AND ended up with a usable score, which is the number the 32B
    Config C run wanted to state and had no field for.
    """
    n_scored = 0
    n_terminal = 0
    n_retried = 0
    for row in rows:
        if usable_score(dig(row, score_field)):
            n_scored += 1
            if _retried(row):
                n_retried += 1
        else:
            n_terminal += 1
    return Counts(len(rows), n_scored, n_terminal, n_retried)


# -- reading files -----------------------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise ArtefactError(f"could not be read: {e}") from e
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        # Reported, never skipped: a file that does not parse is a file that was NOT CHECKED, and an
        # unchecked file is not a clean file. (This repo has already shipped one guard that silently skipped
        # unparseable inputs and printed OK.)
        raise ArtefactError(f"is not parseable JSON, so NOTHING in it was checked: {e}") from e


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ArtefactError(f"could not be read: {e}") from e
    rows: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ArtefactError(f"line {i} is not parseable JSON, so the row count is not trustworthy: {e}") from e
        if not isinstance(row, dict):
            raise ArtefactError(f"line {i} is a {type(row).__name__}, not an object")
        rows.append(row)
    return rows


def _resolve(ref: str, artefact: Path, root: Path) -> Path | None:
    """A path named by an artefact: absolute as given, else relative to the artefact's own directory, else
    to `root`. Returns None when it exists nowhere -- which the caller turns into a violation."""
    candidate = Path(ref)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    for base in (artefact.parent, root):
        p = base / candidate
        if p.is_file():
            return p
    return None


def planned_count(manifest: Path, count_field: str | None) -> int:
    """The planned row count DERIVED from the selection/manifest artefact itself.

    `.jsonl` -> non-blank lines. `.json` holding a list -> its length. `.json` holding an object -> the int
    at `count_field`, which must then be given: there is no guessing at which key of an arbitrary object is
    the denominator, and guessing wrong is worse than refusing.
    """
    if manifest.suffix == ".jsonl":
        if count_field:
            raise ArtefactError(f"planned_from.count_field is set but {manifest.name} is .jsonl (rows are counted)")
        return len(load_jsonl(manifest))
    data = load_json(manifest)
    if count_field:
        value = dig(data, count_field)
        if value is _MISSING:
            raise ArtefactError(f"planned_from.count_field {count_field!r} is not present in {manifest.name}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ArtefactError(f"planned_from.count_field {count_field!r} is {value!r}, not an int")
        return value
    if isinstance(data, list):
        return len(data)
    raise ArtefactError(
        f"{manifest.name} is a JSON object, so the planned count cannot be derived from its shape -- name "
        f"the key holding it in planned_from.count_field. Refusing rather than guessing a denominator."
    )


# -- the headline-number rule ------------------------------------------------------------------------------


def _headline_leaves(obj: Any, prefix: str = "") -> list[str]:
    """Dotted paths of every numeric leaf whose key is headline-named. Used only on a truncated artefact."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            where = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                tokens = set(str(k).lower().split("_"))
                if tokens & HEADLINE_TOKENS:
                    found.append(where)
            else:
                found.extend(_headline_leaves(v, where))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_headline_leaves(v, f"{prefix}[{i}]"))
    return found


# -- the check ---------------------------------------------------------------------------------------------


def _int_field(block: dict[str, Any], name: str, rel: str, out: list[Violation]) -> int | None:
    value = block.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        out.append(Violation(rel, "field-type", f"{BLOCK_KEY}.{name} is {value!r}, not an int"))
        return None
    if value < 0:
        out.append(Violation(rel, "field-range", f"{BLOCK_KEY}.{name} is {value}, which is negative"))
        return None
    return value


def _ref_field(block: dict[str, Any], name: str, rel: str, out: list[Violation]) -> dict[str, Any] | None:
    ref = block.get(name)
    if not isinstance(ref, dict):
        out.append(Violation(rel, "field-type", f"{BLOCK_KEY}.{name} is {ref!r}, not an object with path+sha256"))
        return None
    ok = True
    if not isinstance(ref.get("path"), str) or not str(ref.get("path")).strip():
        out.append(Violation(rel, "field-missing", f"{BLOCK_KEY}.{name}.path is missing or not a non-empty string"))
        ok = False
    sha = ref.get("sha256")
    if not isinstance(sha, str) or not _HEX64.match(sha):
        out.append(
            Violation(rel, "field-missing", f"{BLOCK_KEY}.{name}.sha256 is {sha!r}, not 64 lowercase hex characters")
        )
        ok = False
    return ref if ok else None


def _verify_named_file(
    ref: dict[str, Any], label: str, artefact: Path, root: Path, rel: str, out: list[Violation]
) -> Path | None:
    resolved = _resolve(str(ref["path"]), artefact, root)
    if resolved is None:
        out.append(
            Violation(
                rel,
                "file-missing",
                f"{BLOCK_KEY}.{label}.path {ref['path']!r} exists neither beside the artefact nor under the root. "
                f"A count cannot be checked against a file that is not here, and an unchecked count is not a "
                f"checked one -- refusing rather than trusting the stated number.",
            )
        )
        return None
    actual = _sha256_file(resolved)
    if actual != ref["sha256"]:
        out.append(
            Violation(
                rel,
                "sha-mismatch",
                f"{BLOCK_KEY}.{label}: on-disk sha256 {actual} != pinned {ref['sha256']} ({resolved})",
            )
        )
        return None
    return resolved


def check_artefact(path: Path, *, root: Path) -> list[Violation]:
    """Every violation this one artefact has -- all of them, not the first: a reader fixing a result wants
    the whole list, and stopping early is how a second defect hides behind the first."""
    rel = _rel(path, root)
    out: list[Violation] = []
    if path.suffix == ".jsonl":
        # A raw-outputs `.jsonl` cannot carry a top-level block: it is a stream of rows, not a result
        # artefact. It is still name-matched on purpose -- a sealed raw output committed with no result
        # artefact beside it is a number with no denominator -- so it gets its own message rather than the
        # "not parseable JSON" one a bare load_json would produce.
        return [
            Violation(
                rel,
                "no-sidecar",
                f"is a rows-only .jsonl, so it cannot carry a {BLOCK_KEY} block itself, and no result "
                f"artefact naming it was found. Commit the result artefact that states n_planned/n_scored "
                f"and pins this file's sha256, or waive this path by exact bytes in {DEFAULT_BASELINE} with "
                f"a reason and an {ISSUE_MARKER} reference.",
            )
        ]
    try:
        data = load_json(path)
    except ArtefactError as e:
        return [Violation(rel, "unreadable", str(e))]
    if not isinstance(data, dict):
        return [Violation(rel, "not-an-object", f"top level is a {type(data).__name__}, not a JSON object")]

    block = data.get(BLOCK_KEY)
    if block is None:
        return [
            Violation(
                rel,
                "no-block",
                f"its name says it is a run's result but it carries no top-level {BLOCK_KEY!r} block, so "
                f"nothing about its denominator can be checked. Add the block (see "
                f"scripts/check_run_completeness.py's docstring), or -- if it is not a run's result at all "
                f"-- waive it by exact bytes in {DEFAULT_BASELINE}, with a reason and an {ISSUE_MARKER} ref.",
            )
        ]
    if not isinstance(block, dict):
        return [Violation(rel, "block-type", f"{BLOCK_KEY} is a {type(block).__name__}, not an object")]

    missing = [f for f in REQUIRED_BLOCK_FIELDS if f not in block]
    if missing:
        out.append(
            Violation(
                rel,
                "field-missing",
                f"{BLOCK_KEY} is missing {', '.join(missing)}. A missing field is a REJECTION here, never a "
                f"default: this guard exists because an absent denominator used to read as a fine one.",
            )
        )

    if block.get("schema") != SCHEMA:
        out.append(
            Violation(rel, "schema", f"{BLOCK_KEY}.schema is {block.get('schema')!r}, not {SCHEMA!r} (unknown schema)")
        )

    truncated = block.get("truncated")
    if not isinstance(truncated, bool):
        out.append(
            Violation(
                rel,
                "field-type",
                f"{BLOCK_KEY}.truncated is {truncated!r}, not a bool. It is stated explicitly on every "
                f"result, complete or not, so that 'complete' is a claim someone made rather than what an "
                f"absent key happened to mean.",
            )
        )

    n_planned_stated = _int_field(block, "n_planned", rel, out) if "n_planned" in block else None
    n_scored_stated = _int_field(block, "n_scored", rel, out) if "n_scored" in block else None
    n_term_stated = _int_field(block, "n_error_terminal", rel, out) if "n_error_terminal" in block else None
    n_retr_stated = _int_field(block, "n_error_retried", rel, out) if "n_error_retried" in block else None

    score_field = block.get("score_field")
    if not isinstance(score_field, str) or not score_field.strip():
        out.append(
            Violation(rel, "field-missing", f"{BLOCK_KEY}.score_field is {score_field!r}, not a non-empty string")
        )
        score_field = None

    # -- n_planned, derived from the manifest and only then compared to the claim -------------------------
    n_planned: int | None = None
    manifest_resolved: Path | None = None
    planned_ref = _ref_field(block, "planned_from", rel, out) if "planned_from" in block else None
    if planned_ref is not None:
        manifest = _verify_named_file(planned_ref, "planned_from", path, root, rel, out)
        manifest_resolved = manifest
        if manifest is not None:
            cf = planned_ref.get("count_field")
            if cf is not None and (not isinstance(cf, str) or not cf.strip()):
                out.append(
                    Violation(rel, "field-type", f"{BLOCK_KEY}.planned_from.count_field is {cf!r}, not a string or null")
                )
            else:
                try:
                    n_planned = planned_count(manifest, cf if isinstance(cf, str) else None)
                except ArtefactError as e:
                    out.append(Violation(rel, "planned-underivable", f"planned_from {manifest.name} {e}"))
    if n_planned is not None and n_planned_stated is not None and n_planned != n_planned_stated:
        out.append(
            Violation(
                rel,
                "planned-disagrees",
                f"{BLOCK_KEY}.n_planned says {n_planned_stated} but the selection artefact it names holds "
                f"{n_planned}. The manifest is the authority; a result cannot declare its own denominator "
                f"(a truncated run stating n_planned == n_scored is exactly what this rule blocks).",
            )
        )

    # -- n_scored, recounted from the counted file whose sha256 the result pins ---------------------------
    counts: Counts | None = None
    counted_ref = _ref_field(block, "counted_file", rel, out) if "counted_file" in block else None
    if counted_ref is not None and score_field is not None:
        counted = _verify_named_file(counted_ref, "counted_file", path, root, rel, out)
        if counted is not None and manifest_resolved is not None and counted.resolve() == manifest_resolved.resolve():
            # Found by the adversarial pass over this guard's own first draft, which every rule above would
            # have let through: aim `planned_from` at the OUTPUT file and the denominator becomes the
            # numerator, so a 55-row prefix "plans" 55 rows and reads as complete. The planned count has to
            # come from a DIFFERENT artefact -- the committed selection -- or design point 1 is decorative.
            out.append(
                Violation(
                    rel,
                    "planned-is-the-output",
                    f"{BLOCK_KEY}.planned_from and {BLOCK_KEY}.counted_file are the SAME file ({counted}). "
                    f"The planned count must come from the committed selection/manifest artefact, not from "
                    f"the run's own output: pointing both at the output makes n_planned self-declared again, "
                    f"which is the one thing this guard exists to prevent.",
                )
            )
        if counted is not None:
            try:
                rows = load_jsonl(counted) if counted.suffix == ".jsonl" else _rows_from_json(counted)
            except ArtefactError as e:
                out.append(Violation(rel, "counted-unreadable", f"counted_file {counted.name} {e}"))
            else:
                counts = count_usable(rows, score_field)

    if counts is not None:
        if n_scored_stated is not None and counts.n_scored != n_scored_stated:
            out.append(
                Violation(
                    rel,
                    "scored-disagrees",
                    f"{BLOCK_KEY}.n_scored says {n_scored_stated} but {counts.n_scored} of the "
                    f"{counts.rows_present} rows in the counted file carry a usable score "
                    f"(absent, null, empty, bool, non-finite and unparseable are not scores)",
                )
            )
        if n_term_stated is not None and counts.n_error_terminal != n_term_stated:
            out.append(
                Violation(
                    rel,
                    "terminal-disagrees",
                    f"{BLOCK_KEY}.n_error_terminal says {n_term_stated} but {counts.n_error_terminal} of the "
                    f"{counts.rows_present} rows present have no usable score",
                )
            )
        if n_retr_stated is not None and counts.n_error_retried != n_retr_stated:
            out.append(
                Violation(
                    rel,
                    "retried-disagrees",
                    f"{BLOCK_KEY}.n_error_retried says {n_retr_stated} but {counts.n_error_retried} row(s) "
                    f"record a retry and then a usable score. Retried-then-succeeded is reported separately "
                    f"from terminal precisely so a run with two retried timeouts stops reporting zero errors.",
                )
            )

    # -- the rule itself ----------------------------------------------------------------------------------
    n_scored = counts.n_scored if counts is not None else n_scored_stated
    if n_planned is not None and n_scored is not None:
        if n_scored < n_planned and truncated is not True:
            out.append(
                Violation(
                    rel,
                    "truncated-unmarked",
                    f"n_scored {n_scored} < n_planned {n_planned} and the result does not say it is "
                    f"truncated. A short run is reported as truncated and labelled with its walk order, or "
                    f"it publishes no number at all.",
                )
            )
        if n_scored > n_planned:
            out.append(
                Violation(
                    rel,
                    "scored-exceeds-planned",
                    f"n_scored {n_scored} > n_planned {n_planned}: more rows were scored than the selection "
                    f"artefact plans for, so the two are not describing the same run",
                )
            )
        if truncated is True and n_scored == n_planned:
            out.append(
                Violation(
                    rel,
                    "complete-marked-truncated",
                    f"marked truncated, but n_scored {n_scored} == n_planned {n_planned}. Either the run is "
                    f"complete and the label is wrong, or the label is right and the denominator is not the "
                    f"run's own -- both are reporting errors, and 'mark everything truncated' must not "
                    f"become the way past this guard.",
                )
            )

    if truncated is True:
        walk_order = block.get("walk_order")
        if not isinstance(walk_order, str) or not walk_order.strip():
            out.append(
                Violation(
                    rel,
                    "walk-order-missing",
                    f"marked truncated but {BLOCK_KEY}.walk_order is {walk_order!r}. Walk order only biases "
                    f"a result when the run is truncated, which is exactly this case, so a truncated result "
                    f"is accepted ONLY when it states the order its rows were walked in.",
                )
            )
        headlines = _headline_leaves({k: v for k, v in data.items() if k != BLOCK_KEY})
        if headlines:
            out.append(
                Violation(
                    rel,
                    "headline-on-truncated",
                    f"marked truncated but still publishes headline number(s) at {', '.join(sorted(headlines))}. "
                    f"A truncated run states its counts and its walk order; it does not emit a rate, a mean "
                    f"or an interval on a short denominator.",
                )
            )
    return out


def _rows_from_json(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    if not isinstance(data, list):
        raise ArtefactError("is a JSON object, not a list of rows; counted_file must be .jsonl or a JSON array")
    rows: list[dict[str, Any]] = []
    for i, row in enumerate(data, 1):
        if not isinstance(row, dict):
            raise ArtefactError(f"element {i} is a {type(row).__name__}, not an object")
        rows.append(row)
    return rows


# -- discovery ---------------------------------------------------------------------------------------------


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _tracked_files(root: Path) -> list[str]:
    """Git's own tracked-file list, like `check_no_private_data.py` and `check_fail_open_defaults.py`: a
    gitignored local scratch result is invisible here by construction, exactly like every other
    tracked-file check in this repo.

    A FAILING `git ls-files` RAISES. The first draft of this function returned `[]` on a non-zero exit,
    which meant "git is not here" and "there is nothing to check" printed the same green OK -- the precise
    silent-skip shape this guard is against. Found by asking the question of the guard itself rather than
    of the code it reads.
    """
    proc = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise ArtefactError(
            f"`git ls-files` failed in {root} (exit {proc.returncode}): {proc.stderr.strip()}. Nothing was "
            f"scanned, which is NOT the same as nothing being wrong -- refusing rather than reporting a "
            f"vacuous pass."
        )
    return [p for p in proc.stdout.split("\0") if p]


def _excluded(rel: str) -> bool:
    return any(part in EXCLUDED_DIR_NAMES for part in Path(rel).parts)


def discover(root: Path) -> tuple[list[Path], list[Path]]:
    """(artefacts carrying a `run_completeness` block, files whose NAME says result).

    Both prongs, because either alone has a hole: block-only lets a result opt out of the guard by omitting
    the block, and name-only misses a result artefact under an unguessed name. A file in the second list
    with no block in the first is the `no-block` violation.
    """
    with_block: list[Path] = []
    name_matched: list[Path] = []
    for rel in _tracked_files(root):
        if _excluded(rel):
            continue
        p = root / rel
        name = Path(rel).name
        if any(fnmatch.fnmatch(name, pat) for pat in RESULT_NAME_PATTERNS):
            name_matched.append(p)
        if rel.endswith(".json") and p.is_file():
            try:
                # A cheap substring pre-filter before parsing every tracked JSON file; the parse in
                # check_artefact is what decides. A file that LOOKS like it has a block and does not parse
                # still reaches check_artefact, which reports it as unreadable rather than dropping it.
                has_block = BLOCK_KEY in p.read_text(errors="ignore")
            except OSError:
                # Unreadable at the pre-filter stage: pass it through anyway so check_artefact reports it.
                # `continue` here would drop a file that might be a result artefact, silently.
                with_block.append(p)
                continue
            if not has_block:
                continue
            with_block.append(p)
    return with_block, name_matched


def _files_named_by_artefacts(artefacts: list[Path], root: Path) -> set[Path]:
    """Every `counted_file`/`planned_from` path that a discovered result artefact names, resolved. Such a
    file is already covered by that artefact's own check, so it is not separately an orphan."""
    named: set[Path] = set()
    for art in artefacts:
        try:
            data = load_json(art)
        except ArtefactError:
            continue
        if not isinstance(data, dict):
            continue
        block = data.get(BLOCK_KEY)
        if not isinstance(block, dict):
            continue
        for key in ("counted_file", "planned_from"):
            ref = block.get(key)
            if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                resolved = _resolve(ref["path"], art, root)
                if resolved is not None:
                    named.add(resolved.resolve())
    return named


# -- baseline ----------------------------------------------------------------------------------------------


def baseline_key(path: Path, root: Path) -> str:
    """`<repo-relative path>::<sha256 of the file>`. Keyed on the exact BYTES, not the path alone, so a
    waiver cannot follow a file into being something else: edit the file and the waiver goes stale and CI
    goes red until someone looks again. The guard prints this key for every waivable finding, so writing a
    baseline line is copy-and-paste from the failing log."""
    return f"{_rel(path, root)}::{_sha256_file(path)}"


def load_baseline(path: Path) -> tuple[set[str], list[str]]:
    """(keys that actually waive something, complaints about this file itself).

    A key with no reason, or a reason with no `issue:` reference, WAIVES NOTHING and is reported -- the same
    rule as `scripts/secret_scan_baseline.txt`. A waiver whose reason nobody wrote down is
    indistinguishable from an oversight, and this whole guard is about not letting those two look alike.
    """
    if not path.is_file():
        return set(), []
    keys: set[str] = set()
    complaints: list[str] = []
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = re.split(r"\s{2,}", stripped, maxsplit=1)
        key = parts[0].strip()
        reason = parts[1].strip() if len(parts) > 1 else ""
        if not reason:
            complaints.append(f"{path.name}:{lineno}: key with no reason waives nothing: {key}")
            continue
        if ISSUE_MARKER not in reason.lower():
            complaints.append(f"{path.name}:{lineno}: reason carries no {ISSUE_MARKER} reference, waives nothing: {key}")
            continue
        keys.add(key)
    return keys, complaints


# -- report ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Report:
    violations: list[str]
    hints: list[str]
    baseline_complaints: list[str]
    stale_baseline: list[str]
    n_with_block: int
    n_name_matched: int
    n_waived: int

    @property
    def ok(self) -> bool:
        return not self.violations and not self.baseline_complaints


def check(root: Path, explicit: list[Path] | None = None, baseline_path: Path | None = None) -> Report:
    baseline_file = baseline_path if baseline_path is not None else root / DEFAULT_BASELINE
    baseline, complaints = load_baseline(baseline_file)

    if explicit:
        # Explicit paths are checked as given and are NOT waivable: someone naming a file on the command
        # line is asking about that file, and answering with a baseline hit would answer a different
        # question.
        violations = [v.render() for p in explicit for v in check_artefact(p, root=root)]
        return Report(violations, [], complaints, [], len(explicit), 0, 0)

    try:
        with_block, name_matched = discover(root)
    except ArtefactError as e:
        return Report([f"{root}: [scan-failed] {e}"], [], complaints, [], 0, 0, 0)
    block_set = {p.resolve() for p in with_block}
    # A rows-only .jsonl that a tracked result artefact already names and pins is accounted for; only an
    # orphan one is a finding.
    named_by_an_artefact = _files_named_by_artefacts(with_block, root)
    targets = [
        p
        for p in name_matched
        if p.resolve() not in block_set and p.resolve() not in named_by_an_artefact
    ]
    targets = list(with_block) + targets

    violations: list[str] = []
    hints: list[str] = []
    used: set[str] = set()
    n_waived = 0
    for p in sorted(set(targets), key=lambda q: _rel(q, root)):
        found = check_artefact(p, root=root)
        if not found:
            continue
        key = baseline_key(p, root)
        if key in baseline:
            used.add(key)
            n_waived += 1
            continue
        violations.extend(v.render() for v in found)
        hints.append(f"  baseline key (needs a reason and an {ISSUE_MARKER} reference): {key}")
    return Report(
        violations=violations,
        hints=hints,
        baseline_complaints=complaints,
        stale_baseline=sorted(baseline - used),
        n_with_block=len(with_block),
        n_name_matched=len(name_matched),
        n_waived=n_waived,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", type=Path, help="result artefacts to check directly (not waivable)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--baseline", default=None, help=f"default: <root>/{DEFAULT_BASELINE}")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    baseline = Path(args.baseline).resolve() if args.baseline else None
    report = check(root, [Path(p) for p in args.paths] or None, baseline)

    if not args.paths:
        print(
            f"scanned: {report.n_with_block} artefact(s) carrying a {BLOCK_KEY} block, "
            f"{report.n_name_matched} file(s) whose name says result, {report.n_waived} baselined."
        )
        if report.n_with_block == 0:
            # Said out loud on every run. A guard that prints OK having examined nothing is this repo's own
            # recent bug, and the honest reading of a green tick here is "nothing to check", not "checked".
            print(
                f"NOTE: zero {BLOCK_KEY} blocks in the tracked tree, so this run checked no real result "
                f"artefact. That is expected here -- results never write inside this repo "
                f"(PRAVRUDHI_T2_RESULTS_DIR is mandatory and has no in-repo default) -- and it means this "
                f"step's green tick says 'nothing to check', NOT 'the numbers were verified'. Run it over a "
                f"results directory by hand to check one: "
                f"`uv run python scripts/check_run_completeness.py <result.json>`."
            )

    for c in report.baseline_complaints:
        print(c)
    for s in report.stale_baseline:
        print(f"baseline: no longer matches anything, remove this line: {s}")
    for v in report.violations:
        print(v)
    for h in report.hints:
        print(h)

    if not report.ok:
        print(
            f"\nFAIL: {len(report.violations)} run-completeness violation(s). Every scored result states "
            f"n_planned (derived from the selection artefact it names, never from its own claim), n_scored "
            f"(usable scores, not rows present), n_error_terminal and n_error_retried, plus the sha256 of "
            f"the file it counted; a run with n_scored < n_planned is reported as truncated and labelled "
            f"with its walk order."
        )
        return 1
    print("OK: every result artefact found states a verified denominator, or is baselined with a reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
