"""Intake (Workflows row 2, pravrudhi#414): the pure tools and the deterministic final tool `build_bundle`.

Input is raw text the ENGINE already holds (pasted text, or the text layer of a text PDF with pages joined by form feed "\\f").
Nothing here uses a model, the network, a file or a clock. Every function is pure; the same raw text gives the same bundle.

Tools (the agent may call them as tool calls; the baseline route calls them in-process, with no model at all):
  read_input         the request's raw text: sha256, length, page starts; refuses empty or over-cap text.
  clean              F0 v1: delete admitted page furniture (behind a caption guard), join hyphenated line breaks,
                     collapse whitespace; returns the cleaned text plus a segment map and the list of deleted raw spans.
  split_facts        rule-based paragraph / sentence-group candidates over the CLEANED text, as index spans only.
  extract_citations  the existing typed parser (citations.parse_citations) over the RAW text, with raw spans.
  build_bundle       FINAL. Recomputes every check from the raw text; only what verifies is marked citable.

The bundle checks: C1 offset round-trip cleaned to raw; C2 every deletion is
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
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        raise IntakeRefused("text has characters that cannot be read") from None
    starts = [0]
    for i, c in enumerate(raw):
        if c == "\f":
            starts.append(i + 1)
    return {"raw_sha256": sha(raw), "n_chars": len(raw), "page_starts": starts}


# ---- page furniture (ported from a research cleaner; only the admitted kinds are used) ------------------------
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
    for _ in range(len(ends_by_start) + 1):
        nxt = ends_by_start.get(e)
        if nxt is None or nxt <= e:
            break
        e = nxt
    return e


def _ws_piece(raw: str, s: int, e: int, ends_by_start: dict[int, int]) -> str:
    """What a whitespace run becomes: a paragraph break when it holds two newlines, or one newline before a numbered-paragraph
    marker ("12." / "(3)" / "(iv)"); a single space otherwise. Used by `clean` and re-applied by `check_clean`."""
    run = raw[s:e]
    nxt = _next_kept(e, ends_by_start)
    if "\n" in run and _PARA_START.match(raw[nxt : nxt + 16]):
        return "\n\n"
    if run.count("\n") >= 2:
        # a text layer that puts a blank line after every printed line: a break after a line that does not end a sentence is
        # the same sentence continuing when the next word is lower case, a digit, "&" or "(", or when the line was a full-width
        # printed line (45 characters or more) that ends in a lower-case letter (so not a heading or a party line)
        before = raw[s - 1] if s > 0 else "."
        after = raw[nxt] if nxt < len(raw) else "A"
        line_start = raw.rfind("\n", 0, s) + 1
        line_len = s - line_start
        families = _cue_families(raw[line_start:s])
        open_line = (
            before not in ".?!:;)\"'\u201d\u2019\u0964" and "identity" not in families
        )  # a header identity line never continues
        full_width = before.islower() and line_len >= 45 and not families  # nor does any other header-cue line, by width alone
        if open_line and (after.islower() or after.isdigit() or after in "&(" or full_width):
            return " "
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
    if len(t) > _MAX_LINE or t.count(".") > 8:  # bounds the stamp grammar's backtracking
        return False
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


def _mostly_upper(t: str, share: float = 0.5) -> bool:
    """Yes/no: at least `share` of the letters are upper case. With no letters the answer is no (no rate is made up)."""
    ls = [c for c in t if c.isalpha()]
    if not ls:
        return False
    return sum(c.isupper() for c in ls) / len(ls) >= share


def _protected_lines(text: str) -> set[tuple[int, int]]:
    """The caption guard: cause-title, party, advocate, charge and prayer lines are never deleted."""
    pages = _pages_and_lines(text)
    prot: set[tuple[int, int]] = set()
    for pi, pg in enumerate(pages):
        ne = [i for i, (_, _, ln) in enumerate(pg) if ln.strip()]
        g2 = {i for i in ne if _CAP_VOCAB.search(pg[i][2])}
        pos_of = {i: k for k, i in enumerate(ne)}
        g2pos = sorted(pos_of[j] for j in g2)

        def near(k: int, g2pos: list[int] = g2pos) -> bool:
            x = bisect.bisect_left(g2pos, k)
            return any(0 <= y < len(g2pos) and abs(g2pos[y] - k) <= 2 for y in (x - 1, x))

        for i in ne:
            t = _ws(pg[i][2])
            if (
                pi == 0
                and not re.fullmatch(r"\d{1,3}", t)
                and not re.fullmatch(r"(?i)page\s+\d+(\s+of\s+\d+)?", t)
                and not _URL.search(t)
            ):
                prot.add((pg[i][0], pg[i][1]))
            if i in g2 or len(t.split()) >= 3 and _mostly_upper(t) and near(pos_of[i]):
                prot.add((pg[i][0], pg[i][1]))
    return prot


def _stamp_neighbours(pages: list[list[tuple[int, int, str]]]) -> dict[tuple[int, int], bool]:
    """For every line (start, end): is the previous or next non-empty line of its page a header stamp? Built once per text."""
    out: dict[tuple[int, int], bool] = {}
    for pg in pages:
        ne = [i for i, (_, _, ln) in enumerate(pg) if ln.strip()]
        for pos, k in enumerate(ne):
            nb = [ne[j] for j in (pos - 1, pos + 1) if 0 <= j < len(ne)]
            out[(pg[k][0], pg[k][1])] = any(
                len(pg[i][2]) <= 200 and (_is_stamp(pg[i][2]) or bool(_NEUTRAL.search(pg[i][2]))) for i in nb
            )
    return out


_COUNTERS = (
    re.compile(r"^\s*Page\s+(\d+)\s+of\s+\d+\s*$", re.I),
    re.compile(r"^\s*\{\{\s*(\d+)\s*\}\}\s*$"),
    re.compile(r"^\s*-\s*(\d+)\s*-\s*$"),
    re.compile(r"^\s*::\s*(\d+)\s*::\s*$"),
)


def _counter_lines(pages: list[list[tuple[int, int, str]]]) -> list[tuple[int, int]]:
    """Page counters ("Page 4 of 13", "{{ 4 }}", "- 4 -") that stand alone on a line and count 1 up over three or more lines in
    a row: evidence that does not need form feeds (a text with no page breaks)."""
    lines = [ln for pg in pages for ln in pg]
    out: list[tuple[int, int]] = []
    for rx in _COUNTERS:
        hits = [(a, b, int(m.group(1))) for a, b, ln in lines if (m := rx.match(ln))]  # the matching lines, in document order
        run: list[tuple[int, int, int]] = []
        for h in [*hits, None]:
            if h is not None and run and h[2] == run[-1][2] + 1:
                run.append(h)
                continue
            if len(run) >= 3:
                out.extend((x, y) for x, y, _ in run)
            run = [h] if h is not None else []
    return out


def furniture_lines(raw: str) -> list[tuple[int, int, str]]:
    """Lines (start, end excluding the newline, class) that are admitted page furniture: boundary evidence (pages separated by
    form feed) or a counter that counts up over three or more lines. Never a caption line; boundary lines only of the
    ADMITTED_KINDS."""
    pages = _pages_and_lines(raw)
    prot = sorted(_protected_lines(raw))
    pstarts = [ps for ps, _ in prot]
    nbmap = _stamp_neighbours(pages)
    out = []
    for a, b, c in _f2_spans(raw):
        t = _ws(raw[a:b])
        if c == "N":  # noqa: SIM108
            ok = nbmap.get((a, b), False)
        else:
            ok = _is_stamp(t)
        j = (
            bisect.bisect_left(pstarts, b) - 1
        )  # protected lines are disjoint and sorted: only the last one starting before b can overlap
        overlaps = j >= 0 and prot[j][1] > a
        if ok and _kind(t) in ADMITTED_KINDS and not overlaps:
            out.append((a, b, "furniture"))
    have = {(a, b) for a, b, _ in out}
    for a, b in _counter_lines(pages):  # a bare counter holds no name or caption text: the page-one guard does not apply
        if (a, b) not in have:
            out.append((a, b, "furniture"))
    return sorted(out)


_HYPHEN = re.compile(r"(?<=[^\W\d_])-\r?\n(?=[^\W\d_])")


def _admissible_deletions(raw: str) -> dict[tuple[int, int], str]:
    """Every deletion the cleaner may make, by recomputation: furniture lines (with their one trailing newline)
    and hyphen joins."""
    out: dict[tuple[int, int], str] = {}
    for a, b, _ in furniture_lines(raw):
        e = b + (1 if b < len(raw) and raw[b] == "\n" else 0)
        out[(a, e)] = "furniture"
    furniture = [(a, e) for (a, e), cls in out.items() if cls == "furniture"]
    furniture_starts = {a for a, _e in furniture}
    for m in _HYPHEN.finditer(raw):
        # no join into a line the cleaner deletes (the word would be glued to whatever follows it), and none inside one
        inside = any(a <= m.start() < e for a, e in furniture)
        if raw[m.end()].islower() and m.end() not in furniture_starts and not inside:
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
            if a != pos or e <= a:
                c1_err.append(f"raw not tiled at {pos}")
                break
            pos = e
        else:
            if pos != len(raw):
                c1_err.append("raw not covered to its end")
        dstarts = {d[0]: d[1] for d in deletions if d[2] != "ws" and d[1] > d[0]}
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
    base_ws = {(d[0], d[1]) for d in clean(raw)["deletions"] if d[2] == "ws"}
    c2_err: list[str] = []
    c3_err: list[str] = []
    try:
        for d in deletions:
            a, end, cls = d[0], d[1], d[2]
            if cls == "ws":
                if not raw[a:end] or not raw[a:end].isspace() or (a, end) not in base_ws:
                    c2_err.append(f"ws deletion at {a} is not one the cleaner makes")
            elif adm.get((a, end)) != cls:
                c2_err.append(f"deletion {a}:{end} ({cls}) is not admissible")
        if not c1_err:
            for d in deletions:
                t = raw[d[0] : d[1]]
                if d[2] != "ws" and (_has_negation(t) or _CURR.search(t)):
                    c3_err.append(f"deletion at {d[0]} holds a protected word")
            joined = raw
            for d in sorted((d for d in deletions if d[2] == "hyphen_join"), key=lambda x: -x[0]):
                joined = joined[: d[0]] + joined[d[1] :]  # only the joins the cleaner carried, not every hyphen-newline
            tr, tc = _protected(joined), _protected(cleaned)
            td: Counter[str] = Counter()
            for d in deletions:
                if d[2] != "hyphen_join":
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


_PROT = re.compile(r"\d[\d,./-]*\d|\d|[^\W\d_]+")


def _protected(text: str) -> Counter[str]:
    """Multiset of load-bearing tokens: numbers (amounts, dates, sections) and every word of any script (negations, names).
    A pure tokeniser: the caller removes the hyphen joins it carried before counting."""
    return Counter(m.group() for m in _PROT.finditer(text))


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
        i = m.start() + 1
        j = i
        while j > 0 and not par[j - 1].isspace():
            j -= 1
        word = par[j:i]
        wo = word.strip("'\"()[]“”").rstrip(".?!")
        w = wo.lower()
        if (
            w in _ABBR
            or re.fullmatch(r"(?:[a-z]\.){1,}[a-z]?", word.strip("'\"()[]").lower())
            or (re.fullmatch(r"[A-Z]{1,2}", wo) and word.endswith("."))
        ):
            continue
        cuts.append(m.start() + len(m.group().rstrip()))
    return cuts


def _hard_cut(par: str, a: int, spans: list[dict[str, int]]) -> None:
    """Cut a piece with no usable sentence end at whitespace near MAX_FACT_CHARS (never inside a word)."""
    start = 0
    while len(par) - start > MAX_FACT_CHARS:
        cut = par.rfind(" ", start + MAX_FACT_CHARS // 2, start + MAX_FACT_CHARS)
        if cut <= start:
            cut = start + MAX_FACT_CHARS
        spans.append({"cleaned_start": a + start, "cleaned_end": a + cut})
        start = cut
        while start < len(par) and par[start].isspace():
            start += 1
    if start < len(par):
        spans.append({"cleaned_start": a + start, "cleaned_end": a + len(par)})


def _merge_down(spans: list[dict[str, int]], cleaned: str, excluded: Sequence[dict[str, Any]] = ()) -> list[dict[str, int]]:
    """When there are more than MAX_FACTS candidates, merge neighbours (a contiguous cleaned slice) under a growing size cap.
    Two facts are never merged across an excluded span."""
    cap = MAX_FACT_CHARS
    while len(spans) > MAX_FACTS and cap < MAX_CHARS:
        cap *= 2
        merged: list[dict[str, int]] = []
        for s in spans:
            gap_free = not merged or not any(
                e["cleaned_start"] < s["cleaned_start"] and e["cleaned_end"] > merged[-1]["cleaned_end"] for e in excluded
            )
            if merged and gap_free and s["cleaned_end"] - merged[-1]["cleaned_start"] <= cap:
                merged[-1] = {"cleaned_start": merged[-1]["cleaned_start"], "cleaned_end": s["cleaned_end"]}
            else:
                merged.append(dict(s))
        spans = merged
    return spans


MIN_FACT_CHARS = 80  # a fact shorter than this is joined to its neighbour unless it starts a numbered paragraph
TARGET_FACT_CHARS = 300  # a group of paragraphs is closed once it reaches this size
LONG_PARA = 150  # the first paragraph this long is where the body of an order starts, when no ORDER / JUDGMENT heading is found
REPEAT_MIN = 3  # a short line that occurs this many times is page furniture, not a fact
_BODY_HEADING = re.compile(
    r"^\W*(?:o\s*r\s*d\s*e\s*r|j\s*u\s*d\s*g\s*m\s*e\s*n\s*t|oral\s+order|common\s+order|oral\s+judgment)\W*$", re.I
)


def _numbered_prose(par: str) -> bool:
    """A numbered paragraph that reads as prose: at least two words that do not start with a capital letter (so also
    Devanagari). A party line such as "1. AMARESH" or "1. Ranjeet Narayan Mishra" is not prose."""
    if not _PARA_START.match(par[:16]):
        return False
    words = [w for w in par.split()[1:] if re.search(r"[^\W\d_]", w)]
    return sum(1 for w in words if not next(c for c in w if c.isalpha()).isupper()) >= 2


def _merge_letterless(facts: list[dict[str, int]], cleaned: str) -> list[dict[str, int]]:
    """A fact with no letters (a stray "102." left by a sentence cut) is joined to the fact before it, else the one after."""
    out: list[dict[str, int]] = []
    carry: dict[str, int] | None = None
    for f in facts:
        if carry is not None:
            f = {"cleaned_start": carry["cleaned_start"], "cleaned_end": f["cleaned_end"]}
            carry = None
        if re.search(r"[^\W\d_]", cleaned[f["cleaned_start"] : f["cleaned_end"]]):
            out.append(f)
        elif out:
            out[-1] = {"cleaned_start": out[-1]["cleaned_start"], "cleaned_end": f["cleaned_end"]}
        else:
            carry = f
    return out


def _paragraphs(cleaned: str) -> list[tuple[int, int]]:
    out, pos = [], 0
    for par in re.split(r"\n\n", cleaned):
        a = pos + (len(par) - len(par.lstrip()))
        e = pos + len(par.rstrip())
        pos += len(par) + 2
        if e > a and re.search(r"[^\W\d_]", par):
            out.append((a, e))
    return out


def _letterless(cleaned: str) -> list[tuple[int, int]]:
    """Paragraphs with no letters at all (a page number, an amount, a mark): never facts."""
    out, pos = [], 0
    for par in re.split(r"\n\n", cleaned):
        a = pos + (len(par) - len(par.lstrip()))
        e = pos + len(par.rstrip())
        pos += len(par) + 2
        if e > a and not re.search(r"[^\W\d_]", par):
            out.append((a, e))
    return out


def _pieces(cleaned: str, a: int, b: int) -> list[tuple[int, int]]:
    """One paragraph as pieces of at most MAX_FACT_CHARS: cut at sentence ends, or at whitespace when a sentence is longer."""
    if b - a <= MAX_FACT_CHARS:
        return [(a, b)]
    par = cleaned[a:b]
    ends = [*_sentence_ends(par), len(par)]
    start = last = 0
    pieces: list[tuple[int, int]] = []
    for e in ends:
        if e - start > MAX_FACT_CHARS and last > start:
            pieces.append((start, last))
            start = last
            while start < len(par) and par[start].isspace():
                start += 1
        last = e
    if start < len(par):
        pieces.append((start, len(par)))
    spans: list[dict[str, int]] = []
    for s0, e0 in pieces:
        if e0 - s0 > MAX_FACT_CHARS:
            _hard_cut(par[s0:e0], a + s0, spans)
        else:
            spans.append({"cleaned_start": a + s0, "cleaned_end": a + e0})
    return [(s["cleaned_start"], s["cleaned_end"]) for s in spans]


_CUES = {
    "identity": re.compile(r"\b(?:s|d|w|r)/o\b|\bresident\s+of\b|\bresiding\s+at\b", re.I),
    "age": re.compile(r"\baged\s+(?:about\s+)?\d+", re.I),
    "bench": re.compile(r"\bcoram\b|\bhon'?ble\s+(?:mr|mrs|ms|dr|justice)\b", re.I),
    "counsel": re.compile(r"\bfor\s+(?:the\s+)?(?:appellants?|respondents?|petitioners?|applicants?)\s*[-:]", re.I),
    "dates": re.compile(
        r"\bdate\s+of\s+(?:hearing|judg(?:e)?ment|order)\b|\bdated\s+this\s+the\b|\border\s+on\s+board\b|\b(?:heard|hearing)\b[^.]{0,40}\breserved\b",
        re.I,
    ),
    # A run of dots or hyphens is tried from its start only (`(?<!\.)`, `(?<!-)`): a match that starts inside a run also
    # matches from the run's start, so the answer is the same, and a long run no longer costs a pass per position.
    "parties": re.compile(
        r"\bbetween\s*:|^\s*and\s*:|-versus-|\bversus\b|(?:(?<!\.)\.{2,}|\u2026|(?<!-)-{2,})\s*(?:appellants?|respondents?|petitioners?|applicants?)\b",
        re.I,
    ),
    "preamble": re.compile(r"\bfiled\s+(?:under|u/s)\b[^.]{0,200}\bpraying\b", re.I),
}


def _cue_families(par: str) -> set[str]:
    return {name for name, rx in _CUES.items() if rx.search(par)}


def _caption_like(par: str) -> bool:
    """A party block, counsel line, bench, date line or appeal preamble of the header of an order (privacy: age, parent's name,
    address), or a long all-capitals paragraph. A piece is header-like with one cue, EXCEPT a numbered prose paragraph (any
    length), which is header-like only with an identity cue (S/o, D/o, W/o, R/o, resident of, residing at) or
    two different kinds of cue: a numbered paragraph that says "aged 45 years" is the body."""
    families = _cue_families(par)
    if _numbered_prose(par):  # at any length: one weak cue ("aged 45 years") is the body, not the header
        return "identity" in families or len(families) >= 2
    if len(par) < LONG_PARA:
        return bool(families)
    return bool(families) or _mostly_upper(par, 0.75)


def split_plan(cleaned: str) -> dict[str, list[dict[str, Any]]]:
    """The deterministic fact split of a cleaned text: {"facts": index spans, "excluded": spans that are not facts and why}.
    1. The header of an order (court, bench, parties, counsel, dates, the appeal preamble) is not a fact and is personal data: the
       body starts after the first ORDER / JUDGMENT heading when at least two header-like pieces come before it, else at the
       first long or numbered-prose piece that is not header-like; what comes before is one excluded span ("caption").
       (A header that is more than half of the text is not treated as a header.)
    2. A short line that occurs REPEAT_MIN or more times (a page stamp the cleaner does not remove) is excluded.
    3. The rest is packed into facts: pieces are joined until a group reaches TARGET_FACT_CHARS, never past MAX_FACT_CHARS;
       a numbered paragraph ("12.", "(3)") always starts a new fact, except that a short
       heading before it (under MIN_FACT_CHARS, not numbered) joins it; an excluded span ends a group.
    More than MAX_FACTS facts are merged into larger neighbours rather than refused."""
    units: list[tuple[int, int, bool]] = []  # (start, end, whole paragraph)
    for a, b in _paragraphs(cleaned):
        pieces = _pieces(cleaned, a, b)
        units.extend((x, y, len(pieces) == 1) for x, y in pieces)
    excluded: list[dict[str, Any]] = []
    body_i = 0
    total = sum(b - a for a, b, _ in units)
    caption_flags: list[bool] = []
    if units:
        caption_flags = [_caption_like(cleaned[a:b]) for a, b, _ in units]
        heading = next(
            (
                i
                for i, (a, b, _) in enumerate(units[: max(1, int(len(units) * 0.8))])
                if _BODY_HEADING.match(cleaned[a:b]) and i + 1 < len(units) and sum(caption_flags[:i]) >= 2
            ),
            None,
        )
        if heading is not None:
            body_i = heading + 1
        else:
            body_i = next(
                (
                    i
                    for i, (a, b, _) in enumerate(units)
                    if not caption_flags[i] and (b - a >= LONG_PARA or _numbered_prose(cleaned[a:b]))
                ),
                0,
            )
    header_idx: set[int] = set()
    if body_i > 0:

        def header_ish(i: int) -> bool:
            a, b, _ = units[i]
            line = cleaned[a:b]
            return (
                caption_flags[i]
                or bool(_BODY_HEADING.match(line))
                or (len(line) < LONG_PARA and _mostly_upper(line, 0.6))
                or line.upper().startswith(("IN THE ", "BEFORE"))
            )

        marks = [i for i in range(body_i) if header_ish(i)]
        # fail toward keeping text: the block is a header only if it holds a real header cue; only header-like pieces and short
        # lines between or just after them are excluded, never a long piece without a cue (that is prose of the order)
        if marks and any(caption_flags[i] for i in marks):
            cap_lo, cap_hi = marks[0], marks[-1]
            while cap_hi + 1 < body_i and units[cap_hi + 1][1] - units[cap_hi + 1][0] < MIN_FACT_CHARS:
                cap_hi += 1  # short lines right after the last header line (a counsel or bench line) belong to it
            header_idx = {i for i in range(cap_lo, cap_hi + 1) if header_ish(i) or units[i][1] - units[i][0] < LONG_PARA}
            if sum(units[i][1] - units[i][0] for i in header_idx) > total / 2:
                header_idx = set()
    runs: list[tuple[int, int]] = []
    for i in sorted(header_idx):
        if runs and runs[-1][1] == i - 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    for lo, hi in runs:
        excluded.append(
            {
                "cleaned_start": units[lo][0],
                "cleaned_end": units[hi][1],
                "status": "caption",
                "reason": "the header of the order, before its body",
            }
        )
    body: list[tuple[int, int, bool] | None] = []
    run_starts = {lo for lo, _ in runs}
    for i, unit in enumerate(units):
        if i in header_idx:
            if i in run_starts:
                body.append(None)  # a header run ends a group
            continue
        body.append(unit)

    def repeats(a: int, b: int, whole: bool) -> bool:
        line = cleaned[a:b]
        looks_like_furniture = bool(
            re.search(r"\d", line) or _URL.search(line) or (len(line.split()) >= 2 and _mostly_upper(line, 0.7))
        )
        # a numbered paragraph is body; so is a short line with a negation ("No.", "Not allowed")
        # a page stamp differs from the next one by a number or two; a line with amounts, dates or a percentage is content
        content_numbers = len(re.findall(r"\d+", line)) > 2 or bool(_CURR.search(line)) or "%" in line
        return (
            whole
            and b - a < 100
            and looks_like_furniture
            and not content_numbers
            and not _PARA_START.match(line[:16])
            and not _has_negation(line)
        )

    seen: Counter[str] = Counter(_dn(cleaned[x[0] : x[1]]) for x in body if x is not None and repeats(x[0], x[1], x[2]))
    packed: list[tuple[int, int] | None] = []  # None = a group break (an excluded line)
    for item in body:
        if item is None:
            packed.append(None)
            continue
        a, b, w = item
        if repeats(a, b, w) and seen[_dn(cleaned[a:b])] >= REPEAT_MIN:
            excluded.append(
                {
                    "cleaned_start": a,
                    "cleaned_end": b,
                    "status": "repeated_line",
                    "reason": "the same short line occurs 3 or more times",
                }
            )
            packed.append(None)
        else:
            packed.append((a, b))
    facts: list[dict[str, int]] = []
    cur: tuple[int, int] | None = None
    has_numbered = False
    for u in packed:
        if u is None:
            if cur:
                facts.append({"cleaned_start": cur[0], "cleaned_end": cur[1]})
            cur = None
            has_numbered = False
            continue
        if cur is None:
            cur = u
            has_numbered = bool(_PARA_START.match(cleaned[u[0] : u[0] + 16]))
            continue
        size = cur[1] - cur[0]
        numbered = bool(_PARA_START.match(cleaned[u[0] : u[0] + 16]))
        # a short heading (no numbered paragraph in the open group yet) attaches forward
        lead = not has_numbered and size < MIN_FACT_CHARS
        if size >= TARGET_FACT_CHARS or u[1] - cur[0] > MAX_FACT_CHARS or (numbered and not lead):
            facts.append({"cleaned_start": cur[0], "cleaned_end": cur[1]})
            cur = u
            has_numbered = numbered
        else:
            cur = (cur[0], u[1])
            has_numbered = has_numbered or numbered
    if cur:
        facts.append({"cleaned_start": cur[0], "cleaned_end": cur[1]})
    facts = _merge_down(_merge_letterless(facts, cleaned), cleaned, excluded)
    covered = [(f["cleaned_start"], f["cleaned_end"]) for f in facts] + [(e["cleaned_start"], e["cleaned_end"]) for e in excluded]
    for a, b in _letterless(cleaned):
        if not any(a < ce and cs < b for cs, ce in covered):
            excluded.append(
                {
                    "cleaned_start": a,
                    "cleaned_end": b,
                    "status": "no_words",
                    "reason": "no words in the line (a number or a mark)",
                }
            )
    return {"facts": facts, "excluded": sorted(excluded, key=lambda e: e["cleaned_start"])}


def split_facts(cleaned: str) -> list[dict[str, int]]:
    """Index spans over the CLEANED text: the facts of `split_plan` (the caption and repeated page lines are left out)."""
    return [{"cleaned_start": f["cleaned_start"], "cleaned_end": f["cleaned_end"]} for f in split_plan(cleaned)["facts"]]


# ---- the stated range for a usable fact list ----------------------------------------------------------------------------
USABLE_MIN_TEXT_CHARS = 2000  # shorter texts are not judged (a short note has few facts)
USABLE_FACTS_PER_10K = (10.0, 60.0)  # observed on 39 cleared public orders: 11.9 to 30.1 (median 21.0)
USABLE_MEDIAN_FACT_CHARS = (100, MAX_FACT_CHARS)  # observed: median fact 193 to 517 characters (median of medians 381)
USABLE_SHORT_FACT_SHARE_MAX = 0.20  # facts under 40 characters; observed maximum 0.09


USABLE_LEFT_OUT_SHARE_MAX = (
    0.35  # of the BODY (the text minus the recognised header) not in any fact; observed maximum is in the PR
)
LEFT_OUT_STATUSES = ("repeated_line", "no_words", "duplicate")  # body text in no fact (the header is apart)


def fact_list_usable(bundle: dict[str, Any]) -> dict[str, Any]:
    """The stated range of a usable fact list for one order (a rule, not a bundle check): facts per 10,000 characters within
    USABLE_FACTS_PER_10K, the median fact length within USABLE_MEDIAN_FACT_CHARS, no fact longer than MAX_FACT_CHARS, at most
    USABLE_SHORT_FACT_SHARE_MAX of the facts under 40 characters, and at most USABLE_LEFT_OUT_SHARE_MAX of the BODY left out of
    the facts (the body is the cleaned text minus the recognised header; the header's own share is reported as `caption_share`).
    Not applied to texts under USABLE_MIN_TEXT_CHARS. A bundle that cannot be read FAILS (no length, no facts list)."""
    n_chars = bundle.get("n_chars")
    facts = bundle.get("facts")
    if not isinstance(n_chars, int) or isinstance(n_chars, bool) or n_chars < 0 or not isinstance(facts, list):
        return {"applies": True, "pass": False, "reasons": ["the bundle has no readable length or fact list"], "n_facts": 0}
    try:
        lens = sorted(len(f["text"]) for f in facts)
        spans = bundle.get("not_citable", [])
        caption = sum(n["cleaned_end"] - n["cleaned_start"] for n in spans if n.get("status") == "caption")
        left_out = sum(n["cleaned_end"] - n["cleaned_start"] for n in spans if n.get("status") in LEFT_OUT_STATUSES)
        cleaned_len = len(bundle.get("cleaned", "")) or n_chars
    except (KeyError, TypeError):
        return {"applies": True, "pass": False, "reasons": ["the bundle has no readable length or fact list"], "n_facts": 0}
    out: dict[str, Any] = {"applies": n_chars >= USABLE_MIN_TEXT_CHARS, "pass": True, "reasons": [], "n_facts": len(lens)}
    if not out["applies"]:
        return out
    per10k = len(lens) / (n_chars / 10_000)
    median = lens[len(lens) // 2] if lens else None  # no facts: the median is not computed (None), never a measured 0
    short = (sum(1 for x in lens if x < 40) / len(lens)) if lens else 1.0
    body_len = max(1, cleaned_len - caption)
    left_out_share = left_out / body_len
    out.update(
        facts_per_10k=round(per10k, 1),
        median_fact_chars=median,
        short_fact_share=round(short, 2),
        longest_fact_chars=lens[-1] if lens else 0,
        caption_share=round(caption / cleaned_len, 3) if cleaned_len else 0.0,
        left_out_share=round(left_out_share, 3),
    )
    if not USABLE_FACTS_PER_10K[0] <= per10k <= USABLE_FACTS_PER_10K[1]:
        out["reasons"].append("facts per 10,000 characters outside the range")
    if median is None or not USABLE_MEDIAN_FACT_CHARS[0] <= median <= USABLE_MEDIAN_FACT_CHARS[1]:
        out["reasons"].append("median fact length outside the range")
    if lens and lens[-1] > MAX_FACT_CHARS:
        out["reasons"].append("a fact is longer than the cap")
    if short > USABLE_SHORT_FACT_SHARE_MAX:
        out["reasons"].append("too many facts under 40 characters")
    if left_out_share > USABLE_LEFT_OUT_SHARE_MAX:
        out["reasons"].append("too much of the body was left out of the facts")
    out["pass"] = not out["reasons"]
    return out


# ---- what the model may see ---------------------------------------------------------------------------------------------
def facts_for_model(raw: str) -> list[dict[str, str]]:
    """The ONLY text of an intake request that may go to a model: the numbered facts. The header of the order (parties with
    age, parent's name and address, counsel, bench, dates), repeated page lines and bare marks are excluded spans and are
    never part of it. An unreadable text gives no facts."""
    try:
        bundle = build_bundle(raw)
    except IntakeRefused:
        return []
    return [{"id": f["id"], "text": f["text"]} for f in bundle["facts"]]


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
    if proposal is not None and not isinstance(proposal, dict):
        raise IntakeRefused("proposal must be a mapping")
    prop = proposal or {}
    if prop.get("facts") is not None and not (
        isinstance(prop["facts"], list) and all(isinstance(f, dict) for f in prop["facts"])
    ):
        raise IntakeRefused("facts must be a list of index spans")
    base = clean(raw)
    cl = {k: prop.get(k, base[k]) for k in ("cleaned", "segments", "deletions")} if prop.get("cleaned") is not None else base
    checks = check_clean(raw, cl["cleaned"], cl["segments"], cl["deletions"])
    clean_ok = all(checks[k]["pass"] for k in ("C1", "C2", "C3"))
    clean_status = "cleaned" if clean_ok else "served_raw"
    if not clean_ok:
        cl = {"cleaned": raw, "segments": [[0, len(raw), 0, len(raw), "c"]], "deletions": []}
    cleaned, segments, deletions = cl["cleaned"], cl["segments"], cl["deletions"]
    plan = split_plan(cleaned)
    cand: list[dict[str, Any]] = prop["facts"] if (prop.get("facts") is not None and clean_ok) else plan["facts"]
    if len(cand) > MAX_FACTS * 2:
        raise IntakeRefused(f"more than {MAX_FACTS * 2} fact candidates")
    facts: list[dict[str, Any]] = []
    not_citable: list[dict[str, Any]] = list(plan["excluded"])  # the caption and repeated page lines: left out, said so
    seen: dict[str, str] = {}
    last_end = -1
    for c in cand:
        cs0, ce0 = c.get("cleaned_start"), c.get("cleaned_end")
        cs = cs0 if isinstance(cs0, int) and not isinstance(cs0, bool) else None
        ce = ce0 if isinstance(ce0, int) and not isinstance(ce0, bool) else None
        item: dict[str, Any] = {"cleaned_start": cs, "cleaned_end": ce}
        if cs is None or ce is None or not (0 <= cs < ce <= len(cleaned)) or cs < last_end:
            not_citable.append({**item, "status": "paraphrase", "reason": "span out of range, empty, or out of order"})
            continue
        if any(e["cleaned_start"] < ce and cs < e["cleaned_end"] for e in plan["excluded"]):
            not_citable.append(
                {**item, "status": "excluded", "reason": "the span overlaps a part of the text that is not a fact"}
            )
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
        "pass": not c4_bad and bool(facts),
        "detail": [n["reason"] for n in c4_bad[:3]] or ([] if facts else ["no citable fact was found"]),
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
        if not (0 <= c["start"] < c["end"] <= len(raw)) or raw[c["start"] : c["end"]] != c["text"]:
            c6_bad.append(c["start"])
            continue
        try:
            status = verify_fn(c["text"]) if verify_fn else "NOT_CHECKED"
        except Exception:  # a verifier fault is shown, never a guessed status
            status = "CHECK_UNAVAILABLE"
        in_facts = [f["id"] for f in facts if f["raw_start"] <= c["start"] and c["end"] <= f["raw_end"]]
        cits.append({**c, "verifier_status": str(status), "fact_ids": in_facts})
    checks["C6"] = {"pass": not c6_bad, "detail": c6_bad[:3], "count": len(cits)}
    n_pages = len(ri["page_starts"])
    up = prop.get("unread_pages", [])
    unread = (
        sorted({i for i in up if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < n_pages})
        if isinstance(up, list)
        else []
    )
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
