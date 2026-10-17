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
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pravrudhi.application.citations import Citation, parse_citations

VERSION = "bail-draft-1"
BANNER = "Draft for lawyer review."
GROUNDS_HEADING = "GROUNDS"
NOTES_HEADING = "Notes for the lawyer"
FILL_INS_HEADING = "Details to fill in"
KIND_TITLES = {"regular": "BAIL APPLICATION", "anticipatory": "APPLICATION FOR ANTICIPATORY BAIL"}
KIND_WORDS = {"regular": "a regular bail application", "anticipatory": "an application for anticipatory bail"}
SENSITIVE_REFUSAL = (
    "No draft is made for this kind of case (a case under the POCSO Act or involving a minor, a sexual offence, or one you marked "
    "sensitive). A lawyer drafts the application."
)  # R1's a4_sensitive_refusal (9b9bc5a83311f2a0); the route may override it from its own config
MORE_NOTES_HEADING = "More notes"
NOTES_SHOWN = 5  # the notes a lawyer sees first; the rest are folded under MORE_NOTES_HEADING
ADVERSE_NOTE = "Fact {fid} reads like the court's, the prosecution's or the complainant's voice, or an adverse fact ({why}). It is drafted as given: check that it belongs in the applicant's grounds."
INCOMPLETE_LINE = "INCOMPLETE: the model's answer was cut off at the length limit. No grounds were taken from it."
TEMPLATE_ONLY_LINE = "The draft grounds could not be made, so this shows your facts and the notes only."  # R1's a4_template_only (59673ff74f88cd62)
PRAYER = "PRAYER\nThe applicant prays that this Court may be pleased to consider the grounds above. [Lawyer to complete.]"

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
SENSITIVE_RE = re.compile(
    r"\b(rape[sd]?|pocso|protection of children from sexual|sexual (?:assault|offence|offense|harassment|intercourse)|prosecutrix|"
    r"minor (?:victim|girl|boy)|victim (?:is|was) a minor|molest\w*|outrag\w+ (?:the )?modesty)\b",
    re.I,
)
#: Facts that read like the other side's voice or an adverse fact. ADVISORY only: the lawyer ticked the fact, so it is never excluded; it is named in a note and in an info check.
ADVERSE_RE = re.compile(
    r"\b(public prosecutor|state counsel|learned counsel for the (?:state|complainant)|prosecution|the court (?:has )?observed|"
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
    concl = [p for p in CONCLUSORY if _has_phrase(low, p)]
    if concl:
        why.append(f"conclusory: {', '.join(concl[:3])}")
    forb = [p for p in FORBIDDEN if p in low]
    if forb:
        why.append(f"forbidden_words: {', '.join(forb[:3])}")
    if index > 0 and re.match(r"\s*it is submitted\b", low):
        why.append("blanket_opener: only the first paragraph may open with 'It is submitted'")
    # new-noun check against the facts (all of them: a noun in any fact is the lawyer's own)
    facts_words = _words(corpus_all)
    invented = [
        n for n in INVENTED_NOUNS if _has_phrase(low, n) and not _has_phrase(corpus_all.lower(), n) and n not in facts_words
    ]
    if invented:
        why.append(f"new_noun: {', '.join(invented[:4])} is in no fact")
    # every number, date and amount must be in the facts AS WRITTEN (a reformatted date or a computed figure is a blurred fact)
    bare = BRACKET.sub(" ", FACT_MARK.sub(" ", text))
    facts_nocomma = corpus_all.replace(",", "")
    loose = [
        t
        for t in dict.fromkeys(x.rstrip(".,/-") for x in NUMBER_RE.findall(bare))
        if t not in corpus_all and t.replace(",", "") not in facts_nocomma
    ]
    if loose:
        why.append(f"number_not_in_facts: {', '.join(loose[:5])} is not written like that in any fact")
    # authorities only from the supplied list
    supplied = {_key(x) for a in case.authorities for x in parse_citations(a)}
    unresolved = [text[x.span[0] : x.span[1]] for x in parse_citations(text) if _key(x) not in supplied]
    if unresolved:
        why.append(f"authority_not_supplied: {', '.join(dict.fromkeys(unresolved))}")
    return why


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
        dropped = [
            f"Paragraph {i + 1} (facts {', '.join(grounds[i]['facts']) or 'none'}) was not used: {'; '.join(r)}."
            for i, r in failed
        ]
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
    tiered += [(1 if any(k in n.lower() for k in NOTE_KEYS) else 2, n) for n in notes]
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
