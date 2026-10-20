# ruff: noqa: E501
"""A4 bail drafting: ONE draft of the GROUNDS section of a bail application, from the lawyer's own applicant-side facts.

What the engine writes (no model): the heading block with the statute-framing lines it was handed, the numbered facts, the fill-ins
list, the notes box, the prayer placeholder and the authorities the lawyer supplied. What the model writes: the grounds paragraphs and
the notes, as JSON, in ONE tool-less call (thinking on; the call is made by the caller's Completer, this module passes no thinking
setting) and at most ONE regeneration of the paragraphs that failed a mechanical check. The engine never edits the model's words: a
paragraph that fails twice is dropped to the notes, and when no paragraph survives, or no Completer was given, the output is the
template with the facts and the notes only.

Inputs are decided before this module is called (the lawyer's applicant-side tick, the declared kind and dates, the consent, the
sensitive-tag referral and the statute framing by date are the route's job: bail_draft_input.py). This module is duck-typed on:
  case:    matter_id, kind ("regular" | "anticipatory"), facts (each .fact_id, .text; applicant side only), n_facts_excluded,
           authorities (list[str]), parties (dict, optional)
  framing: status ("single" | "straddle"), lines (list[str], engine-worded, printed verbatim)
  cfg:     model, usd_per_mtok_in, usd_per_mtok_out, max_tokens_per_call, max_calls_per_matter, max_input_tokens_per_call, usd_per_matter
Cost is the counted usage of the calls times the cfg price: never an estimate. The pre-call size check is an estimate used only to REFUSE.
Limits, said once: the checks are mechanical (they do not test legal correctness, nor that a paragraph follows from its facts); a draft
that passes them is not thereby free of unsupported content; no lawyer has read these drafts as a quality measure.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pravrudhi.application.citations import Citation, parse_citations

_log = logging.getLogger(__name__)
VERSION = "bail-draft-1"
BANNER = "Draft for lawyer review."
GROUNDS_HEADING = "GROUNDS"
NOTES_HEADING = "Notes for the lawyer"
FILL_INS_HEADING = "Details to fill in"
KIND_TITLES = {"regular": "BAIL APPLICATION", "anticipatory": "APPLICATION FOR ANTICIPATORY BAIL"}
KIND_WORDS = {"regular": "a regular bail application", "anticipatory": "an application for anticipatory bail"}
SENSITIVE_REFUSAL = (
    "No draft is made for this kind of case (a case under the POCSO Act or involving a minor, a sexual offence, trafficking, or one "
    "you marked sensitive). A lawyer drafts the application."
)  # R1's wording of 20 Oct (adds "trafficking"); the route may override it from its own config
NOTE_LEFT_OUT = "A note from the draft was left out because it predicted the result of this application."
#: R1: barred everywhere, notes included: predictions about THIS application's result (what the other side may argue is allowed in notes).
PREDICTIONS = (
    "likely to be granted",
    "likely to be refused",
    "will be granted",
    "will be refused",
    "ready to file",
    "strong case",
    "must be granted",
    "should be granted",
    "certain to",
)
#: R1 reviews this CLOSED list: phrases removed from a sentence before the support test (they carry no fact of their own).
BOILERPLATE = (
    "the applicable bail provision",
    "the applicant states that",
    "the applicant submits that",
    "it is submitted that",
    "the applicant",
)
SUPPORT_MIN = 0.5  # a sentence is supported when at least this share of its content words are in the facts it cites
STOP_WORDS = frozenset(
    [
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "were",
        "will",
        "would",
        "could",
        "should",
        "their",
        "there",
        "which",
        "about",
        "after",
        "before",
        "under",
        "upon",
        "into",
        "than",
        "then",
        "they",
        "them",
        "also",
        "only",
        "such",
        "some",
        "each",
        "other",
        "these",
        "those",
        "being",
        "while",
        "where",
        "when",
        "whom",
        "whose",
    ]
)
#: A model note that says the bail provision is not identified contradicts the heading, which the engine writes with the Act and section.
NOTE_PROVISION_RE = re.compile(
    r"(?i)provision\b.*\b(not|insert|identify|confirm|state|specify|add)\b|\b(not|insert|identify|confirm|state|specify|add)\b.*\bbail provision"
)
#: Plain words for each failure code, used in the note that says which ground was not used.
REASON_WORDS = {
    "empty": "it had no text", "fact_ref_unknown": "it cited a fact number that does not exist", "no_fact": "it cited no fact",
    "d1_token_invented": "it used a placeholder that none of your facts contains", "d2_meta": "it talked about what was or was not given instead of stating a fact",
    "conclusory": "it used outcome or strength wording that is not in your facts", "forbidden_words": "it used outcome or strength wording",
    "blanket_opener": "it opened with 'It is submitted' although only the first paragraph may", "new_noun": "it named something that is in none of your facts",
    "number_not_in_facts": "it used a number, date or amount that is not written like that in your facts", "authority_not_supplied": "it cited an authority you did not supply",
    "unsupported_sentence": "it had a sentence that none of your facts supports", "role_flip": "it named the applicant as another party", "tool_voice": "it spoke as the tool and not as a party", "repeated_subject": "it repeated the subject ('The Applicant states that the Applicant')",
}  # fmt: skip
#: Section mentions in a fact ("Sections 370, 370-A and 34 of the Indian Penal Code"): the list after the word Section(s), then up to 80 characters naming the Act.
SECTION_MENTION_RE = re.compile(
    r"\b(?:sections?|secs?\.?|ss?\.)\s*(?P<nums>\d{1,3}(?:-?[A-Z]{1,2}(?![a-z]))?(?:\s*(?:,|/|&|and|or|to)\s*\d{1,3}(?:-?[A-Z]{1,2}(?![a-z]))?)*)(?P<tail>[^.;\n]{0,80})",
    re.I,
)
SECTION_NUM_RE = re.compile(r"\d{1,3}(?:-?[A-Z]{1,2}(?![a-z]))?")
SENSITIVE_ACT_WORDS = re.compile(
    r"(?<![a-z])(ipc|indian penal code|bns|bharatiya nyaya sanhita|pocso|protection of children from sexual offences)(?![a-z])",
    re.I,
)
OTHER_ACT = re.compile(r"\b(?:[A-Z][A-Za-z]*\s+){0,6}(?:Act|Code|Sanhita|Rules|Regulation)\b")
SENSITIVE_SECTION_NOTE = "a ticked fact names a section on the sensitive list"
MORE_NOTES_HEADING = "More notes"
NOTES_SHOWN = 5  # the notes a lawyer sees first; the rest are folded under MORE_NOTES_HEADING
ADVERSE_NOTE = "Fact {fid} reads like the court's, the prosecution's or the complainant's voice, or an adverse fact ({why}). It was used as ticked: check that it belongs in the applicant's grounds."
INCOMPLETE_LINE = "INCOMPLETE: the model's answer was cut off at the length limit. No grounds were taken from it."
TEMPLATE_ONLY_LINE = "The draft grounds could not be made, so this shows your facts and the notes only."  # R1's a4_template_only (59673ff74f88cd62)
PRAYER = "PRAYER\nTo be completed by counsel. This draft gives grounds only; no relief is drafted."

SYSTEM = (
    "You write the GROUNDS of a draft bail application for a lawyer to rewrite. Answer with ONE JSON object and nothing else: "
    '{"grounds": [{"text": "...", "facts": ["F1", "F2"]}], "notes_for_lawyer": ["..."]}. '
    'Use ONLY the numbered facts and the authorities listed in the message. Each paragraph names in "facts" the fact ids it rests on '
    "and carries the same ids in its text as [F1], [F2]. "
    "Copy any square-bracket placeholder that a fact contains (for example [name] or [FIR number]) exactly as written wherever you "
    "mention that thing; never replace it with a guessed word. "
    "State no fact, place, institution, person, date or amount that is not in the facts, and add no contention or inference beyond "
    "them: use the facts' own words (do not turn 'sufficient time' into 'considerable time'). "
    "Do not say which Act or section governs the application: write 'the applicable bail provision'. "
    "Name no authority that is not listed. "
    "Write in the applicant's voice as plain statements of what the applicant states; do not open every paragraph with 'It is "
    "submitted'. Do not predict the outcome and do not write 'entitled', 'fit case' or 'should be granted'. "
    "In the paragraphs write nothing about what is missing or not given; put every gap or question for the lawyer in notes_for_lawyer. "
    "Do not repeat the subject: write 'The Applicant states that he has been in custody', never 'The Applicant states that the Applicant ...'. "
    "Every sentence restates a fact: add no sentence that introduces, links or summarises the facts (for example that the applicant has approached this Court). "
    "The system writes the heading with the Act and section and the prayer: write no note about which Act or bail provision applies. "
    "No headings, no titles, no placeholders of your own."
)

#: Words that may not appear in a grounds paragraph (outcome and strength language), case-insensitive.
FORBIDDEN = (
    "fake",
    "fabricated",
    "hallucinat",
    "invalid",
    "bogus",
    "guarantee",
    "certain to",
    "will be granted",
    "must be granted",
)
#: D2: the model talking to the lawyer inside the body; allowed only in notes_for_lawyer.
META_PHRASES = (
    "not supplied",
    "no authority is supplied",
    "no authority has been supplied",
    "none is supplied",
    "this draft",
    "the facts supplied",
    "the facts provided",
    "the facts given",
    "facts supplied",
    "supplied above",
    "as supplied",
    "not provided",
    "not given",
    "the lawyer",
    "for the lawyer",
    "supplied",
)
#: Conclusory prayers and outcome claims inside the grounds.
CONCLUSORY = (
    "entitled",
    "fit case",
    "should be granted",
    "ought to be granted",
    "deserves bail",
    "is a fit",
    "likely",
    "ready to file",
    "clearly",
    "no doubt",
    "bound to",
    "certainly",
    "made out",
)  # R1's barred list for the body
#: New-noun check (closed list): things a model tends to add that a bail ground would not carry unless the facts do. A paragraph that
#: uses one of these words fails when the word is in none of the facts. Substring match on whole words.
INVENTED_NOUNS = (
    "bank",
    "atm",
    "facility",
    "pandemic",
    "hospital",
    "employer",
    "mortgage",
    "loan",
    "passport",
    "surety",
    "sureties",
    "bond",
    "complainant",
    "informant",
    "victim",
    "prosecutrix",
    "witnesses",
    "family",
    "wife",
    "husband",
    "children",
    "mother",
    "father",
    "business",
    "employment",
    "residence",
    "address",
    "aadhaar",
    "medical",
    "illness",
    "elderly",
    "sole breadwinner",
)
#: Fail-closed backstop on the fact text only (the lawyer's tag is the first line of defence and is the route's job).
_NUM_WORDS = {w: i for i, w in enumerate(["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen"], 1)}
_WORDS_ALT = "|".join(_NUM_WORDS)
_SENSITIVE_WORDS = re.compile(
    r"\b(rape[sd]?|gang[- ]?rape\w*|pocso|protection of children from sexual|"
    r"sexual(?:ly)? (?:assault\w*|abuse\w*|exploit\w*|offence\w*|offense\w*|harass\w*|intercourse)|prosecutrix|"
    r"minor (?:victim|girl|boy)|victim (?:is|was) a minor|(?:a|the) minor(?! (?:injur\w+|stake|share|damage|error|issue|dispute|detail|child(?:ren)?|son|daughter|dependant\w*))|"
    r"juvenile|child (?:of (?:\d{1,2}|" + _WORDS_ALT + r")|abuse\w*|labou?r|victim|pornograph\w+)|traffick\w+|prostitut\w+|bonded labou?r|sold the (?:girl|boy|child)|obscene|unnatural|non[- ]?consensual|"
    r"forced himself|kidnap\w*|lawful guardian\w*|molest\w*|outrag\w+ (?:the )?modesty|"
    r"(?:girl|boy)s?\b.{0,24}\b(?:aged|age of|below|under)|(?:below|under|less than) (?:the age of )?(?:18|eighteen)(?: years)?|"
    r"(?:complainant|victim|witness|informant|accused|co-accused)(?:'s|\u2019s|s')?\s+(?:minor|young|juvenile|\w+[- ]year[- ]old)\s+"
    r"(?:daughter|son|child|girl|boy|niece|nephew|sister|brother)|"
    r"indecent|brothel|sex[- ]workers?|bought (?:a|the) (?:girl|boy|child)|school(?:girl|boy)s?|infants?|acid (?:attack|was thrown|thrown)|"
    r"child(?:ren)?\b.{0,24}\b(?:beaten|abused|assaulted|harmed|hurt|killed|injured|molested))\b",
    re.I,
)
#: "15 year old", "eleven-year-old": an age under 18 (not "12-year-old son": the applicant's own child, Track B's near-miss).
_YOUNG_AGE = re.compile(
    rf"\b(?:(?:aged?|age of)\s+(?:about\s+|around\s+)?(\d{{1,2}}|{_WORDS_ALT})\b|(\d{{1,2}}|{_WORDS_ALT})(?:[- ]years?[- ]old|\s+years?\s+of\s+age)\b"
    r"(?!\s+(?:son|daughter|child|children)\b)|"
    rf"\b(?:girl|boy|child|she|he)\s+(?:was|is|of|aged)\s+(?:only\s+|about\s+|around\s+)?(\d{{1,2}}|{_WORDS_ALT})\b"
    r"(?!\s*(?:days?|months?|hours?|times?|percent|%|lakh|thousand|crore)))",
    re.I,
)


class _SensitiveWords:
    """The fail-closed backstop on the fact wording: `.search(text)` returns a match (with `.group(0)`) or None. An uncertain wording refers."""

    @staticmethod
    def search(text: str) -> re.Match[str] | None:
        if (m := _SENSITIVE_WORDS.search(text)) is not None:
            return m
        for m in _YOUNG_AGE.finditer(text):
            tok = (m.group(1) or m.group(2) or m.group(3)).lower()
            if (int(tok) if tok.isdigit() else _NUM_WORDS.get(tok, 99)) < 18:
                return m
        return None


SENSITIVE_RE = _SensitiveWords()
#: Facts that read like the other side's voice or an adverse fact. ADVISORY only: the lawyer ticked the fact, so it is never excluded; it is named in a note and in an info check.
ADVERSE_RE = re.compile(
    r"\b(public prosecutor|state counsel|learned counsel for the (?:state|complainant)|the prosecution (?:has |had )?(?:opposed|states|submits|alleges|contends)|prosecution'?s (?:case|version|contention)|the court (?:has )?observed|"
    r"observed that|opposed|opposes|opposing the bail|complainant (?:states|alleges|submits)|alleged by the complainant|convicted|"
    r"conviction|habitual offender|history[- ]sheeter|proclaimed offender|absconder)\b",
    re.I,
)
#: Tokens the engine reads as numbers, dates and amounts: runs of digits with the separators dates and amounts use.
NUMBER_RE = re.compile(r"\d[\d,./-]*\d|\d")
FACT_MARK = re.compile(r"\[(F[0-9]{1,4})\]")
BRACKET = re.compile(r"\[(?!F[0-9]{1,4}\])[^\[\]\n]{1,60}\]")
WORD = re.compile(r"[a-z][a-z'-]*")
#: Advisory only (never fails a paragraph): drafting words that are not facts.
DRAFTING_WORDS = frozenset(
    [
        "applicant",
        "applicants",
        "state",
        "grounds",
        "ground",
        "applicable",
        "provision",
        "provisions",
        "court",
        "bail",
        "custody",
        "application",
        "accused",
        "arrested",
        "alleged",
        "allegation",
        "allegations",
        "offence",
        "offences",
        "registered",
        "investigation",
        "chargesheet",
        "charge",
        "release",
        "conditions",
        "period",
        "remains",
        "stated",
        "states",
        "record",
        "according",
        "against",
        "already",
        "further",
        "therefore",
        "thereby",
        "cooperate",
        "cooperation",
        "trial",
        "case",
        "matter",
        "order",
        "regarding",
        "hearing",
        "nature",
        "days",
        "months",
        "years",
        "since",
        "after",
        "before",
        "during",
        "whether",
        "without",
        "which",
        "would",
        "could",
        "should",
        "shall",
        "continued",
        "detention",
        "incarceration",
        "remand",
        "anticipatory",
        "arrest",
        "apprehension",
        "apprehends",
    ]
)


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    stop_reason: str = ""  # "max_tokens" marks a cut-off answer; "" when the caller does not record it


Completer = Callable[[str, str, int], Completion]


def cost_usd(tokens_in: int, tokens_out: int, cfg: Any) -> float:
    """Counted tokens times the config's pinned price (per million tokens)."""
    return float(round(tokens_in * cfg.usd_per_mtok_in / 1_000_000 + tokens_out * cfg.usd_per_mtok_out / 1_000_000, 6))


