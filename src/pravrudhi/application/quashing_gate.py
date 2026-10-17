# ruff: noqa: E501
"""S3 (quashing categories agenda): the BUILD-TIME checks and the corroboration table. Never imported by the route.

The product asset (`assets/quashing/bhajan_lal_para102.json`) ships the Court's words of paras 102 and 103 of State of Haryana v.
Bhajan Lal as quoted in Amanullah v. State of Bihar. Production holds no judgment text, so the words are verified HERE, offline,
against a sha-pinned archive of the full-text index (`scripts/check_s3_quotes.py` drives these functions). Everything below is pure:
text in, verdicts out.

Three different comparisons are used and never mixed:
  * EXACT: `quote_raw` must be a byte-exact substring of the RAW text of the pinned judgment (the box gate);
  * the page's `quote_shown` is `quote_raw` with whitespace runs collapsed (the only display fold; no word is changed);
  * CORROBORATION across other indexed decisions compares after cleaner A and fold `s3_fold_v1` (build-time, reported, never gated).
"""

from __future__ import annotations

import hashlib
import inspect
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pravrudhi.application.verify import normalize_text_for_match

STEM_START = "In the backdrop of the interpretation"
STEM_END = "such power should be exercised."
CAUTION_START = "103. We also give a note of caution"
CAUTION_END = "the allegations made in the FIR or the complaint"
ITEM_MARK = re.compile(r"\((\d)\) Where")
ELLIPSIS = " ..."
MIN_COMPUTABLE = 20  # fewer decisions than this: INSUFFICIENT COPIES
D_CONFLICT_N = 3  # a word variant in this many decisions or more is a CONFLICT
DOMINANCE = 5  # the pinned form must be at least this many times the largest variant

# ---- cleaner A: artefact insertions (page headers, note markers) ---------------------------------------------------------------
# The Indian Kanoon page header ("Title vs Title on 12 April, 2016" + "Indian Kanoon - http://indiankanoon.org/doc/N/ 11"), with or
# without the line break, and bracketed note markers like "[9]". The title run is capped (at most 8 + 10 words) so the cleaner
# cannot eat the Court's sentence around a flat (single-line) header.
_WORD = r"[A-Z&][\w.&'’-]*,?"
CLEANER_A = (
    rf"(?:{_WORD}[ \t]+){{1,8}}vs\.?[ \t]+(?:{_WORD}[ \t]+){{1,10}}on[ \t]+\d{{1,2}}[ \t]+\w+,?[ \t]+\d{{4}}\s*Indian[ \t]+Kanoon\s*-?\s*"
    r"https?://indiankanoon\.org/doc/\d+/?\s*\d*",
    r"Indian[ \t]+Kanoon\s*-?\s*https?://indiankanoon\.org/doc/\d+/?\s*\d*",
    r"\[\d{1,3}\]",
)
_CLEANER_RX = tuple(re.compile(p) for p in CLEANER_A)

# closed list of spelling pairs (class C): variant spelling -> the pinned spelling, compared in folded form
SPELLING = {"channelized": "channelised"}


def source_hash(obj: Any) -> str:
    return hashlib.sha256(inspect.getsource(obj).encode("utf-8")).hexdigest()


def cleaner_hash() -> str:
    return hashlib.sha256("\n".join(CLEANER_A).encode("utf-8")).hexdigest()


def clean_a(text: str) -> str:
    """Remove artefact insertions (cleaner A). Applied before the fold; never applied to quote_raw itself."""
    for rx in _CLEANER_RX:
        text = rx.sub(" ", text)
    return text


def fold_v1(text: str) -> str:
    """`s3_fold_v1`: the engine's normaliser, then casefold, then drop every non-alphanumeric character and all whitespace."""
    return "".join(ch for ch in normalize_text_for_match(text).casefold() if ch.isalnum())


def fold_hash() -> str:
    return hashlib.sha256((inspect.getsource(fold_v1) + repr(sorted(SPELLING.items()))).encode("utf-8")).hexdigest()


def normaliser_hash() -> str:
    return source_hash(normalize_text_for_match)


