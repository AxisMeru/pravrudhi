"""Intake (Workflows row 2, pravrudhi#414): the pure tools and the deterministic final tool `build_bundle`.

Input is raw text the ENGINE already holds (pasted text, or the text layer of a text PDF with pages joined by form feed "\\f").
Nothing here uses a model, the network, a file or a clock. Every function is pure; the same raw text gives the same bundle.

Tools (the agent may call them as tool calls; the baseline route calls them in-process, with no model at all):
  read_input         the request's raw text: sha256, length, page starts; refuses empty or over-cap text.
  clean              F0 v1: delete admitted page furniture (behind the E1d caption guard), join hyphenated line breaks,
                     collapse whitespace; returns the cleaned text plus a segment map and the list of deleted raw spans.
  split_facts        rule-based paragraph / sentence-group candidates over the CLEANED text, as index spans only.
  extract_citations  the existing typed parser (citations.parse_citations) over the RAW text, with raw spans.
  build_bundle       FINAL. Recomputes every check from the raw text; only what verifies is marked citable.

The bundle's checks (see docs: INTAKE_DESIGN_NOTE_2026-10-14): C1 offset round-trip cleaned to raw; C2 every deletion is
admissible by recomputation; C3 protected tokens; C4 each fact a contiguous raw span (else paraphrase, not citable);
C5 coverage (reported); C6 citation spans and verifier status shown unchanged; C7 determinism (bundle sha).
Offsets are Unicode code-point offsets into the raw text exactly as received (no normalisation of the raw text).
"""

from __future__ import annotations

import bisect
import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

MAX_CHARS = 200_000
MAX_FACTS = 400
MAX_FACT_CHARS = 600
MIN_PAGES_RECURRENCE = 3
SCAN_MIN_CHARS_PER_PAGE = 100  # looks_scanned: average non-space characters per page below this
UNREAD_PAGE_CHARS = 20  # a page with fewer non-space characters is listed as "not read"
ADMITTED_KINDS = frozenset({"number_only", "page_mark", "web_address", "page_fragment"})
SCAN_LINE = "This file looks like a scan, so its text cannot be read here. Paste the text instead."


class IntakeRefused(Exception):
    """A request or a tool call refused with a plain reason (nothing is produced)."""


def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---- scan detection (engine side, before any session) -------------------------------------------------------------------
def _nonspace(s: str) -> int:
    return sum(1 for c in s if not c.isspace())


def looks_scanned(page_texts: Sequence[str]) -> bool:
    """True when a PDF's text layer is (almost) empty: no pages, or fewer than SCAN_MIN_CHARS_PER_PAGE non-space characters
    per page on average. Pasted text is never tested."""
    if not page_texts:
        return True
    return sum(_nonspace(p) for p in page_texts) / len(page_texts) < SCAN_MIN_CHARS_PER_PAGE


def unread_pages(page_texts: Sequence[str]) -> list[int]:
    """Indexes of pages with almost no text (image pages in a mixed PDF); listed to the user as not read, never guessed."""
    return [i for i, p in enumerate(page_texts) if _nonspace(p) < UNREAD_PAGE_CHARS]


# ---- tool: read_input ---------------------------------------------------------------------------------------------------
def read_input(raw: str) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw.strip():
        raise IntakeRefused("no text to read")
    if len(raw) > MAX_CHARS:
        raise IntakeRefused(f"text is longer than {MAX_CHARS} characters")
    starts = [0]
    for i, c in enumerate(raw):
        if c == "\f":
            starts.append(i + 1)
    return {"raw_sha256": sha(raw), "n_chars": len(raw), "page_starts": starts}