def estimate_tokens(text: str) -> int:
    """A deliberately high size ESTIMATE (3 characters a token), used only to refuse an over-size pack before any call."""
    return -(-len(text) // 3)


def build_pack(case: Any) -> str:
    """The ONE compact user message: the applicant-side facts (placeholders kept) and the authorities the lawyer supplied."""
    lines = [f"Matter {case.matter_id}. Write the grounds for {KIND_WORDS[case.kind]}.", "", "FACTS:"]
    lines += [f"[{f.fact_id}] {f.text}" for f in case.facts]
    lines += ["", "AUTHORITIES YOU MAY CITE (and no others):"]
    lines += [f"- {a}" for a in case.authorities] or ["- none: cite no authority"]
    return "\n".join(lines)


def _key(c: Citation) -> tuple[str, int | None, int | None, int]:
    return (c.reporter, c.year, c.volume, c.page)


def _words(text: str) -> set[str]:
    return set(WORD.findall(text.lower()))


def _has_phrase(low: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", low) is not None


def parse_answer(text: str) -> tuple[list[dict[str, Any]], list[str]] | None:
    """The model's JSON, or None when it is not the asked shape. A code fence around it is tolerated; nothing else is repaired."""
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body).strip()
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("grounds"), list):
        return None
    grounds: list[dict[str, Any]] = []
    for g in obj["grounds"]:
        if not (isinstance(g, dict) and isinstance(g.get("text"), str) and isinstance(g.get("facts"), list)):
            return None
        if not all(isinstance(x, str) for x in g["facts"]):
            return None
        grounds.append({"text": g["text"].strip(), "facts": list(g["facts"])})
    notes = obj.get("notes_for_lawyer", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        return None
    return grounds, [n.strip() for n in notes if n.strip()]


def section_strings(text: str) -> list[str]:
    """One string per section number a fact mentions, with the words after the list that name the Act ("Section 370 of the Indian Penal Code").
    A mention whose tail names another Act (NDPS, Excise, Negotiable Instruments...) and none of IPC/BNS/POCSO is skipped; one that names no Act is kept (the table reads it against both lists)."""
    out: list[str] = []
    for m in SECTION_MENTION_RE.finditer(text):
        tail = re.sub(
            r"^(?:\s*\([0-9A-Za-z]+\))+", "", m.group("tail")
        )  # sub-clauses such as (2)(n) are not part of the Act words
        if not SENSITIVE_ACT_WORDS.search(tail) and OTHER_ACT.search(tail):
            continue
        out += [f"Section {n} {tail.strip()}".strip() for n in SECTION_NUM_RE.findall(m.group("nums"))]
    return out


def _sensitive_checker() -> Callable[[list[str]], bool] | None:
    """The sensitive-section table lives with the route's input layer (Track C, bail_draft_input.sensitive_section). Imported lazily: absent => no section backstop."""
    import importlib

    try:
        return importlib.import_module("pravrudhi.application.bail_draft_input").sensitive_section  # type: ignore[no-any-return]
    except (ImportError, AttributeError):
        return None


def sensitive_section_check(mentions: list[str], who: str) -> dict[str, Any] | None:
    """Fail closed. None = go on; otherwise the FAILING check the caller records before it refuses with SENSITIVE_REFUSAL.
    A section was named: no table to read, or a table that raises, is a refusal exactly like a hit (a lawyer drafts those)."""
    if not mentions:
        return None
    checker = _sensitive_checker()
    if checker is None:
        _log.error("%s: the sensitive-section table is not available; a draft that names a section is referred to a lawyer", who)
        return {
            "name": "sensitive_section_table_missing",
            "passed": False,
            "detail": "the sensitive-section table could not be read, so a draft that names a section is referred to a lawyer",
        }
    try:
        hit = bool(checker(mentions))
    except Exception:  # noqa: BLE001 - any failure of the check is a refusal, never a pass
        _log.exception("%s: the sensitive-section check failed; the draft is referred to a lawyer", who)
        return {
            "name": "sensitive_section_check_error",
            "passed": False,
            "detail": "the sensitive-section check failed, so the draft is referred to a lawyer",
        }
    return {"name": "sensitive_section_backstop", "passed": False, "detail": SENSITIVE_SECTION_NOTE} if hit else None


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def _copied_from_facts(low: str, phrase: str, facts_norm: str) -> bool:
    """True when every occurrence of `phrase` in the paragraph sits in a stretch of words (3 before, 3 after) that is written the same in the facts."""
    words = low.split()
    first = phrase.split()[0]
    hits = [i for i, w in enumerate(words) if re.sub(r"[^a-z0-9']", "", w) == first]
    if not hits:
        return False
    span = len(phrase.split())
    return all(_norm(" ".join(words[max(0, i - 3) : i + span + 3])) in facts_norm for i in hits)


def _sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z\[])", FACT_MARK.sub(" ", text).strip()) if x.strip()]


