"""Accused-attribution fallback (MVP limit): REFER whenever a second actor or a group marker appears in the act sentence.

M1 (model-picked actor) and the structural veto V3 both failed the pre-registered held-out rule (v3, v4: co-actor passes), so the
shipped fallback is a plain, broad, deterministic refusal. It never proves attribution; it only declines to pass when the sentence
mentions anyone besides the accused under review.

  F0 no accused given            -> `accused_not_specified`
  F1 item level                  -> `item_has_multiple_accused(accused)`: REFER at item level when MORE THAN ONE accused is
                                    listed in the item's parties (applied inside `check_attribution_fallback`, so the judge path
                                    uses it). Only numbered accused labels in `other_parties` can be told apart from the husband
                                    etc.; an unnumbered, named co-accused is not detected here. It would REFER most real cruelty
                                    FIRs (husband plus in-laws): default OFF, re-decide with the retrained 32B.
  F5 multi-sentence quote        -> never passed: a second sentence can carry the co-actor.
  F2 accused not named           -> the quote does not name the accused under review (a pronoun-only sentence, another party's
                                    name): `accused_attribution_unresolved`
  F3 co-actor or group marker    -> any listed other party, numbered/short alias of another party, kin phrase, unlisted name,
                                    plural or group noun, link word, "by <person>" participle or clause-final ellipsis
                                    ("so did Y"): `accused_attribution_collective`
  F4 error                       -> refuses; there is no pass-on-error path.

RESIDUAL RISK, stated plainly: a single listed accused plus an unlisted co-actor written in wording outside these lists can still
pass. Known residuals: role nouns not on the list (the list is blunt but finite), ALL-CAPS names ("RAMU"), lowercase names, names
written in another script, and an unnumbered named co-accused at item level (F1). With F1 on (the production default of this
selector), any item that lists a numbered co-accused is REFERred 100%, whatever the sentence says; the sentence-level refusal rate
measured on a held-out set is therefore only the cost of F2-F5 (SENTENCE-LEVEL only).
COST, stated plainly: a bystander group in the sentence ("... in front of her in-laws", "with the family members gathered round"),
a named victim, a role noun ("a neighbour", "the driver") or a second numbered person who is only a victim also refuse. A refusal never changes the element's
status; the agent turns it into REFER_TO_LAWYER. Default OFF. All examples in the tests are constructed toy sentences.
"""

from __future__ import annotations

import re


from pravrudhi.application.nyaya_attribution import (
    MAX_QUOTE_CHARS,
    REASON_COLLECTIVE,
    REASON_NOT_SPECIFIED,
    REASON_UNRESOLVED,
    AccusedRef,
    AttributionResult,
    _canon,
    extract_candidates,
)
from pravrudhi.application.nyaya_attribution_m1 import _FULL_NAME, _NOT_NAMES, _TITLE_NAME