# ---- page furniture (ported from the E1c/E1d research cleaner; only the admitted kinds are used) ------------------------
_MAX_LINE = 120
_CURR = re.compile(r"(?:\bRs\b\.?|\bINR\b|₹|\brupees?\b)", re.I)
_NEGW = re.compile(
    r"\b(?:not|no|never|nor|without|cannot|neither|none|nobody|nothing|nowhere|unable|absent|fail\w*|refus\w*|"
    r"den(?:y|ied|ies|ial)|lack\w*|non-\w+)\b|n't",
    re.I,
)
_LABEL = re.compile(
    r"^\W*(?:accused|witness|exhibit|ex|pw|dw|p\.?w|d\.?w|annexure|section|sec|article|paragraph|para|mark|marked|item|fact|count|charge)\b"
    r"|^\W*[A-Za-z]\.?\s?-?\s?\d{1,3}\W*$",
    re.I,
)
_PAGE = re.compile(r"\bpage\s+\d+|(?:^|\s)[-–—:/]+\s*\d{1,3}\s*[-–—:/]+(?:\s|$)", re.I)
_CASE = re.compile(
    r"\b(?:crl|cri|criminal|appeals?|petition|w\.?p|c\.?r\.?p|c\.?a|o\.?s|s\.?a|r\.?c|m\.?p|revn?|writ|case|suit)\b[^\n]{0,40}?\d"
    r"|\bof\s+(?:19|20)\d\d\b|\b\d+\s*/\s*(?:19|20)\d\d\b",
    re.I,
)
_URL = re.compile(r"https?://|www\.", re.I)
_PAGEFRAG = re.compile(r"^\W*page\s*(?:no\.?)?\s*#?\W*(?:\d+(?:\s*(?:/|of)\s*\d+)?)?\W*$", re.I)
_BOUNDARY_W = 3
_CAP_VOCAB = re.compile(
    r"\b(?:petitioners?|respondents?|appellants?|accused|complainants?|advocates?|counsel|represented\s+by|aged\s+about|s/o|d/o|w/o|r/o|"
    r"resident\s+of|versus|vs\.?|v/s|between|coram|before|hon'?ble|justice|present|public\s+prosecutor|learned)\b"
    r"|\bcharges?\b|\bcharge[- ]?sheet\b|\bprayers?\b|\bpray(?:s|ed)?\b|\boffences?\b|\bsections?\b|\bu/s\b|\bipc\b|\bbnss?\b"
    r"|\bcr\.?\s?p\.?\s?c\b|\bquash(?:ed|ing)?\b|\bset\s+aside\b|\bdirect(?:ed|s|ion)?\b|\band:|"
    r"\bfor\s+the\s+(?:petitioner|respondent|appellant|state)\b|\b(?:sri|shri|smt|km)\.?\s+[A-Za-z]",
    re.I,
)
_NEUTRAL = re.compile(
    r"\b(?:19|20)\d\d\s*:\s*[A-Za-z]+(?:\s*[-:]\s*[A-Za-z]+)*\s*:?\s*\d+|\bNC\s*:|\bCRL\.?\s*[A-Z]*\.?\s*NO", re.I
)
_CT = (
    r"(?:CRL|CRIMINAL|CRI|CR|CRLA|CRLP|CRLRC|CRLMP|CRP|WP|WA|OP|MC|SB|RFA|RSA|SA|CMP|MP|APPEAL|PETITION|REVISION|MISC|CASE)"
    r"(?:\.[A-Z]+)*\.?"
)
_YR = r"(?:19|20)\d\d"
_CASE_NO = rf"\d{{1,6}}(?:\s*(?:/|OF)\s*{_YR})?"
_CASE_STAMP = (
    rf"(?:\([A-Z]{{1,4}}\)\s*\.?\s*)?{_CT}(?:[ .\-/]*{_CT})*[ .]*(?:NOS?\.?)?[ .]*{_CASE_NO}(?:\s*(?:AND|&|,)\s*{_CASE_NO})*[ .]*"
)
_PAGE_STAMP = r"(?:PAGE|PG\.?)\s*(?:NO\.?)?\s*\d+(?:\s*(?:OF|/)\s*\d+)?(?:\s*//\s*\d+\s*//)?"
_NC_STAMP = rf"NC\s*:\s*{_YR}\s*:\s*[A-Z]+\s*:\s*\d+"
_NUM_PREFIX = r"[-–—:/ ]*\d{1,3}[-–—:/ ]*"
_BRACKET_STAMP = rf"\([A-Z]{{1,4}}\)\s*\.?\s*NOS?\.?\s*{_CASE_NO}"
_TAIL = r"(?:\s+\d{1,3})?"
_STAMP = re.compile(
    rf"^(?:{_PAGE_STAMP}|(?:{_NUM_PREFIX})?(?:{_NC_STAMP}\s*)?(?:{_CASE_STAMP}|{_BRACKET_STAMP})?{_TAIL}"
    rf"|https?://\S+(?:\s+(?:{_CASE_STAMP}|{_BRACKET_STAMP}))?{_TAIL}|www\.\S+{_TAIL})$",
    re.I,
)