def _unsupported_sentences(text: str, facts_text: str) -> list[str]:
    """Sentences whose content words are mostly not in the facts they cite: the model's own linking or introducing sentences."""
    fw = _words(facts_text)
    bad: list[str] = []
    for sent in _sentences(text):
        low = BRACKET.sub(" ", sent.lower())
        for b in sorted(BOILERPLATE, key=len, reverse=True):
            low = low.replace(b, " ")
        toks = [t for t in WORD.findall(low) if len(t) >= 4 and t not in STOP_WORDS]
        if toks and sum(1 for t in toks if t in fw) / len(toks) < SUPPORT_MIN:
            bad.append(sent.strip())
    return bad


def check_paragraph(case: Any, para: dict[str, Any], index: int) -> list[str]:
    """Mechanical reasons a grounds paragraph fails (empty list = passes). Each reason is a stable code plus a short detail."""
    text, low = para["text"], para["text"].lower()
    facts = {f.fact_id: f for f in case.facts}
    why: list[str] = []
    if not text:
        return ["empty: the paragraph has no text"]
    cited = list(dict.fromkeys([*para["facts"], *FACT_MARK.findall(text)]))
    unknown = [c for c in cited if c not in facts]
    if unknown:
        why.append(f"fact_ref_unknown: {', '.join(unknown)} is not one of the numbered facts")
    if not [c for c in cited if c in facts]:
        why.append("no_fact: the paragraph cites no numbered fact")
    corpus_all = " ".join(f.text for f in case.facts)
    # D1: placeholders kept verbatim, none invented
    tokens_in_text = set(BRACKET.findall(text))
    allowed = set(BRACKET.findall(corpus_all))
    bad_tokens = sorted(tokens_in_text - allowed)
    if bad_tokens:
        why.append(f"d1_token_invented: {', '.join(bad_tokens)} is a placeholder that no fact contains")
    # D2: meta talk and conclusory language
    meta = [p for p in META_PHRASES if _has_phrase(low, p)]
    if meta:
        why.append(f"d2_meta: the body talks about what was or was not given ({', '.join(meta[:3])}); gaps belong in the notes")
    facts_norm = _norm(corpus_all)
    concl = [
        p for p in CONCLUSORY if _has_phrase(low, p) and not _copied_from_facts(low, p, facts_norm)
    ]  # the lawyer's own words are not the model's claim
    if concl:
        why.append(f"conclusory: {', '.join(concl[:3])}")
    forb = [p for p in FORBIDDEN if p in low and not _copied_from_facts(low, p, facts_norm)]
    if forb:
        why.append(f"forbidden_words: {', '.join(forb[:3])}")
    if index > 0 and re.match(r"\s*it is submitted\b", low):
        why.append("blanket_opener: only the first paragraph may open with 'It is submitted'")
    if re.search(
        r"\bthe applicant (is|was|being) (also )?(the )?(complainant|victim|informant)\b|\bthe (complainant|victim|informant) (is|was) (also )?the applicant\b",
        low,
    ):
        why.append("role_flip: the applicant is named as another party")
    if re.search(r"\b(the applicant|he|she) (states|submits) that the applicant\b", low):
        why.append("repeated_subject: 'The Applicant states that the Applicant'")
    cited_text = " ".join(facts[c].text for c in cited if c in facts)
    unsupported = _unsupported_sentences(text, cited_text or corpus_all)
    if unsupported:
        why.append(f"unsupported_sentence: {unsupported[0][:80]!r} is supported by none of the facts it cites")
    # new-noun check against the facts (all of them: a noun in any fact is the lawyer's own)
    facts_words = _words(corpus_all)
    invented = [
        n for n in INVENTED_NOUNS if _has_phrase(low, n) and not _has_phrase(corpus_all.lower(), n) and n not in facts_words
    ]
    if invented:
        why.append(f"new_noun: {', '.join(invented[:4])} is in no fact")
    # every number, date and amount must be in the facts AS WRITTEN (a reformatted date or a computed figure is a blurred fact)
    bare = BRACKET.sub(" ", FACT_MARK.sub(" ", text))
    num_corpus = (
        corpus_all + " " + " ".join(case.authorities)
    )  # the lawyer's supplied authorities are as much his own words as the facts
    facts_nocomma = num_corpus.replace(",", "")
    loose = [
        t
        for t in dict.fromkeys(x.rstrip(".,/-") for x in NUMBER_RE.findall(bare))
        if t not in num_corpus and t.replace(",", "") not in facts_nocomma
    ]
    if loose:
        why.append(f"number_not_in_facts: {', '.join(loose[:5])} is not written like that in any fact")
    # authorities only from the supplied list
    supplied = {_key(x) for a in case.authorities for x in parse_citations(a)}
    unresolved = [text[x.span[0] : x.span[1]] for x in parse_citations(text) if _key(x) not in supplied]
    if unresolved:
        why.append(f"authority_not_supplied: {', '.join(dict.fromkeys(unresolved))}")
    return why


