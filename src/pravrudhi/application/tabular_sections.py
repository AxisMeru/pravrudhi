# ruff: noqa: E501
"""S2: the two NEW scanners of the matter tabular table: section mentions and court / police station mentions. Pure: text in, forms out.

CLOSED forms only, from `assets/tabular/cues.json` (a lawyer confirms, strikes or adds entries). Nothing is inferred: a form is a substring of the passage
(`text[start:end] == form.text`), a number is shown as printed with the Act beside it, IPC and BNS are never mapped, and a recognised form that cannot be read
is returned as NOT READ with a fixed reason (`act_not_named`, `act_not_on_list`, `form_not_supported`, `name_not_clear`), never dropped and never expanded.
Dates are NOT scanned here: A3's `chronology_dates.scan_document` reads them, and a section string a date has claimed is excluded by the caller.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

CUES = Path(__file__).resolve().parent.parent / "assets" / "tabular" / "cues.json"

REASONS = ("act_not_named", "act_not_on_list", "form_not_supported", "name_not_clear")


@dataclass(frozen=True)
class Form:
    kind: str  # "section" | "place"
    start: int
    end: int
    text: str  # the exact matched substring of the passage, at most 200 characters
    status: str  # "read" | "not_read"
    reason: str | None = None
    act: str | None = None  # the Act id beside a read section number, as listed in cues.json
    place_kind: str | None = None  # "court" | "magistrate" | "station" for a place
    group: int = 0  # forms of ONE match (a number list) share a group: a join that cuts it is ONE not-read item


@lru_cache(maxsize=1)
def cues() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(CUES.read_text(encoding="utf-8")))


def _loose(form: str) -> str:
    """A cue as a regex that tolerates the spaces and dots a scan puts anywhere inside it ('Cr. P. C.' = 'Cr.P.C.' = 'CrPC')."""
    out = []
    for ch in form.strip():
        if ch in " .":
            out.append(r"[\s.]*" if ch == "." else r"\s+")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _alt(forms: list[str]) -> str:
    return "|".join(_loose(f) for f in sorted(forms, key=len, reverse=True))


@lru_cache(maxsize=1)
def _rx() -> dict[str, Any]:
    c = cues()
    act_forms = {f: a["id"] for a in c["acts"] for f in a["forms"]}
    act_alt = _alt(list(act_forms))
    num = r"[0-9]{1,4}(?:-?[A-Za-z](?![A-Za-z]))?(?:\([0-9]{1,3}\)(?:\([A-Za-z]\))*)*"
    numlist = rf"{num}(?:\s*(?:,|and|&|or)\s*{num})*"
    cue = r"(?<![A-Za-z0-9/])(?:sections?|secs?\.?|ss\.?|s\.|u/s\.?)"
    return {
        "act_forms": act_forms,
        "act_rx": re.compile(rf"(?P<act>{act_alt})(?![A-Za-z])", re.I),
        "num": re.compile(num),
        "section": re.compile(rf"{cue}\s*(?P<nums>{numlist})", re.I),
        "unsupported_tail": re.compile(r"(?:\s*(?:-|–|—|to)\s*[0-9]|/\s*[0-9]|\s*(?:r\s*/\s*w|r\.\s*w\.|read\s+with)\s*[0-9])", re.I),
        "unsupported_run": re.compile(r"(?:[0-9\-–—/,&]|\s|to\b|and\b|r\s*/\s*w|r\.\s*w\.|read\s+with)+", re.I),
        "after_act": re.compile(rf"\s*(?:,\s*)?(?:of\s+(?:the\s+)?)?(?:{act_alt})(?![A-Za-z])", re.I),
        "bare_list": re.compile(
            rf"(?<![\w/.\-])(?P<run>{num}(?:\s*(?:,|and|&|/|-|–|to|r\s*/\s*w|r\.\s*w\.|read\s+with)\s*{num})*)\s*,?\s*(?P<act>{act_alt})(?![A-Za-z])", re.I
        ),
        "act_before": re.compile(rf"(?:{act_alt})(?![A-Za-z])\s*,?\s*$", re.I),
        "unsupported_in_run": re.compile(r"-[0-9]|–[0-9]|/|\bto\b|r\s*/\s*w|r\.\s*w\.|read\s+with", re.I),
        "reverse": re.compile(rf"(?P<act>{act_alt})(?![A-Za-z])\s*,?\s*(?:sections?|secs?\.?|s\.)\s*(?P<nums>{numlist})", re.I),
        "unlisted": re.compile(
            r"\s*(?:,\s*)?(?:of\s+(?:the\s+)?)?(?:" + "|".join(_loose(f) for f in sorted(c["unlisted_acts"], key=len, reverse=True)) + r"|(?:[A-Z][\w.&/]*\s+){1,6}Act)(?![A-Za-z])"
        ),
        "bare": re.compile(r"\s*(?:,\s*)?(?:of\s+)?(?:" + "|".join(_loose(f) for f in sorted(c["bare_act_words"], key=len, reverse=True)) + r")(?![A-Za-z])", re.I),
        "court": re.compile(r"(?<![A-Za-z])(?:" + "|".join(_loose(f) for f in sorted(c["court_cues"], key=len, reverse=True)) + r")(?![A-Za-z])", re.I),
        "high_court": re.compile(
            r"(?<![A-Za-z])High\s+Court\s+of\s+(?:Judicature\s+at\s+)?(?:" + "|".join(_loose(f) for f in sorted(c["high_courts"], key=len, reverse=True)) + r")(?![A-Za-z])", re.I
        ),
        "magistrate": re.compile(r"(?<![A-Za-z])(?:" + "|".join(_loose(f) for f in sorted(c["magistrate_titles"], key=len, reverse=True)) + r")(?![A-Za-z])", re.I),
        "station": re.compile(r"(?<![A-Za-z])(?:Police\s+Station|P\.\s?S\.|PS)(?![A-Za-z])", re.I),
        "word": re.compile(r"\S+"),
    }


def _norm(s: str) -> str:
    return " ".join(s.split())


def scan_sections(text: str) -> list[Form]:
    """Every section mention of one passage, in order of appearance. Each number of a list is its own form, read with the Act named after it."""
    r = _rx()
    forms: list[Form] = []
    claimed: list[tuple[int, int]] = []
    gid = [0]

    def nxt() -> int:
        gid[0] += 1
        return gid[0]

    def free(a: int, b: int) -> bool:
        return not any(a < y and x < b for x, y in claimed)

    # the Act first ("IPC, section 420"): claimed before the forward scan so a number is never read twice
    for m in r["reverse"].finditer(text):
        act_id = r["act_forms"].get(_match_form(r, m.group("act")), None)
        base, g = m.start("nums"), nxt()
        for n in r["num"].finditer(m.group("nums")):
            forms.append(Form("section", base + n.start(), base + n.end(), n.group(0), "read", None, act_id, None, g))
        claimed.append((m.start(), m.end()))
    for m in r["section"].finditer(text):
        if not free(m.start(), m.end()):
            continue
        a, b = m.start("nums"), m.end("nums")
        tail = text[b:]
        t = r["unsupported_tail"].match(tail)
        if t is not None:  # a range, a slash or an "r/w": never expanded, printed as it stands
            run = r["unsupported_run"].match(text, a)
            end = run.end() if run else b
            txt = text[m.start() : min(end, m.start() + 200)].rstrip()
            forms.append(Form("section", m.start(), m.start() + len(txt), txt, "not_read", "form_not_supported", None, None, nxt()))
            claimed.append((m.start(), end))
            continue
        nums = [(a + n.start(), a + n.end(), n.group(0)) for n in r["num"].finditer(m.group("nums"))]
        aft = r["after_act"].match(tail)
        if aft is not None:
            act_text = aft.group(0)
            ma = r["act_rx"].search(act_text)
            act_id = r["act_forms"].get(_match_form(r, ma.group("act")), None) if ma else None
            g = nxt()
            for s, e, tx in nums:
                forms.append(Form("section", s, e, tx, "read", None, act_id, None, g))
            claimed.append((m.start(), b + aft.end()))
            continue
        reason = "act_not_on_list" if r["unlisted"].match(tail) is not None else "act_not_named"  # "the Act", "the said Act" and no Act at all are act_not_named
        end = b + (r["bare"].match(tail).end() if r["bare"].match(tail) else (r["unlisted"].match(tail).end() if reason == "act_not_on_list" else 0))
        g = nxt()
        for s, e, tx in nums:
            forms.append(Form("section", s, e, tx, "not_read", reason, None, None, g))
        claimed.append((m.start(), end))
    # a number list straight before the Act, no cue word ("323, 324 and 34 IPC"; "420/34 IPC"; "302 r/w 34 IPC")
    for m in r["bare_list"].finditer(text):
        if not free(m.start(), m.end()) or r["act_before"].search(text[max(0, m.start() - 40) : m.start()]):
            continue  # an Act named just BEFORE the numbers ("BNS 318(4) and 5 BNSS") must not lend the Act that follows
        run = m.group("run")
        if r["unsupported_in_run"].search(run):
            txt = text[m.start() : min(m.end(), m.start() + 200)]
            forms.append(Form("section", m.start(), m.start() + len(txt), txt, "not_read", "form_not_supported", None, None, nxt()))
        else:
            act_id = r["act_forms"].get(_match_form(r, m.group("act")), None)
            g = nxt()
            for n in r["num"].finditer(run):
                forms.append(Form("section", m.start("run") + n.start(), m.start("run") + n.end(), n.group(0), "read", None, act_id, None, g))
        claimed.append((m.start(), m.end()))
    return sorted(forms, key=lambda f: (f.start, f.end))


def _match_form(r: dict[str, Any], matched: str) -> str:
    """The listed form a matched Act string stands for (the matcher is tolerant of spaces and dots, the list is exact)."""
    key = re.sub(r"[\s.]+", "", matched).casefold()
    for form in r["act_forms"]:
        if re.sub(r"[\s.]+", "", form).casefold() == key:
            return str(form)
    return matched


def _word_ends_with_comma(w: str) -> bool:
    return w.endswith(",")


def scan_places(text: str) -> list[Form]:
    """Court and police station mentions of one passage, in order. The magistrate cell shows the title words only. A station name is word for word
    up to a closed terminator (comma, 'dated', 'under') and at most six words; a rank or title word, or no terminator in six words, is NOT READ."""
    r, c = _rx(), cues()
    forms: list[Form] = []
    taken: list[tuple[int, int]] = []

    def add(kind: str, m: re.Match[str]) -> None:
        if any(m.start() < y and x < m.end() for x, y in taken):
            return
        taken.append((m.start(), m.end()))
        forms.append(Form("place", m.start(), m.end(), _norm_keep(text, m.start(), m.end()), "read", None, None, kind, 1_000 + len(forms)))

    for m in r["high_court"].finditer(text):
        add("court", m)
    for m in r["court"].finditer(text):
        add("court", m)
    for m in r["magistrate"].finditer(text):
        add("magistrate", m)
    stops = {w.casefold().rstrip(".") for w in c["station_stop_words"]}
    terms = {t.casefold() for t in c["station_terminators"] if t != ","}
    for m in r["station"].finditer(text):
        if any(m.start() < y and x < m.end() for x, y in taken):
            continue
        words = []
        end = m.end()
        reached = False
        bad = False
        for w in r["word"].finditer(text, m.end()):
            tok = w.group(0)
            core = tok.rstrip(",").casefold()
            if core == "":  # a comma straight after the cue: no name at all
                bad = True
                words.append(w)
                break
            if core.rstrip(".") in terms:
                reached = True
                break
            if core.rstrip(".") in stops or core.rstrip(".") + "." in stops:
                bad = True
                words.append(w)
                break
            words.append(w)
            end = w.end() - (1 if tok.endswith(",") else 0)
            if tok.endswith(","):
                reached = True
                break
            if len(words) >= c["station_max_words"]:
                break
        shown_end = (words[-1].end() if words else m.end())
        if bad or not words or not reached or len(words) > c["station_max_words"]:
            txt = text[m.start() : min(shown_end, m.start() + 200)].rstrip(", ")
            span_end = m.start() + len(txt)
            taken.append((m.start(), span_end))
            forms.append(Form("place", m.start(), span_end, txt, "not_read", "name_not_clear", None, "station", 1_000 + len(forms)))
        else:
            taken.append((m.start(), end))
            forms.append(Form("place", m.start(), end, text[m.start() : end], "read", None, None, "station", 1_000 + len(forms)))
    return sorted(forms, key=lambda f: (f.start, f.end))


def _norm_keep(text: str, a: int, b: int) -> str:
    """The matched substring exactly as it stands (never normalised: the cell must equal the passage slice), capped at 200 characters."""
    return text[a : min(b, a + 200)]


def non_english(text: str) -> bool:
    """A passage whose letters are mostly non-Latin, or that holds one of the closed non-English section cue words."""
    c = cues()
    if any(w in text for w in c["non_english_cue_words"]):
        return True
    latin = sum(1 for ch in text if ch.isalpha() and ord(ch) < 0x250)
    other = sum(1 for ch in text if ch.isalpha() and ord(ch) >= 0x250)
    return other >= 3 and other / max(1, latin + other) > 0.5