_PARA_START = re.compile(r"(?:\d{1,3}[.)]|\([0-9]{1,3}\)|\([ivxIVX]{1,5}\))\s+\S")


def _next_kept(e: int, ends_by_start: dict[int, int]) -> int:
    """Where the next kept text begins: skip the deletions that start right at `e`."""
    while e in ends_by_start:
        e = ends_by_start[e]
    return e


def _ws_piece(raw: str, s: int, e: int, ends_by_start: dict[int, int]) -> str:
    """What a whitespace run becomes: a paragraph break when it holds two newlines, or one newline before a numbered-paragraph
    marker ("12." / "(3)" / "(iv)"); a single space otherwise. Used by `clean` and re-applied by `check_clean`."""
    run = raw[s:e]
    nxt = _next_kept(e, ends_by_start)
    if run.count("\n") >= 2 or ("\n" in run and _PARA_START.match(raw[nxt : nxt + 16])):
        return "\n\n"
    return " "


def _ws(s: str) -> str:
    return " ".join(s.split())


def _dn(s: str) -> str:
    return re.sub(r"\d+", "#", _ws(s))


def _has_negation(t: str) -> bool:
    return bool(_NEGW.search(re.sub(r"(?i)\bno\.", "", t)))


def _header_evidence(t: str) -> bool:
    if _PAGE.search(t) or _CASE.search(t) or _URL.search(t):
        return True
    w = t.split()
    letters = [c for c in t if c.isalpha()]
    return len(w) >= 3 and bool(letters) and sum(c.isupper() for c in letters) / len(letters) >= 0.7


def _sentence_like(t: str) -> bool:
    w = t.split()
    if len(w) > 14:
        return True
    run = 0
    for x in w:
        run = run + 1 if re.fullmatch(r"[a-z]+[,.;:]?", x) else 0
        if run >= 5:
            return True
    return False


def _guards_ok(t: str) -> bool:
    return (
        bool(t)
        and len(t) <= _MAX_LINE
        and not _has_negation(t)
        and not _CURR.search(t)
        and not _sentence_like(t)
        and not _LABEL.search(t)
    )


def _is_stamp(t: str) -> bool:
    t = _ws(t)
    return bool(t) and bool(_STAMP.match(t)) and bool(re.search(r"[A-Za-z]", t))


def _kind(t: str) -> str:
    t = _ws(t)
    if re.fullmatch(r"\d{1,3}", t):
        return "number_only"
    if _PAGE.search(t):
        return "page_mark"
    if _URL.search(t):
        return "web_address"
    if _PAGEFRAG.match(t):
        return "page_fragment"
    if _CASE.search(t):
        return "case_mark"
    return "other"


def _pages_and_lines(text: str) -> list[list[tuple[int, int, str]]]:
    out, base = [], 0
    for seg in text.split("\f"):
        ls, pos = [], 0
        for ln in seg.split("\n"):
            ls.append((base + pos, base + pos + len(ln), ln))
            pos += len(ln) + 1
        out.append(ls)
        base += len(seg) + 1
    return out


def _boundary(page: list[tuple[int, int, str]]) -> dict[str, list[int]]:
    ne = [i for i, (_, _, ln) in enumerate(page) if ln.strip()]
    return {"first": ne[:_BOUNDARY_W], "last": ne[-_BOUNDARY_W:]} if ne else {"first": [], "last": []}