def collapse(text: str) -> str:
    """The page's display fold: whitespace runs to one space. No character other than whitespace changes."""
    return " ".join(text.split())


# ---- extracting the units from the RAW text of the pinned judgment -----------------------------------------------------------------


@dataclass(frozen=True)
class Units:
    stem: str
    items: tuple[str, ...]
    caution: str


class ExtractError(ValueError):
    """The pinned text does not have the expected shape (the gate fails)."""


def extract_units(raw: str) -> Units:
    """Stem, items (1)-(7) and the caution as EXACT raw substrings. Items are keyed on the numbers (1)-(7) in order, never on
    'Where' alone (item (4) reads '(4) Where, the allegations ...')."""
    i = raw.find(STEM_START)
    if i < 0 or raw.count(STEM_START) != 1:
        raise ExtractError("the stem start is not found exactly once")
    j = raw.find(STEM_END, i)
    if j < 0:
        raise ExtractError("the stem end is not found")
    stem = raw[i : j + len(STEM_END)]
    marks = [(m.start(), int(m.group(1))) for m in ITEM_MARK.finditer(raw, j)]
    first_run: list[tuple[int, int]] = []
    for pos, num in marks:
        if num == len(first_run) + 1:
            first_run.append((pos, num))
        if len(first_run) == 7:
            break
    if [n for _, n in first_run] != [1, 2, 3, 4, 5, 6, 7]:
        raise ExtractError("the items (1)-(7) are not found in order after the stem")
    c0 = raw.find(CAUTION_START, first_run[-1][0])
    if c0 < 0:
        raise ExtractError("the caution start is not found after item (7)")
    items: list[str] = []
    for k, (pos, _) in enumerate(first_run):
        end = first_run[k + 1][0] if k < 6 else c0
        items.append(raw[pos:end].rstrip())
    c1 = raw.find(CAUTION_END, c0)
    if c1 < 0:
        raise ExtractError("the caution end is not found")
    caution = raw[c0 : c1 + len(CAUTION_END)]
    return Units(stem, tuple(items), caution)


def quote_block_sha256(units: Units) -> str:
    """Hash of the concatenated quote_raw (stem, items (1)-(7), caution), each followed by a newline."""
    body = "\n".join([units.stem, *units.items, units.caution]) + "\n"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ---- the exact checks (box gate) ---------------------------------------------------------------------------------------------------


def exact_failures(units: Units, raw: str, lokanath_raw: str | None = None) -> list[str]:
    """Byte-exact containment of every quote_raw in the RAW pinned text (not after any fold), once, in order; the caution is a
    prefix, in raw bytes, of the clean full sentence in the second exact copy when one is given."""
    fails: list[str] = []
    pieces = [("stem", units.stem), *[(f"item{k}", t) for k, t in enumerate(units.items, 1)], ("caution", units.caution)]
    last = -1
    for name, text in pieces:
        if raw.count(text) != 1:
            fails.append(f"{name}: not found exactly once as a byte-exact substring of the raw text")
            continue
        at = raw.find(text)
        if at < last:
            fails.append(f"{name}: out of order")
        last = at
    if lokanath_raw is not None:
        sentence = lokanath_raw.find(CAUTION_START)
        if sentence < 0 or not lokanath_raw[sentence:].startswith(units.caution):
            fails.append("caution: not an exact raw prefix of the second copy's sentence")
    return fails


def block_between_has_artefact(raw: str, units: Units) -> bool:
    """A printed page header inside the stem-to-item-(7) block (the block must be one contiguous clean run)."""
    start = raw.find(units.stem)
    end = raw.find(units.items[-1], start) + len(units.items[-1])
    block = raw[start:end]
    return clean_a(block) != block


# ---- corroboration (build-time, reported, never gated) ---------------------------------------------------------------------------


def _title_key(title: str, year: int | None) -> tuple[str, int | None]:
    return re.sub(r"[^a-z0-9]+", "", title.casefold()), year


def _spell(folded: str) -> str:
    for variant, pinned in SPELLING.items():
        folded = folded.replace(fold_v1(variant), fold_v1(pinned))
    return folded