def _dropped_note(case: Any, para: dict[str, Any], index: int, reasons: list[str]) -> str:
    """Says WHICH ground was lost, in the lawyer's words (the facts it rested on), and why in plain words."""
    by_id = {f.fact_id: f.text for f in case.facts}
    ids = [c for c in dict.fromkeys([*para["facts"], *FACT_MARK.findall(para["text"])]) if c in by_id]
    label = (
        "; ".join(f"{c}: " + by_id[c].strip()[:70] + ("..." if len(by_id[c].strip()) > 70 else "") for c in ids[:2])
        or "no fact cited"
    )
    plain = "; ".join(dict.fromkeys(REASON_WORDS.get(r.split(":")[0], r.split(":")[0]) for r in reasons))
    return f"A ground was not used (paragraph {index + 1}, from {label}): {plain}. Add it yourself if you want it."


def _fill_ins(case: Any, kept: list[dict[str, Any]]) -> list[str]:
    """D1 as a list: a placeholder in a fact that no kept paragraph citing that fact repeats goes to the lawyer to fill in."""
    out: list[str] = []
    for f in case.facts:
        for tok in dict.fromkeys(BRACKET.findall(f.text)):
            repeated = any(f.fact_id in {*p["facts"], *FACT_MARK.findall(p["text"])} and tok in p["text"] for p in kept)
            if not repeated:
                out.append(f"Fact {f.fact_id} contains {tok}; no paragraph repeats it: fill it in where it belongs.")
    return out