def _f2_spans(text: str) -> list[tuple[int, int, str]]:
    pages = _pages_and_lines(text)
    res: list[tuple[int, int, str]] = []
    for side in ("first", "last"):
        nums: list[tuple[int, int, int]] = []
        for pi, pg in enumerate(pages):
            b = _boundary(pg)[side]
            cand = [(li, int(_ws(pg[li][2]))) for li in b if re.fullmatch(r"\d{1,3}", _ws(pg[li][2]))]
            if len(cand) == 1:
                nums.append((pi, cand[0][0], cand[0][1]))
        run = [nums[0]] if nums else []

        def flush(r: list[tuple[int, int, int]]) -> None:
            if len(r) >= 3:
                for pi, li, _ in r:
                    res.append((pages[pi][li][0], pages[pi][li][1], "N"))

        for prev, cur in zip(nums, nums[1:], strict=False):
            if cur[0] == prev[0] + 1 and cur[2] == prev[2] + 1:
                run.append(cur)
            else:
                flush(run)
                run = [cur]
        flush(run)
    forms: dict[str, set[int]] = {}
    for pi, pg in enumerate(pages):
        bd = _boundary(pg)
        for li in set(bd["first"]) | set(bd["last"]):
            t = _ws(pg[li][2])
            if not _guards_ok(t) or re.fullmatch(r"\d{1,3}", t):
                continue
            if not (_PAGEFRAG.match(t) or _header_evidence(t)):
                continue
            forms.setdefault(_dn(t), set()).add(pi)
    for _pi, pg in enumerate(pages):
        bd = _boundary(pg)
        for li in set(bd["first"]) | set(bd["last"]):
            t = _ws(pg[li][2])
            if (
                _dn(t) in forms
                and len(forms[_dn(t)]) >= MIN_PAGES_RECURRENCE
                and _guards_ok(t)
                and not re.fullmatch(r"\d{1,3}", t)
            ):
                res.append((pg[li][0], pg[li][1], "R"))
    return sorted(set(res))


def _upper_share(t: str) -> float:
    ls = [c for c in t if c.isalpha()]
    return sum(c.isupper() for c in ls) / len(ls) if ls else 0.0


def _protected_lines(text: str) -> set[tuple[int, int]]:
    """The caption guard: cause-title, party, advocate, charge and prayer lines are never deleted."""
    pages = _pages_and_lines(text)
    prot: set[tuple[int, int]] = set()
    for pi, pg in enumerate(pages):
        ne = [i for i, (_, _, ln) in enumerate(pg) if ln.strip()]
        g2 = {i for i in ne if _CAP_VOCAB.search(pg[i][2])}
        for i in ne:
            t = _ws(pg[i][2])
            if (
                pi == 0
                and not re.fullmatch(r"\d{1,3}", t)
                and not re.fullmatch(r"(?i)page\s+\d+(\s+of\s+\d+)?", t)
                and not _URL.search(t)
            ):
                prot.add((pg[i][0], pg[i][1]))
            if i in g2 or len(t.split()) >= 3 and _upper_share(t) >= 0.5 and any(abs(ne.index(i) - ne.index(j)) <= 2 for j in g2):
                prot.add((pg[i][0], pg[i][1]))
    return prot


def _n_has_stamp_neighbour(pages: list[list[tuple[int, int, str]]], span: tuple[int, int]) -> bool:
    for pg in pages:
        for k, (a, b, _) in enumerate(pg):
            if (a, b) == span:
                ne = [i for i, (_, _, ln) in enumerate(pg) if ln.strip()]
                pos = ne.index(k)
                nb = [ne[j] for j in (pos - 1, pos + 1) if 0 <= j < len(ne)]
                return any(_is_stamp(pg[i][2]) or bool(_NEUTRAL.search(pg[i][2])) for i in nb)
    return False


def furniture_lines(raw: str) -> list[tuple[int, int, str]]:
    """Lines (start, end excluding the newline, class) that are admitted page furniture. Boundary evidence only (pages are
    separated by form feed); a text with no page breaks yields none.
    Never a caption line; never a class outside ADMITTED_KINDS."""
    pages = _pages_and_lines(raw)
    prot = _protected_lines(raw)
    out = []
    for a, b, c in _f2_spans(raw):
        t = _ws(raw[a:b])
        if c == "N":  # noqa: SIM108
            ok = _n_has_stamp_neighbour(pages, (a, b))
        else:
            ok = _is_stamp(t)
        if ok and _kind(t) in ADMITTED_KINDS and not any(a < pe and ps < b for ps, pe in prot):
            out.append((a, b, "furniture"))
    return out


_HYPHEN = re.compile(r"(?<=[^\W\d_])-\r?\n(?=[^\W\d_])")