VARIANT = "FALLBACK"
_ACCUSED_LABEL = re.compile(
    r"(?<![A-Za-z0-9])(?:accused|accd|petitioner|pet|respondent|resp|[AP])\b[\s.\-()]*(?:nos?\b[\s.\-()]*|number\b[\s.\-()]*)?\d+",
    re.I,
)
#: Plural / group nouns and "unnamed others".
_GROUP = re.compile(
    r"\b(?:others?|ors|associates?|relatives?|persons|men|women|people|family\s+members|(?:matrimonial|entire|whole|joint)\s+family|"
    r"members|friends?|unidentified|unknown|unnamed|accomplices?|colleagues?|neighbou?rs|gang|mob|crowd|group|team|in-laws|"
    r"kin|kith|several|few|"
    r"person|someone|somebody|anyone|another|stranger|individual|neighbou?r|driver|servant|maid|lawyer|doctor|priest|landlord|"
    r"tenant|man|woman|boy|girl|lady|gentleman|the\s+other)\b|&\s*ors\b",
    re.I,
)
#: Every link word seen in held-out v1-v4 and the explorer's list, as categories.
_LINK = re.compile(
    r"\b(?:together|along\s*with|alongwith|jointly|severally|as\s+well\s+as|as\s+did|so\s+did|so\s+too|the\s+same|followed\s+suit|"
    r"likewise|in\s+(?:concert|collusion|conspiracy|company|league|cahoots|complicity|collaboration|unison|tandem|connivance)|"
    r"hand[\s-]*in[\s-]*(?:glove|hand)|side\s+by\s+side|common\s+intention|furtherance|"
    r"(?:aided|assisted|abetted|accompanied|joined|helped|supported|backed|propped\s+up|egged\s+on|instigated|encouraged)\s+by|"
    r"(?:coupled|combined|linked)\s+with|"
    r"(?:help|assistance|aid|support|participation|connivance|complicity)\s+of|with\s+the\s+(?:help|assistance|aid|support))\b",
    re.I,
)
#: "with" / "and" / "&" / "by" followed by a person-like mention.
_PERSON_AFTER = re.compile(
    r"\b(?:with|and|&|by|of)\s+(?:(?:Mr|Mrs|Ms|Smt|Shri|Sri|Dr|Kum)\.?\s+[A-Z]|[A-Z][a-z]{2,}|(?i:her|his|their)\s+(?i:[\w-]+\s+)?"
    r"(?i:mother|father|sister|brother|uncle|aunt|nephew|niece|cousin|husband|wife|friend|relative|[\w-]+-in-law)|"
    r"(?i:the\s+(?:maternal|paternal)\s+\w+\s+of)|(?i:accd?\b|accused\b|petitioner\b|pet\b|respondent\b)|(?i:[AP])[\s.\-]*\d)"
)
_KIN_OF = re.compile(
    r"\b(?:the\s+)?(?:maternal|paternal)?\s*(?:uncle|aunt|nephew|niece|cousin|brother|sister|mother|father|husband|wife)\s+of\s+"
    r"(?:the|her|his|their)\b",
    re.I,
)
_KIN = re.compile(
    r"\b(?:her|his|their)\s+(?:[\w-]+\s+)?(?:mother|father|sister|brother|uncle|aunt|nephew|niece|cousin|friend|relative|husband|wife|"
    r"[\w-]+-in-law)\b",
    re.I,
)
_LEADING_FUNCTION_WORDS = {
    "when", "after", "thereafter", "during", "on", "in", "some", "as", "it", "the", "according", "before", "while", "since", "then",
    "later", "once", "if", "because", "although", "she", "he", "her", "his", "their", "thereupon", "subsequently", "about",
}
#: Capitalised tokens that are not a person's name (statute words, courts, places-by-role, units, months, days).
_NOT_PERSON_CAPS = {
    "accused", "accd", "acc", "petitioner", "petitioners", "pet", "respondent", "resp", "complainant", "no", "nos", "number", "pw",
    "section", "sections", "ipc", "bns", "crpc", "bnss", "act", "code", "fir", "court", "high", "supreme", "police", "station",
    "india", "indian", "rs", "dowry", "prohibition", "hon", "ble", "judge", "magistrate", "sessions", "criminal", "procedure",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "i", "a", "p",
}
#: Sentence-initial words that are not names.
_SENTENCE_STARTERS = {
    "the", "a", "an", "on", "in", "at", "after", "before", "when", "while", "thereafter", "during", "she", "he", "they", "it", "her",
    "his", "their", "as", "according", "since", "some", "several", "later", "then", "however", "thereupon", "subsequently", "this",
    "that", "once", "if", "about", "she", "after", "by", "from", "with", "and", "but", "it's", "there", "para", "paragraph", "fir",
    "many", "months", "years", "days", "one", "two", "three", "also", "further", "again", "soon", "finally", "although", "because",
    "so", "to", "for", "of", "per", "not", "no", "all", "any", "each", "every", "whenever", "wherever", "until", "now",
}
_CAP_TOKEN = re.compile(r"(?<![A-Za-z0-9'\u2019])[A-Z][a-z]{2,}(?![A-Za-z0-9])")
_X_KIN = re.compile(
    r"\b(?:the\s+)?(?:husband|wife)['\u2019]s\s+(?:[\w-]+\s+)?(?:mother|father|sister|brother|uncle|aunt|nephew|niece|cousin|friend|relative|"
    r"[\w-]+-in-law)\b",
    re.I,
)
_POSSESSIVE = re.compile(r"^['’]s\b")
#: A sentence end that is not an abbreviation dot ("Pet. No.4", "Mrs. Radhika", "Accd. No.3").
_SENT_END = re.compile(
    r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bSmt)(?<!\bDr)(?<!\bSri)(?<!\bShri)(?<!\bKum)(?<!\bSh)(?<!\bPet)(?<!\bAccd)(?<!\bAcc)"
    r"(?<!\bResp)(?<!\bNo)(?<!\bNos)(?<!\bPW)(?<!\bRs)(?<!\bvs)(?<!\bSr)(?<!\bJr)(?<!\bSt)(?<!\bA)(?<!\bP)[.!?\u0964]\s+(?=\S)"
)
_APPOS_GAP = re.compile(r"^[\s,()]*$")