NOTE_KEYS = (
    "contradict",
    "conflict",
    "different",
    "differ",
    "inconsisten",
    "disclos",
    "adverse",
    "convict",
    "opposed",
    "earlier",
)


def _adverse_flags(case: Any) -> list[tuple[str, str]]:
    """Facts that read like the other side's voice or an adverse fact: (fact id, the words matched). Advisory; nothing is excluded."""
    out: list[tuple[str, str]] = []
    for f in case.facts:
        hits = list(dict.fromkeys(m.group(0).lower() for m in ADVERSE_RE.finditer(f.text)))
        if hits:
            out.append((f.fact_id, ", ".join(hits[:3])))
    return out


def _advisory_words(case: Any, kept: list[dict[str, Any]]) -> list[str]:
    facts_words = _words(" ".join(f.text for f in case.facts))
    seen: dict[str, None] = {}
    for p in kept:
        for w in WORD.findall(p["text"].lower()):
            if len(w) >= 7 and w not in facts_words and w not in DRAFTING_WORDS:
                seen[w] = None
    return list(seen)[:10]


def _gaps(case: Any, kept: list[dict[str, Any]]) -> list[str]:
    cited = {c for p in kept for c in [*p["facts"], *FACT_MARK.findall(p["text"])]}
    return [f"Fact {f.fact_id} was given but the grounds do not cite it." for f in case.facts if f.fact_id not in cited]