def _admissible_deletions(raw: str) -> dict[tuple[int, int], str]:
    """Every deletion the cleaner may make, by recomputation: furniture lines (with their one trailing newline)
    and hyphen joins."""
    out: dict[tuple[int, int], str] = {}
    for a, b, _ in furniture_lines(raw):
        e = b + (1 if b < len(raw) and raw[b] == "\n" else 0)
        out[(a, e)] = "furniture"
    for m in _HYPHEN.finditer(raw):
        if raw[m.end()].islower():
            out[(m.start(), m.end())] = "hyphen_join"
    return out


# ---- tool: clean --------------------------------------------------------------------------------------------------------
def clean(raw: str) -> dict[str, Any]:
    """-> {"cleaned", "segments": [[cs,ce,rs,re,"c"|"w"]...], "deletions": [[rs,re,cls]...]}. Segments and deletions
    tile the raw text."""
    dels = _admissible_deletions(raw)
    marks = sorted(dels)
    # drop overlaps (keep the first by position): a furniture line next to a hyphen join cannot overlap in practice
    kept_marks: list[tuple[int, int]] = []
    for a, e in marks:
        if not kept_marks or a >= kept_marks[-1][1]:
            kept_marks.append((a, e))
    starts = dict(kept_marks)
    segments: list[list[Any]] = []
    deletions: list[list[Any]] = []
    out: list[str] = []
    cpos = 0
    prev_ws = True  # leading whitespace is deleted

    def emit_copy(a: int, e: int) -> None:
        nonlocal cpos, prev_ws
        segments.append([cpos, cpos + (e - a), a, e, "c"])
        out.append(raw[a:e])
        cpos += e - a
        prev_ws = False

    def emit_text(a: int, e: int) -> None:
        for m in re.finditer(r"\s+|\S+", raw[a:e]):
            s, t = a + m.start(), a + m.end()
            if m.group().isspace():
                if prev_ws:
                    deletions.append([s, t, "ws"])
                else:
                    piece = _ws_piece(raw, s, t, starts)
                    nonlocal_emit_ws(s, t, piece)
            else:
                emit_copy(s, t)

    def nonlocal_emit_ws(s: int, t: int, piece: str) -> None:
        nonlocal cpos, prev_ws
        segments.append([cpos, cpos + len(piece), s, t, "w"])
        out.append(piece)
        cpos += len(piece)
        prev_ws = True

    pos = 0
    for a, e in [*kept_marks, (len(raw), len(raw))]:
        if a > pos:
            emit_text(pos, a)
        if e > a:
            deletions.append([a, e, dels[(a, e)]])
        pos = e
    # trailing whitespace is deleted: the last emitted piece must not be whitespace
    while segments and segments[-1][4] == "w":
        s = segments.pop()
        out.pop()
        cpos -= s[1] - s[0]
        deletions.append([s[2], s[3], "ws"])
    deletions.sort()
    return {"cleaned": "".join(out), "segments": segments, "deletions": deletions}