def item_has_multiple_accused(accused: AccusedRef | None) -> bool:
    """F1: True when at least one OTHER party carries a numbered accused label (Accused No.2, A3, Petitioner No.4, Accd. No.5)."""
    if accused is None:
        return False
    return any(any(_ACCUSED_LABEL.search(a) for a in group) for group in accused.other_parties)


def _own_labels(accused: AccusedRef) -> set[str]:
    return {_canon(a) for a in accused.aliases}


class _Span:
    def __init__(self, start: int, end: int) -> None:
        self.start, self.end = start, end


def _apposed(text: str, c: Any, selfs: list[Any]) -> bool:
    """A kin descriptor of the accused itself ("Accused No.2, her husband," / "Accused No.2 (the husband's aunt)"): only comma,
    bracket or space sits between it and an own-alias mention."""
    for s in selfs:
        gap = text[s.end : c.start] if c.start >= s.end else text[c.end : s.start] if c.end <= s.start else None
        if gap is not None and len(gap) <= 4 and _APPOS_GAP.match(gap) and gap.strip():
            return True
    return False


def _bare_name(sentence: str, taken: list[tuple[int, int]], accused: AccusedRef) -> str | None:
    """Any capitalised token that is not a statute/court/month word, not a sentence-starter and not part of an own-alias mention is
    treated as a possible second person ("while Ramu held her arms", "Ramu too"). Cost: place names and defined terms also refuse."""
    alias_words = {w.lower() for a in accused.aliases for w in re.findall(r"[A-Za-z]+", a)}
    for m in _CAP_TOKEN.finditer(sentence):
        w = m.group(0).lower()
        if any(m.start() < e and s < m.end() for s, e in taken) or w in _NOT_PERSON_CAPS or w in alias_words:
            continue
        at_start = not sentence[: m.start()].strip(" \"'([")
        if at_start and w in _SENTENCE_STARTERS:
            continue
        if w in _SENTENCE_STARTERS and sentence[: m.start()].rstrip().endswith((",", ";", ":")):
            continue
        if w in _SENTENCE_STARTERS:
            continue
        return f"bare_name:{m.group(0)}"
    return None