def _feedback(failed: list[tuple[int, list[str]]]) -> str:
    lines = [
        "",
        "REVISION: rewrite the whole answer. These paragraphs of your last answer failed checks; fix them, keep the rest:",
    ]
    lines += [f"- paragraph {i + 1}: {'; '.join(r)}" for i, r in failed]
    return "\n".join(lines)


def _sections(
    case: Any,
    framing: Any,
    grounds: list[dict[str, Any]],
    fill_ins: list[str],
    notes: list[str],
    tail: str | None,
    more_notes: list[str],
) -> list[dict[str, Any]]:
    title = KIND_TITLES[case.kind]
    parties = (
        " v. ".join(v for v in ((case.parties or {}).get("applicant"), (case.parties or {}).get("state")) if v) or "[parties]"
    )
    head = "\n".join([BANNER, title, "[Court]", parties, *list(framing.lines)])
    facts = "FACTS\n" + "\n".join(f"[{f.fact_id}] {f.text}" for f in case.facts)
    body = "\n\n".join(p["text"] for p in grounds) if grounds else (tail or TEMPLATE_ONLY_LINE)
    out: list[dict[str, Any]] = [
        {"id": "heading", "source": "engine", "text": head},
        {"id": "facts", "source": "engine", "text": facts},
        {"id": "grounds", "source": "model" if grounds else "engine", "text": f"{GROUNDS_HEADING}\n{body}"},
    ]
    if fill_ins:
        out.append(
            {"id": "fill_ins", "source": "engine", "text": FILL_INS_HEADING + "\n" + "\n".join(f"- {x}" for x in fill_ins)}
        )
    if notes:
        out.append({"id": "notes", "source": "model", "text": NOTES_HEADING + "\n" + "\n".join(f"- {n}" for n in notes)})
    if more_notes:
        out.append(
            {"id": "more_notes", "source": "model", "text": MORE_NOTES_HEADING + "\n" + "\n".join(f"- {n}" for n in more_notes)}
        )
    out.append({"id": "prayer", "source": "engine", "text": PRAYER})
    out.append(
        {
            "id": "authorities",
            "source": "engine",
            "text": "AUTHORITIES\n" + ("\n".join(f"- {a}" for a in case.authorities) or "None supplied."),
        }
    )
    return out