# ---- checks C1-C3 -------------------------------------------------------------------------------------------------------
def check_clean(raw: str, cleaned: str, segments: Sequence[Sequence[Any]], deletions: Sequence[Sequence[Any]]) -> dict[str, Any]:
    """Recomputed from the raw text; nothing carried is trusted. C1 offset round-trip, C2 admissible
    deletions, C3 protected tokens."""
    c1_err: list[str] = []
    try:
        items = sorted(
            [(s[2], s[3], "seg", s) for s in segments] + [(d[0], d[1], "del", d) for d in deletions], key=lambda x: (x[0], x[1])
        )
        pos = 0
        for a, e, _kind, _it in items:
            if a != pos or e < a:
                c1_err.append(f"raw not tiled at {pos}")
                break
            pos = e
        else:
            if pos != len(raw):
                c1_err.append("raw not covered to its end")
        dstarts = {d[0]: d[1] for d in deletions if d[2] != "ws"}
        cpos = 0
        rebuilt: list[str] = []
        for s in segments:
            cs, ce, rs, re_, k = s
            if cs != cpos or ce < cs:
                c1_err.append("cleaned not tiled")
                break
            piece = cleaned[cs:ce]
            if k == "c":
                if piece != raw[rs:re_] or (ce - cs) != (re_ - rs):
                    c1_err.append(f"copy segment differs at cleaned {cs}")
                    break
            elif k == "w":
                run = raw[rs:re_]
                want = _ws_piece(raw, rs, re_, dstarts)
                if not run or not run.isspace() or piece != want:
                    c1_err.append(f"whitespace segment wrong at cleaned {cs}")
                    break
            else:
                c1_err.append("unknown segment kind")
                break
            rebuilt.append(piece)
            cpos = ce
        if not c1_err and ("".join(rebuilt) != cleaned or cpos != len(cleaned)):
            c1_err.append("segments do not rebuild the cleaned text")
    except (TypeError, ValueError, IndexError) as e:
        c1_err.append(f"malformed map: {type(e).__name__}")
    adm = _admissible_deletions(raw)
    c2_err: list[str] = []
    c3_err: list[str] = []
    try:
        for d in deletions:
            a, end, cls = d[0], d[1], d[2]
            if cls == "ws":
                if not raw[a:end] or not raw[a:end].isspace():
                    c2_err.append(f"ws deletion at {a} is not whitespace")
            elif adm.get((a, end)) != cls:
                c2_err.append(f"deletion {a}:{end} ({cls}) is not admissible")
        if not c1_err:
            for d in deletions:
                t = raw[d[0] : d[1]]
                if d[2] != "ws" and (_has_negation(t) or _CURR.search(t)):
                    c3_err.append(f"deletion at {d[0]} holds a protected word")
            tr, tc = _protected(raw), _protected(cleaned)
            td: Counter[str] = Counter()
            for d in deletions:
                td += _protected(raw[d[0] : d[1]])
            if tr - tc != td or tc - tr:
                c3_err.append("protected tokens differ beyond the deleted spans")
    except (TypeError, ValueError, IndexError) as e:
        c2_err.append(f"malformed deletions: {type(e).__name__}")
    return {
        "C1": {"pass": not c1_err, "detail": c1_err[:3]},
        "C2": {"pass": not c2_err, "detail": c2_err[:3]},
        "C3": {"pass": not c3_err, "detail": c3_err[:3]},
    }


_PROT = re.compile(r"\d[\d,./-]*\d|\d|\b[A-Z][A-Za-z]+\b|\b(?:not|no|never|nor|without|cannot|neither|none)\b|n't", re.I)


def _protected(text: str) -> Counter[str]:
    """Multiset of load-bearing tokens: numbers (amounts, dates, sections), negations, capitalised words (names). Hyphen joins
    and whitespace do not change it because the tokens are matched on text with hyphen-newline joins removed."""
    t = _HYPHEN.sub(lambda m: "" if m.string[m.end()].islower() else m.group(), text)
    return Counter(m.group() for m in _PROT.finditer(t))


# ---- tool: split_facts --------------------------------------------------------------------------------------------------
_ABBR = {
    "mr", "mrs", "ms", "dr", "sri", "shri", "smt", "km", "no", "nos", "sec", "ss", "art", "arts", "cr", "crl", "cri",
    "p", "c", "ltd",
    "pvt", "hon", "vs", "v", "dt", "rs", "i.e", "e.g", "etc", "viz", "ors", "anr", "addl", "dy", "supdt", "insp",
    "si", "asi", "pc", "hc",
    "sc", "st", "jr", "sr", "col", "gen", "capt", "prof", "u/s", "s/o", "d/o", "w/o", "r/o",
}  # fmt: skip
_SENT_END = re.compile(r"[.?!]['\"”’)\]]*\s+(?=[A-Z0-9\"'(“ऀ-ॿ])")


def _sentence_ends(par: str) -> list[int]:
    cuts: list[int] = []
    for m in _SENT_END.finditer(par):
        word = (re.search(r"(\S+)$", par[: m.start() + 1]) or re.search(r"(.)$", "x")).group(1)  # type: ignore[union-attr]
        w = word.strip("'\"()[]“”").rstrip(".?!").lower()
        if (
            w in _ABBR
            or re.fullmatch(r"(?:[a-z]\.){1,}[a-z]?", word.strip("'\"()[]").lower())
            or re.fullmatch(r"[a-z]{1,2}", w)
            and word.endswith(".")
        ):
            continue
        cuts.append(m.start() + len(m.group().rstrip()))
    return cuts