@dataclass
class ItemResult:
    name: str
    n_computable: int = 0
    n_equal: int = 0
    variants: Counter[str] = field(default_factory=Counter)  # candidate hash -> n
    classes: dict[str, str] = field(default_factory=dict)  # candidate hash -> "C" | "D"

    def largest_variant(self) -> int:
        return max(self.variants.values(), default=0)

    def d_conflict(self) -> bool:
        return any(n >= D_CONFLICT_N and self.classes[h] == "D" for h, n in self.variants.items())

    def status(self) -> str:
        if self.n_computable < MIN_COMPUTABLE:
            return "INSUFFICIENT COPIES"
        if self.d_conflict() or self.n_equal < DOMINANCE * self.largest_variant():
            return "CONFLICT"
        return "CORROBORATED"


def _anchors(unit: str) -> tuple[str, str]:
    words = unit.split()
    return fold_v1(" ".join(words[:8])), fold_v1(" ".join(words[-8:]))


def corroborate(units: Units, decisions: list[dict[str, Any]]) -> list[ItemResult]:
    """The corroboration table. `decisions` are archive rows other than the pin (title, year, window). Rows are de-duplicated by
    (title, year). A decision is computable for a unit only when both anchors (first and last 8 words, after cleaner A and the fold)
    are in its window in order; a window that cuts the unit is NOT computable."""
    seen: set[tuple[str, int | None]] = set()
    folded: list[str] = []
    for row in decisions:
        key = _title_key(row.get("title") or "", row.get("year"))
        if key in seen:
            continue
        seen.add(key)
        folded.append(fold_v1(clean_a(row["window"])))
    units_by_name = [("stem", units.stem), *[(f"item{k}", t) for k, t in enumerate(units.items, 1)], ("caution", units.caution)]
    out: list[ItemResult] = []
    for name, text in units_by_name:
        res = ItemResult(name)
        pinned = fold_v1(clean_a(text))
        first, last = _anchors(clean_a(text))
        for w in folded:
            i = w.find(first)
            if i < 0:
                continue
            j = w.find(last, i + len(first) if first != last else i)
            if j < 0:
                continue
            cand = w[i : j + len(last)]
            if len(cand) > len(pinned) * 1.5 + 200:
                continue
            res.n_computable += 1
            if cand == pinned:
                res.n_equal += 1
                continue
            h = hashlib.sha256(cand.encode("utf-8")).hexdigest()[:16]
            res.variants[h] += 1
            res.classes[h] = "C" if _spell(cand) == _spell(pinned) else "D"
        out.append(res)
    return out


def table(results: list[ItemResult]) -> list[dict[str, Any]]:
    """The printed/attested table: counts and variant hashes only, never judgment text."""
    return [
        {
            "unit": r.name,
            "n_computable": r.n_computable,
            "n_equal": r.n_equal,
            "variants": [{"hash": h, "class": r.classes[h], "n": n} for h, n in sorted(r.variants.items(), key=lambda t: -t[1])],
            "status": r.status(),
        }
        for r in results
    ]


# ---- the headnote / reporter marker check (R1's list) --------------------------------------------------------------------------------

MARKER_REGEX = re.compile(r"\[\d{1,4}[A-H](?:-[A-H])?\]")
MARKER_WORDS = (
    "SCR", "Headnote", "Headnotes", "SCC", "AIR", "MANU", "Manupatra", "Equivalent citations", "Cited by", "Cites",
    "Indian Kanoon", "indiankanoon", "http", "www",
)  # fmt: skip
_WORD_RX = re.compile(
    r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(w) for w in sorted(MARKER_WORDS, key=len, reverse=True)) + r")(?![A-Za-z0-9])",
    re.I,
)
_HELD_RX = re.compile(r"(?<![A-Za-z])Held:")


def reporter_markers(text: str) -> list[str]:
    """Law-report page-letter markers and publisher words in one string (whole word, case-insensitive for the words, exact for
    the regex and for the reporter heading 'Held:')."""
    found = [m.group(0) for m in MARKER_REGEX.finditer(text)]
    found += [m.group(0) for m in _WORD_RX.finditer(text)]
    found += [m.group(0) for m in _HELD_RX.finditer(text)]
    return found