def _result(
    case: Any,
    framing: Any,
    run_id: str,
    *,
    state: str,
    grounds: list[dict[str, Any]],
    notes: list[str],
    fill_ins: list[str],
    checks: list[dict[str, Any]],
    more_notes: list[str] | None = None,
    calls: int,
    tin: int,
    tout: int,
    cost: float,
    regenerated: bool,
    incomplete: bool,
    tail: str | None = None,
    refused: str | None = None,
    unresolved: list[str] | None = None,
) -> dict[str, Any]:
    secs: list[dict[str, Any]] = [] if refused else _sections(case, framing, grounds, fill_ins, notes, tail, more_notes or [])
    return {
        "run_id": run_id,
        "version": VERSION,
        "matter_id": case.matter_id,
        "kind": case.kind,
        "state": state,
        "refused": refused,
        "sections": secs,
        "text": "\n\n".join(s["text"] for s in secs),
        "grounds": grounds,
        "notes_for_lawyer": notes,
        "more_notes": list(more_notes or []),
        "fill_ins": fill_ins,
        "checks": checks,
        "unresolved_authorities": unresolved or [],
        "framing_status": framing.status,
        "framing_lines": list(framing.lines),
        "facts_used": len(case.facts),
        "facts_excluded": int(getattr(case, "n_facts_excluded", 0)),
        "calls": calls,
        "tokens_in": tin,
        "tokens_out": tout,
        "cost_usd": cost,
        "regenerated": regenerated,
        "incomplete": incomplete,
    }