def split_facts(cleaned: str) -> list[dict[str, int]]:
    """Index spans over the CLEANED text: one candidate per paragraph (blank-line separated); a paragraph longer than
    MAX_FACT_CHARS is cut at sentence ends into groups of at most MAX_FACT_CHARS. Pure-number and letterless
    paragraphs are skipped."""
    spans: list[dict[str, int]] = []
    pos = 0
    for par in re.split(r"\n\n", cleaned):
        a = pos
        pos += len(par) + 2
        if not re.search(r"[^\W\d_]", par):
            continue
        if len(par) <= MAX_FACT_CHARS:
            spans.append({"cleaned_start": a, "cleaned_end": a + len(par)})
            continue
        ends = [*_sentence_ends(par), len(par)]
        start = 0
        last = 0
        for e in ends:
            if e - start > MAX_FACT_CHARS and last > start:
                spans.append({"cleaned_start": a + start, "cleaned_end": a + last})
                start = last
                while start < len(par) and par[start].isspace():
                    start += 1
            last = e
        if start < len(par):
            spans.append({"cleaned_start": a + start, "cleaned_end": a + len(par)})
    return spans


# ---- tool: extract_citations --------------------------------------------------------------------------------------------
def extract_citations(raw: str) -> list[dict[str, Any]]:
    from pravrudhi.application.citations import parse_citations

    out = []
    for c in parse_citations(raw):
        a, b = c.span
        out.append({"start": a, "end": b, "text": raw[a:b], "reporter": c.reporter, "year": c.year})
    return out


# ---- the final tool -----------------------------------------------------------------------------------------------------
def _raw_span(cs: int, ce: int, segments: Sequence[Sequence[Any]]) -> tuple[int, int] | None:
    """Raw span of the cleaned span [cs, ce); both ends must fall on copied characters, else None."""
    starts = [s[0] for s in segments]

    def at(i: int) -> Sequence[Any] | None:
        k = bisect.bisect_right(starts, i) - 1
        return segments[k] if 0 <= k < len(segments) and segments[k][0] <= i < segments[k][1] else None

    if not (0 <= cs < ce):
        return None
    s0, s1 = at(cs), at(ce - 1)
    if s0 is None or s1 is None or s0[4] != "c" or s1[4] != "c":
        return None
    return s0[2] + (cs - s0[0]), s1[2] + (ce - 1 - s1[0]) + 1