def check_attribution_fallback(quote: str | None, accused: AccusedRef | None, *, item_level: bool = True) -> AttributionResult:
    """The fallback decision on ONE quoted fact. Never raises: any error is a refusal."""
    if accused is None:
        return AttributionResult(False, REASON_NOT_SPECIFIED, variant=VARIANT, rule="F0")
    try:
        if item_level and item_has_multiple_accused(accused):
            return AttributionResult(False, REASON_COLLECTIVE, variant=VARIANT, rule="F1", actor_span="more than one accused listed")
        if not isinstance(quote, str) or not quote.strip() or len(quote) > MAX_QUOTE_CHARS:
            return AttributionResult(False, REASON_UNRESOLVED, variant=VARIANT, rule="F4", error="empty or oversized quote")
        cands = extract_candidates(quote, accused)
        selfs = [c for c in cands if c.role == "self"]
        if not selfs:
            return AttributionResult(False, REASON_UNRESOLVED, variant=VARIANT, rule="F2")
        first = selfs[0]
        # F5: a multi-sentence quote is never passed (the whole quote is the act text; a second sentence can carry the co-actor)
        if any(part.strip() for part in _SENT_END.split(quote.strip())[1:]):
            return AttributionResult(False, REASON_COLLECTIVE, variant=VARIANT, rule="F5", actor_span="multi-sentence quote")
        lo = 0
        sentence = quote
        off = 0
        taken = [(c.start - off, c.end - off) for c in selfs]

        def outside_self(a: int, b: int) -> bool:
            return not any(a < e and s < b for s, e in taken)

        own = _own_labels(accused)
        hit: str | None = None
        for c in cands:
            if c.role == "self" or c.start < lo or c.end > lo + len(sentence):
                continue
            if c.role == "pronoun":
                continue
            if _POSSESSIVE.match(quote[c.end : c.end + 3]) or _apposed(quote, c, selfs):
                continue
            hit = f"candidate:{c.role}:{c.text}"
            break
        if hit is None:
            for name, rx in (("group", _GROUP), ("link", _LINK), ("kin_of", _KIN_OF), ("kin", _KIN)):
                for m in rx.finditer(sentence):
                    if (
                        outside_self(m.start(), m.end())
                        and not _POSSESSIVE.match(sentence[m.end() : m.end() + 3])
                        and not (name.startswith("kin") and _apposed(sentence, _Span(m.start(), m.end()), [_Span(a, b) for a, b in taken]))
                    ):
                        hit = f"{name}:{m.group(0)}"
                        break
                if hit:
                    break
        if hit is None:
            for m in _ACCUSED_LABEL.finditer(sentence):
                if outside_self(m.start(), m.end()) and _canon(m.group(0)) not in own:
                    hit = f"label:{m.group(0)}"
                    break
        if hit is None:
            for m in _PERSON_AFTER.finditer(sentence):
                tail = sentence[m.end() - 1 : m.end() + 12]
                word = re.sub(r"[^A-Za-z]", "", sentence[m.start() : m.end()].split()[-1]).lower()
                if outside_self(m.start(), m.end()) and word not in _NOT_NAMES and not re.match(r"^[A-Z][a-z]+\s+(?:No|Nos)\b", tail):
                    hit = f"person_after:{m.group(0)}"
                    break
        if hit is None:
            for m in _X_KIN.finditer(sentence):
                if outside_self(m.start(), m.end()):
                    hit = f"x_kin:{m.group(0)}"
                    break
        if hit is None:
            hit = _bare_name(sentence, taken, accused)
        if hit is None:
            for pat in (_TITLE_NAME, _FULL_NAME):
                for m in pat.finditer(sentence):
                    words = [w.lower().strip(".") for w in m.group(0).split()]
                    if (
                        outside_self(m.start(), m.end())
                        and not any(w in _NOT_NAMES for w in words)
                        and words[0] not in _LEADING_FUNCTION_WORDS
                    ):
                        hit = f"name:{m.group(0)}"
                        break
                if hit:
                    break
        if hit is not None:
            return AttributionResult(False, REASON_COLLECTIVE, variant=VARIANT, rule="F3", actor_span=hit[:120])
        return AttributionResult(True, None, variant=VARIANT, rule=None, actor_span=first.text)
    except Exception as e:  # noqa: BLE001 -- fail closed
        return AttributionResult(False, REASON_UNRESOLVED, variant=VARIANT, rule="F4", error=f"{type(e).__name__}: {e}"[:300])


class FallbackChecker:
    """An `AttributionChecker` (see nyaya_attribution): the fallback decision, F1 included."""

    name = "fallback"

    def check(self, quote: str | None, accused: AccusedRef | None) -> AttributionResult:
        return check_attribution_fallback(quote, accused)


