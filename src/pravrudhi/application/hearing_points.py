# ruff: noqa: E501
"""A9 hearing points for a bail hearing: a one-page oral-argument AID built ONLY from the facts the lawyer enters (each with a side) and, optionally, the matter's hearing-pack lines.
(1) POINTS TO MAKE, each tied to client-side facts; (2) POINTS THE OTHER SIDE MAY RAISE, each tied to a fact that is the other side's, adverse-sounding, or a line from the papers, in the
frame "the papers show ..., which the other side may rely on" (never a prediction of the court's decision); (3) QUESTIONS TO BE READY FOR: prompts for the lawyer, each ending '?', tied to a fact
or to a closed bail-ground label; they state no law and no fact. The engine adds fixed prompts for ground labels no fact covers. No law, no case, nothing from outside what was entered.

Reuses bail_draft's and synopsis_dates' machinery: ONE tool-less call, per-item checks, one regeneration, failing items dropped to the notes (never edited), fallback = the lawyer's facts plus the
engine's gap prompts, sensitive backstops, notes capped at 5. Duck-typed on: case (matter_id, facts[fact_id, text, side 'applicant'|'other'], n_facts_excluded, parties, grounds[], hearing_date)
and cfg (model, usd_per_mtok_in/out, max_tokens_per_call, max_calls_per_matter, max_input_tokens_per_call, usd_per_matter). Limits: mechanical checks only; no lawyer has read these.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from pravrudhi.application import bail_draft as bd
from pravrudhi.application import synopsis_dates as sd
from pravrudhi.application.bail_draft import Completer, Completion

VERSION = "hearing-points-1"
MAX_ITEM_WORDS = 40
MAX_QUESTION_WORDS = 30
MAX_PER_LIST = 8
POINTS_HEADING = "Points to make"
OTHER_HEADING = "Points the other side may raise"
QUESTIONS_HEADING = "Questions to be ready for"
OTHER_FRAME = "may rely on"
FALLBACK_LINE = (
    "No points could be drafted from what was entered. A lawyer prepares the points."  # R1 a9_fallback 88d053446d24628e
)
OTHER_NOTE = "These are points the papers themselves raise. They are not a prediction of what the other side will say or of how the hearing will go."  # R1 a9_other_note 99ea43d23a8bfc82
NO_OTHER_LINE = "Nothing you entered or the papers show was identified as something the other side may rely on."
GAP_PROMPTS = {
    "accusation": "What is the accusation, and under which sections?",
    "custody": "How long has the applicant been in custody, and since which date?",
    "antecedents": "Are there any other cases against the applicant, or none?",
    "flight": "Does the applicant have a fixed residence and ties here, and has he ever failed to attend?",
    "tampering": "Is there any suggestion that the applicant could tamper with evidence or witnesses?",
    "co_accused": "Has any co-accused been granted bail, and on what footing is the applicant's role compared to theirs?",
    "stage": "Has the charge sheet been filed, and what is the stage of the investigation or trial?",
}  # fixed prompts, engine-written; R1 words them
GROUND_RULES = {
    "accusation": r"\b(offence|alleged|allegation|FIR|F\.I\.R|section|sections|charged)\b",
    "custody": r"\b(custody|remand|incarcerat|jail|arrested)\b",
    "antecedents": r"\b(antecedent|previous case|criminal history|first offender|no other case|convicted)\b",
    "flight": r"\b(abscond|flight|untraceable|residence|resides|fixed address|surrender)\b",
    "tampering": r"\b(tamper|influence (?:the )?witness|witnesses)\b",
    "co_accused": r"\b(co-accused|co accused|another accused|parity|same footing)\b",
    "stage": r"\b(charge.?sheet|investigation|trial|chargesheet)\b",
}
GROUND_VOCAB = frozenset(
    [
        "custody",
        "arrest",
        "arrested",
        "antecedents",
        "previous",
        "cases",
        "other",
        "flight",
        "abscond",
        "absconded",
        "tampering",
        "evidence",
        "witnesses",
        "witness",
        "co-accused",
        "charge-sheet",
        "chargesheet",
        "investigation",
        "trial",
        "residence",
        "resides",
        "bail",
        "conditions",
        "period",
        "stage",
        "filed",
        "sections",
        "accusation",
        "offence",
        "applicant",
        "client",
        "police",
        "court",
        "date",
        "since",
        "ties",
        "attend",
        "granted",
        "role",
        "footing",
        "compared",
        "complete",
    ]
)
OTHER_SIDE_FRAME_NOTE = "other-side items must use the frame 'the papers show ..., which the other side may rely on'"

SYSTEM = (
    "You prepare a one-page oral-argument aid for a lawyer going into a bail hearing. Answer with ONE JSON object and nothing else: "
    '{"points_to_make": [{"text": "...", "facts": ["F1"]}], "other_side_may_raise": [{"text": "...", "facts": ["F5"]}], "questions": [{"text": "...?", "facts": ["F2"]}], "notes_for_lawyer": ["..."]}. '
    "The message lists FACTS of two kinds (the client's side, and the other side's or the court's observations as the lawyer entered them) and LINES FROM THE PAPERS, each with an id [F1], [F2]... Use ONLY them. "
    f"points_to_make: each one line (at most {MAX_ITEM_WORDS} words) of what to say, tied by id to CLIENT-side facts only, in the facts' own words; no law, no case, no inference beyond the facts. "
    f"other_side_may_raise: only what the other side's facts, the adverse-sounding facts or the paper lines show; each line begins 'The papers show ...' and ends 'which the other side may rely on', tied by id to the fact it comes from (at most {MAX_ITEM_WORDS} words). Never predict what the court will decide; never add a point that no fact or line shows. "
    f"questions: prompts for the lawyer to answer at the hearing, each ending with a question mark (at most {MAX_QUESTION_WORDS} words), about something the facts show or leave open (custody, antecedents, flight, tampering, co-accused, stage); they state no law and no fact of their own. "
    "Copy any square-bracket placeholder exactly as written. State no fact, place, institution, person, date or amount that is not in the facts and lines. Do not say what the court will do, and do not write 'entitled', 'fit case' or 'should be granted'. "
    "Do not repeat the subject ('The applicant states that the applicant'). Put every gap or question about the instructions in notes_for_lawyer. No headings, no titles, no placeholders of your own."
)


def gap_labels(facts: list[Any]) -> list[str]:
    """Closed ground labels that no entered fact touches, in a fixed order."""
    text = " ".join(f.text for f in facts)
    return [g for g, rx in GROUND_RULES.items() if not re.search(rx, text, re.I)]


def pack_lines(hearing_pack: dict[str, Any] | None) -> list[str]:
    """Plain sentences from an A7 hearing-pack page (listing dates, citations the index could not match or check); one per item, as the page gives them."""
    out: list[str] = []
    if not hearing_pack:
        return out
    for r in (hearing_pack.get("next_dates") or {}).get("rows") or []:
        out.append(f"The papers give {r.get('date_as_written')} as a listing or hearing date ({r.get('source')}).")
    cites = hearing_pack.get("citations") or {}
    for c in cites.get("needs_attention") or []:
        out.append(f"The papers cite {c.get('text')}, which the index did not find or match.")
    for c in cites.get("not_checked") or []:
        out.append(f"The papers cite {c.get('text')}, which the index could not check.")
    return out[:20]


def build_pack(case: Any, lines: list[str], first_line_ref: int) -> str:
    own = [f for f in case.facts if getattr(f, "side", "applicant") == "applicant"]
    oth = [f for f in case.facts if getattr(f, "side", "applicant") != "applicant"]
    out = [f"Matter {case.matter_id}. Bail hearing.", "", "FACTS, client's side:"]
    out += [f"[{f.fact_id}] {f.text}" for f in own] or ["(none)"]
    out += ["", "FACTS, the other side's or the court's observations (as the lawyer entered them):"]
    out += [f"[{f.fact_id}] {f.text}" for f in oth] or ["(none)"]
    out += ["", "LINES FROM THE PAPERS:"] + ([f"[F{first_line_ref + i}] {t}" for i, t in enumerate(lines)] or ["(none)"])
    g = getattr(case, "grounds", None) or []
    if g:
        out += ["", "GROUNDS ALREADY DRAFTED (stay consistent with them; cite facts, not these):"] + [
            f"- {x.get('text')}" for x in g
        ]
    return "\n".join(out)


def parse_answer(text: str) -> tuple[dict[str, list[dict[str, Any]]], list[str]] | None:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body).strip()
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    lists: dict[str, list[dict[str, Any]]] = {}
    for key in ("points_to_make", "other_side_may_raise", "questions"):
        raw = obj.get(key, [])
        if not isinstance(raw, list):
            return None
        items: list[dict[str, Any]] = []
        for g in raw:
            if not (
                isinstance(g, dict)
                and isinstance(g.get("text"), str)
                and isinstance(g.get("facts", []), list)
                and all(isinstance(x, str) for x in g.get("facts", []))
            ):
                return None
            items.append({"text": g["text"].strip(), "facts": list(g.get("facts", []))})
        lists[key] = items[:MAX_PER_LIST]
    notes = obj.get("notes_for_lawyer", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        return None
    return lists, [n.strip() for n in notes if n.strip()]


def check_item(
    kind: str,
    item: dict[str, Any],
    pcase: Any,
    own_ids: set[str],
    other_ids: set[str],
    pack_ids: set[str],
    adverse_ids: set[str],
    index: int,
) -> list[str]:
    """Mechanical reasons an item fails (empty = passes)."""
    text = item["text"]
    why: list[str] = []
    words = len(text.split())
    if kind == "questions":
        if words > MAX_QUESTION_WORDS or not text.rstrip().endswith("?") or not QUESTION_START.match(text):
            why.append(f"not_a_question: a question ends with '?' and has at most {MAX_QUESTION_WORDS} words")
    elif words > MAX_ITEM_WORDS:
        why.append(f"item_length: over {MAX_ITEM_WORDS} words")
    cited = [c for c in dict.fromkeys([*item["facts"], *bd.FACT_MARK.findall(text)])]
    known = {f.fact_id for f in pcase.facts}
    if kind == "questions":
        # a question may rest on a fact or on the closed ground vocabulary; it may state nothing else
        unk = [c for c in cited if c not in known]
        if unk:
            why.append(f"fact_ref_unknown: {', '.join(unk)} is not one of the numbered facts")
        ctext = " ".join(f.text for f in pcase.facts if f.fact_id in cited)
        low = bd.BRACKET.sub(" ", bd.FACT_MARK.sub(" ", text.lower()))
        toks = [t for t in bd.WORD.findall(low) if len(t) >= 4 and t not in bd.STOP_WORDS and t not in bd.BOILERPLATE]
        fw = bd._words(ctext)
        legal = [t for t in toks if t in LEGAL_WORDS and t not in fw]
        if legal:
            why.append(f"legal_reference: {', '.join(legal[:3])} is not in the facts; a question states no law")
        bad = [t for t in toks if t not in fw and t not in GROUND_VOCAB and t not in QUESTION_STEMS]
        if toks and len(bad) / len(toks) > 0.5:
            why.append(f"unsupported_sentence: {text[:70]!r} says more than the facts it cites")
        nums = [
            t
            for t in dict.fromkeys(x.rstrip(".,/-") for x in bd.NUMBER_RE.findall(bd.FACT_MARK.sub(" ", text)))
            if t not in ctext
        ]
        if nums:
            why.append(f"number_not_in_facts: {', '.join(nums[:3])} is not written like that in the facts it cites")
        invented = [
            n
            for n in bd.INVENTED_NOUNS
            if bd._has_phrase(low, n) and not bd._has_phrase(" ".join(f.text for f in pcase.facts).lower(), n)
        ]
        if invented:
            why.append(f"new_noun: {', '.join(invented[:3])} is in no fact")
        return why
    base = bd.check_paragraph(
        pcase,
        {"text": FRAME_RE.sub(" ", text) if kind == "other_side_may_raise" else text, "facts": item["facts"]},
        1 if index else 0,
    )
    why += [w for w in base if not w.startswith("blanket_opener")] + sd._extra_reasons(pcase, {"text": text})
    ids = set(cited) & known
    if kind == "points_to_make":
        off = sorted(ids - own_ids)
        if off:
            why.append(f"wrong_side: {', '.join(off)} is not a client-side fact")
    if kind == "other_side_may_raise":
        if not (ids & (other_ids | pack_ids | adverse_ids)):
            why.append(
                "no_other_side_source: it cites no fact of the other side, no adverse-sounding fact and no line from the papers"
            )
        if OTHER_FRAME not in text.lower():
            why.append(f"frame: {OTHER_SIDE_FRAME_NOTE}")
    return why


QUESTION_START = re.compile(
    r"\s*(?:how|what|when|where|which|who|why|is|are|was|were|has|have|had|does|do|did|can|could|will|would|should)\b", re.I
)
LEGAL_WORDS = frozenset(
    [
        "supreme",
        "judgment",
        "judgments",
        "ruling",
        "rulings",
        "precedent",
        "authority",
        "citation",
        "article",
        "constitution",
        "statute",
        "held",
        "law",
        "laws",
        "act",
        "acts",
        "section",
    ]
)
FRAME_RE = re.compile(r"(?i)\bthe papers show(?: that)?\b|,?\s*which the other side may rely on\b")
QUESTION_STEMS = frozenset(
    [
        "what",
        "when",
        "where",
        "which",
        "does",
        "have",
        "been",
        "will",
        "whether",
        "there",
        "their",
        "hearing",
        "court",
        "ready",
        "answer",
        "prompt",
    ]
)


def _result(case: Any, run_id: str, state: str, lists: dict[str, list[dict[str, Any]]], gaps: list[str], notes: list[str], more: list[str], checks: list[dict[str, Any]], calls: int, tin: int, tout: int, cost: float, *, refused: str | None = None, line: str | None = None, incomplete: bool = False, regenerated: bool = False) -> dict[str, Any]:  # fmt: skip
    return {
        "run_id": run_id, "version": VERSION, "matter_id": case.matter_id, "state": state, "refused": refused,
        "points_to_make": {"heading": POINTS_HEADING, "items": lists.get("points_to_make", [])},
        "other_side_may_raise": {"heading": OTHER_HEADING, "note": OTHER_NOTE, "items": lists.get("other_side_may_raise", []), "none_line": NO_OTHER_LINE if state == "drafted" and not lists.get("other_side_may_raise") else None},
        "questions": {"heading": QUESTIONS_HEADING, "items": lists.get("questions", []), "gap_prompts": [{"ground": g, "text": GAP_PROMPTS[g]} for g in gaps] if not refused else []},
        "fallback_line": line, "notes_for_lawyer": notes, "more_notes": more, "checks": checks, "facts_used": len(case.facts), "facts_excluded": int(getattr(case, "n_facts_excluded", 0)),
        "calls": calls, "tokens_in": tin, "tokens_out": tout, "cost_usd": cost, "regenerated": regenerated, "incomplete": incomplete,
    }  # fmt: skip


def draft(case: Any, hearing_pack: dict[str, Any] | None, complete: Completer | None, cfg: Any, run_id: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    lines = pack_lines(hearing_pack)
    texts = [f.text for f in case.facts] + lines
    if any(bd.SENSITIVE_RE.search(x) for x in texts):
        checks.append(
            {
                "name": "sensitive_backstop",
                "passed": False,
                "detail": "a fact or a paper's line names a sensitive offence or a minor",
            }
        )
        return _result(case, run_id, "refused", {}, [], [], [], checks, 0, 0, 0, 0.0, refused=bd.SENSITIVE_REFUSAL)
    mentions = [m for x in texts for m in bd.section_strings(x)]
    chk = bd._sensitive_checker() if mentions else None
    if mentions and chk is None:
        bd._log.warning(
            "hearing_points: the sensitive-section table is not available; the section backstop is OFF for this draft"
        )
        checks.append(
            {
                "name": "sensitive_section_table_missing",
                "passed": False,
                "info": True,
                "detail": "the sensitive-section table was not found; the section backstop did not run",
            }
        )
    if chk is not None and chk(mentions):
        checks.append({"name": "sensitive_section_backstop", "passed": False, "detail": bd.SENSITIVE_SECTION_NOTE})
        return _result(case, run_id, "refused", {}, [], [], [], checks, 0, 0, 0, 0.0, refused=bd.SENSITIVE_REFUSAL)
    gaps = gap_labels(case.facts)
    n = len(case.facts)
    line_facts = [SimpleNamespace(fact_id=f"F{n + 1 + i}", text=t, side="paper") for i, t in enumerate(lines)]
    cites = (hearing_pack or {}).get("citations") or {}
    cited = [
        c["text"] for c in [*cites.get("needs_attention", []), *cites.get("not_checked", [])] if c.get("text")
    ]  # citations the papers themselves cite may be named
    pcase = SimpleNamespace(
        matter_id=case.matter_id,
        kind="regular",
        facts=[*case.facts, *line_facts],
        authorities=cited,
        parties={},
        n_facts_excluded=0,
    )
    own_ids = {f.fact_id for f in case.facts if getattr(f, "side", "applicant") == "applicant"}
    other_ids = {f.fact_id for f in case.facts if getattr(f, "side", "applicant") != "applicant"}
    pack_ids = {f.fact_id for f in line_facts}
    adverse_ids = {fid for fid, _ in bd._adverse_flags(SimpleNamespace(facts=[f for f in case.facts if f.fact_id in own_ids]))}
    base_notes = (
        [f"{int(getattr(case, 'n_facts_excluded', 0))} fact(s) were not used."]
        if int(getattr(case, "n_facts_excluded", 0))
        else []
    )
    if complete is None or not own_ids:
        reason = "no model call was available" if complete is None else "no client-side fact was entered"
        checks.append({"name": "facts_and_prompts_only", "passed": True, "detail": reason})
        return _result(case, run_id, "prompts_only", {}, gaps, base_notes, [], checks, 0, 0, 0, 0.0, line=FALLBACK_LINE)
    pack = build_pack(case, lines, n + 1)
    est = bd.estimate_tokens(SYSTEM + pack)
    worst = round(est * cfg.usd_per_mtok_in / 1_000_000 + cfg.max_tokens_per_call * cfg.usd_per_mtok_out / 1_000_000, 6)
    refuse = None
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
        return _result(case, run_id, "refused", {}, [], [], [], checks, 0, 0, 0, 0.0, refused=refuse)
    calls = tin = tout = 0
    spent = 0.0
    user = pack
    kept: dict[str, list[dict[str, Any]]] = {}
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
        spent = bd.cost_usd(tin, tout, cfg)
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
        lists, notes = parsed
        kept = {}
        failed: list[tuple[str, int, list[str]]] = []
        for kind, items in lists.items():
            kept[kind] = []
            for i, it in enumerate(items):
                why = check_item(kind, it, pcase, own_ids, other_ids, pack_ids, adverse_ids, i)
                if why:
                    failed.append((kind, i, why))
                else:
                    kept[kind].append(it)
        checks.append({"name": f"item_checks_attempt_{attempt + 1}", "passed": not failed, "detail": f"{sum(len(v) for v in kept.values())} of {sum(len(v) for v in lists.values())} items passed", "failed": [{"list": k, "item": i + 1, "reasons": r} for k, i, r in failed]})  # fmt: skip
        if not failed:
            dropped = []
            break
        dropped = [
            bd._dropped_note(pcase, lists[k][i] | {"facts": lists[k][i]["facts"]}, i, r).replace(
                "A ground was not used", f"A {k.replace('_', ' ')} item was not used"
            )
            for k, i, r in failed
        ]
        if attempt == 0 and min(2, cfg.max_calls_per_matter) > 1:
            user = (
                pack
                + "\n\nREVISION: rewrite the whole answer. These items failed checks; fix them, keep the rest:\n"
                + "\n".join(f"- {k} item {i + 1}: {'; '.join(r)}" for k, i, r in failed)
            )
            regenerated = True
            continue
        break
    tiered: list[tuple[int, str]] = []
    flags = bd._adverse_flags(SimpleNamespace(facts=[f for f in case.facts if f.fact_id in own_ids]))
    if flags:
        checks.append(
            {
                "name": "adverse_voice",
                "passed": True,
                "info": True,
                "detail": f"{len(flags)} client-side fact(s) read like an adverse or other-side fact",
                "fact_ids": [f for f, _ in flags],
            }
        )
        tiered += [(0, bd.ADVERSE_NOTE.format(fid=f, why=why)) for f, why in flags]
    tiered += [(0, d) for d in dropped]
    ok_notes = [m for m in notes if not any(bd._has_phrase(m.lower(), w) for w in bd.PREDICTIONS)]
    tiered += [(1 if any(k in m.lower() for k in bd.NOTE_KEYS) else 2, m) for m in ok_notes]
    tiered += [(3, m) for m in base_notes]
    ordered = [t[1] for _, t in sorted(enumerate(tiered), key=lambda x: (x[1][0], x[0]))]
    shown, more = ordered[: bd.NOTES_SHOWN], ordered[bd.NOTES_SHOWN :]
    has_any = any(kept.get(k) for k in kept)
    state = "drafted" if has_any else "prompts_only"
    line = None if has_any else (bd.INCOMPLETE_LINE if incomplete else FALLBACK_LINE)
    return _result(
        case,
        run_id,
        state,
        kept,
        gaps,
        shown,
        more,
        checks,
        calls,
        tin,
        tout,
        spent,
        line=line,
        incomplete=incomplete,
        regenerated=regenerated,
    )


__all__ = ["Completer", "Completion", "check_item", "draft", "gap_labels", "pack_lines"]