def build_bundle(
    raw: str,
    *,
    proposal: dict[str, Any] | None = None,
    verify_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """FINAL tool. `proposal` is what an agent carried ({"cleaned","segments","deletions","facts": [{"cleaned_start",
    "cleaned_end",
    optional "text"}]}); without one the deterministic baseline runs. Every check is recomputed from `raw`.
    The agent can never add
    text: a fact is the cleaned slice at its indexes, or it is marked paraphrase and is not citable."""
    ri = read_input(raw)
    prop = proposal or {}
    base = clean(raw)
    cl = {k: prop.get(k, base[k]) for k in ("cleaned", "segments", "deletions")} if prop.get("cleaned") is not None else base
    checks = check_clean(raw, cl["cleaned"], cl["segments"], cl["deletions"])
    clean_ok = all(checks[k]["pass"] for k in ("C1", "C2", "C3"))
    clean_status = "cleaned" if clean_ok else "served_raw"
    if not clean_ok:
        cl = {"cleaned": raw, "segments": [[0, len(raw), 0, len(raw), "c"]], "deletions": []}
    cleaned, segments, deletions = cl["cleaned"], cl["segments"], cl["deletions"]
    cand: list[dict[str, Any]] = prop["facts"] if (prop.get("facts") is not None and clean_ok) else split_facts(cleaned)
    if len(cand) > MAX_FACTS:
        raise IntakeRefused(f"more than {MAX_FACTS} fact candidates")
    facts: list[dict[str, Any]] = []
    not_citable: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    last_end = -1
    for c in cand:
        cs, ce = c.get("cleaned_start"), c.get("cleaned_end")
        item: dict[str, Any] = {"cleaned_start": cs, "cleaned_end": ce}
        if not (isinstance(cs, int) and isinstance(ce, int)) or not (0 <= cs < ce <= len(cleaned)) or cs < last_end:
            not_citable.append({**item, "status": "paraphrase", "reason": "span out of range, empty, or out of order"})
            continue
        last_end = ce
        text = cleaned[cs:ce]
        rs = _raw_span(cs, ce, segments)
        if not text.strip() or not re.search(r"[^\W\d_]", text):
            not_citable.append({**item, "status": "paraphrase", "reason": "no words in the span"})
            continue
        if "text" in c and c["text"] != text:
            not_citable.append({**item, "status": "paraphrase", "reason": "text is not the source span at these indexes"})
            continue
        if rs is None:
            not_citable.append({**item, "status": "paraphrase", "reason": "span does not start and end on source characters"})
            continue
        inside = [d for d in deletions if rs[0] <= d[0] and d[1] <= rs[1] and d[2] != "ws"]
        raw_kept = raw[rs[0] : rs[1]]
        for d in sorted(inside, key=lambda x: -x[0]):
            raw_kept = raw_kept[: d[0] - rs[0]] + raw_kept[d[1] - rs[0] :]
        status = "span"
        shown = text
        if _protected(raw_kept) != _protected(text):
            status, shown = "raw_fallback", _ws(raw[rs[0] : rs[1]])
        key = _ws(shown)
        if len(key) >= 40 and key in seen:
            not_citable.append(
                {**item, "status": "duplicate", "duplicate_of": seen[key], "reason": "same text as an earlier fact"}
            )
            continue
        fid = f"F{len(facts) + 1}"
        seen[key] = fid
        facts.append(
            {
                "id": fid,
                "text": shown,
                "status": status,
                "cleaned_start": cs,
                "cleaned_end": ce,
                "raw_start": rs[0],
                "raw_end": rs[1],
                "deletions_inside": len(inside),
            }
        )
    c4_bad = [n for n in not_citable if n["status"] == "paraphrase"]
    checks["C4"] = {
        "pass": not c4_bad,
        "detail": [n["reason"] for n in c4_bad[:3]],
        "citable": len(facts),
        "not_citable": len(not_citable),
    }
    covered = sum(f["cleaned_end"] - f["cleaned_start"] for f in facts)
    total = _nonspace(cleaned)
    gaps = []
    p = 0
    for f in facts:
        if f["cleaned_start"] - p >= 200 and _nonspace(cleaned[p : f["cleaned_start"]]):
            gaps.append([p, f["cleaned_start"]])
        p = f["cleaned_end"]
    if len(cleaned) - p >= 200 and _nonspace(cleaned[p:]):
        gaps.append([p, len(cleaned)])
    checks["C5"] = {"pass": True, "covered_chars": covered, "cleaned_nonspace_chars": total, "uncovered_runs": gaps[:20]}
    cits = []
    c6_bad = []
    for c in extract_citations(raw):
        if raw[c["start"] : c["end"]] != c["text"]:
            c6_bad.append(c["start"])
            continue
        try:
            status = verify_fn(c["text"]) if verify_fn else "NOT_CHECKED"
        except Exception:  # a verifier fault is shown, never a guessed status
            status = "CHECK_UNAVAILABLE"
        in_facts = [f["id"] for f in facts if f["raw_start"] <= c["start"] and c["end"] <= f["raw_end"]]
        cits.append({**c, "verifier_status": str(status), "fact_ids": in_facts})
    checks["C6"] = {"pass": not c6_bad, "detail": c6_bad[:3], "count": len(cits)}
    unread = sorted(set(prop.get("unread_pages", []))) if isinstance(prop.get("unread_pages", []), list) else []
    body = {
        "raw_sha256": ri["raw_sha256"],
        "n_chars": ri["n_chars"],
        "clean_status": clean_status,
        "cleaned": cleaned,
        "segments": segments,
        "deletions": deletions,
        "facts": facts,
        "not_citable": not_citable,
        "citations": cits,
        "unread_pages": unread,
        "checks": checks,
    }
    checks["C7"] = {"pass": True, "bundle_sha256": sha(canon(body))}
    body["bundle_sha256"] = checks["C7"]["bundle_sha256"]
    return body