def draft(case: Any, framing: Any, complete: Completer | None, cfg: Any, run_id: str) -> dict[str, Any]:
    """Build the draft: at most two model calls (the grounds, then one regeneration), mechanical checks after each, never an edit."""
    checks: list[dict[str, Any]] = []
    if case.kind not in KIND_TITLES:
        raise ValueError(f"unknown kind {case.kind!r}")
    hit = next((m.group(0) for f in case.facts if (m := SENSITIVE_RE.search(f.text))), None)
    if hit is not None:  # fail closed: the lawyer's tag is the first line of defence; this is the backstop on the text itself
        checks.append({"name": "sensitive_backstop", "passed": False, "detail": "a fact names a sensitive offence or a minor"})
        return _result(
            case,
            framing,
            run_id,
            state="refused",
            grounds=[],
            notes=[],
            fill_ins=[],
            checks=checks,
            calls=0,
            tin=0,
            tout=0,
            cost=0.0,
            regenerated=False,
            incomplete=False,
            refused=SENSITIVE_REFUSAL,
        )
    mentions = [m for f in case.facts for m in section_strings(f.text)]
    sec = sensitive_section_check(
        mentions, "bail_draft"
    )  # the table is Track C's; the Act is read from the words, none named = both lists
    if sec is not None:
        checks.append(sec)
        return _result(
            case,
            framing,
            run_id,
            state="refused",
            grounds=[],
            notes=[],
            fill_ins=[],
            checks=checks,
            calls=0,
            tin=0,
            tout=0,
            cost=0.0,
            regenerated=False,
            incomplete=False,
            refused=SENSITIVE_REFUSAL,
        )
    if complete is None or not case.facts:
        why = "no model call was available" if complete is None else "no applicant-side fact was given"
        checks.append({"name": "template_only", "passed": True, "detail": why})
        return _result(
            case,
            framing,
            run_id,
            state="template_only",
            grounds=[],
            notes=[],
            fill_ins=_fill_ins(case, []),
            checks=checks,
            calls=0,
            tin=0,
            tout=0,
            cost=0.0,
            regenerated=False,
            incomplete=False,
        )
    pack = build_pack(case)
    est = estimate_tokens(SYSTEM + pack)
    worst = round(est * cfg.usd_per_mtok_in / 1_000_000 + cfg.max_tokens_per_call * cfg.usd_per_mtok_out / 1_000_000, 6)
    refuse: str | None = None
    if cfg.max_calls_per_matter < 1:
        refuse = "the call cap is below one; no call was made"
    elif est > cfg.max_input_tokens_per_call:
        refuse = (
            f"pack of about {est} tokens (an estimate) is over the {cfg.max_input_tokens_per_call}-token cap; no call was made"
        )
    elif worst > cfg.usd_per_matter:
        refuse = f"worst case for one call (an estimate, about ${worst}) is over the ${cfg.usd_per_matter} cap; no call was made"
    if refuse:
        checks.append({"name": "size_or_cap", "passed": False, "detail": refuse})
        return _result(
            case,
            framing,
            run_id,
            state="refused",
            grounds=[],
            notes=[],
            fill_ins=[],
            checks=checks,
            calls=0,
            tin=0,
            tout=0,
            cost=0.0,
            regenerated=False,
            incomplete=False,
            refused=refuse,
        )

    calls = tin = tout = 0
    spent = 0.0
    user = pack
    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    dropped: list[str] = []
    incomplete = regenerated = False
    for attempt in range(min(2, cfg.max_calls_per_matter)):
        if attempt == 1 and spent + worst > cfg.usd_per_matter:
            checks.append(
                {"name": "regeneration_skipped", "passed": False, "detail": "a second call would pass the per-matter cap"}
            )
            break
        c = complete(SYSTEM, user, cfg.max_tokens_per_call)
        if c.model != cfg.model:
            raise AssertionError(f"the completer answered with model {c.model!r}, not the pinned {cfg.model!r}")
        calls, tin, tout = calls + 1, tin + c.input_tokens, tout + c.output_tokens
        spent = cost_usd(tin, tout, cfg)
        if c.stop_reason == "max_tokens":
            incomplete = True
            checks.append({"name": "answer_complete", "passed": False, "detail": "the answer stopped at max_tokens"})
            break
        parsed = parse_answer(c.text)
        if parsed is None:
            checks.append(
                {"name": "answer_is_json", "passed": False, "detail": f"attempt {attempt + 1}: not the asked JSON shape"}
            )
            if attempt == 0:
                user = pack + "\n\nREVISION: your last answer was not the asked JSON object. Answer with the JSON object only."
                regenerated = True
                continue
            break
        grounds, notes = parsed
        results = [(i, check_paragraph(case, p, i)) for i, p in enumerate(grounds)]
        failed = [(i, r) for i, r in results if r]
        kept = [p for (i, r), p in zip(results, grounds, strict=True) if not r]
        checks.append(
            {
                "name": f"paragraph_checks_attempt_{attempt + 1}",
                "passed": not failed,
                "detail": f"{len(kept)} of {len(grounds)} paragraphs passed",
                "failed": [{"paragraph": i + 1, "reasons": r} for i, r in failed],
            }
        )
        if not failed:
            dropped = []
            break
        dropped = [_dropped_note(case, grounds[i], i, r) for i, r in failed]
        if attempt == 0 and min(2, cfg.max_calls_per_matter) > 1:
            user = pack + _feedback(failed)
            regenerated = True
            continue
        break
    unresolved = [
        u
        for p in kept
        for x in parse_citations(p["text"])
        if _key(x) not in {_key(y) for a in case.authorities for y in parse_citations(a)}
        for u in [p["text"][x.span[0] : x.span[1]]]
    ]
    adv = _advisory_words(case, kept)
    tiered: list[
        tuple[int, str]
    ] = []  # (tier, note): 0 engine warnings, 1 model notes on conflicts and adverse points, 2 other model notes, 3 engine info
    flags = _adverse_flags(case)
    if flags:
        checks.append(
            {
                "name": "adverse_voice",
                "passed": True,
                "info": True,
                "detail": f"{len(flags)} fact(s) read like an adverse or other-side fact",
                "fact_ids": [f for f, _ in flags],
            }
        )
        tiered += [(0, ADVERSE_NOTE.format(fid=f, why=why)) for f, why in flags]
    tiered += [(0, n) for n in dropped]
    notes_ok = [n for n in notes if not any(_has_phrase(n.lower(), w) for w in PREDICTIONS)]
    n_before = len(notes_ok)
    notes_ok = [
        n for n in notes_ok if not NOTE_PROVISION_RE.search(n)
    ]  # the heading already names the Act and section: such a note contradicts it
    if len(notes_ok) != n_before:
        checks.append(
            {
                "name": "notes_provision_contradiction",
                "passed": True,
                "info": True,
                "detail": f"{n_before - len(notes_ok)} note(s) about the bail provision left out: the heading names it",
            }
        )
    if len(notes_ok) != len(notes):
        checks.append(
            {
                "name": "notes_prediction",
                "passed": False,
                "detail": f"{len(notes) - len(notes_ok)} note(s) left out: a prediction about this application's result",
            }
        )
        tiered.append((0, NOTE_LEFT_OUT))
    tiered += [(1 if any(k in n.lower() for k in NOTE_KEYS) else 2, n) for n in notes_ok]
    if adv:
        tiered.append((3, "Words in the grounds that are not in your facts (check each): " + ", ".join(adv) + "."))
    if int(getattr(case, "n_facts_excluded", 0)):
        tiered.append(
            (3, f"{int(getattr(case, 'n_facts_excluded', 0))} fact(s) were not marked as the applicant's side and were not used.")
        )
    tiered += [(3, g) for g in (_gaps(case, kept) if kept else [])]
    ordered = [t[1] for _, t in sorted(enumerate(tiered), key=lambda x: (x[1][0], x[0]))]
    final_notes, more_notes = ordered[:NOTES_SHOWN], ordered[NOTES_SHOWN:]
    state = "drafted" if kept else "template_only"
    tail = INCOMPLETE_LINE if incomplete else None
    return _result(
        case,
        framing,
        run_id,
        state=state,
        grounds=kept,
        notes=final_notes,
        more_notes=more_notes,
        fill_ins=_fill_ins(case, kept),
        checks=checks,
        calls=calls,
        tin=tin,
        tout=tout,
        cost=spent,
        regenerated=regenerated,
        incomplete=incomplete,
        tail=tail,
        unresolved=unresolved,
    )
