# ruff: noqa: E501
"""A8 synopsis and list of dates: a first draft of the two pages every petition opens with, from the matter's own papers and the facts the lawyer ticks.

(a) LIST OF DATES AND EVENTS: a table built by the ENGINE from A3's chronology rows: the date AS WRITTEN (never rewritten, never inferred; a date that reads two ways
shows both readings), the paper's own sentence as the event, the paper and page as the source. Optionally ONE short model line per row, shown above the paper's sentence
and only when it passes the checks; otherwise the paper's sentence alone.
(b) SYNOPSIS: 1 to 3 short paragraphs from the ticked facts and the listed rows only, each tied to its sources by [F] markers.

It reuses bail_draft's machinery (checks, notes, backstops): ticked facts are F1..Fn, the date rows continue as F(n+1).. so one set of checks reads both. ONE tool-less call
and at most ONE regeneration; a failing paragraph is dropped to the notes and a failing event line falls back to the paper's sentence; the model's words are never edited.
No call (or nothing that passes) gives the table alone. Duck-typed on: case (matter_id, facts[fact_id, text], n_facts_excluded, authorities, parties, document_label) and
cfg (model, usd_per_mtok_in/out, max_tokens_per_call, max_calls_per_matter, max_input_tokens_per_call, usd_per_matter). Limits: mechanical checks only; no lawyer has read these drafts.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

from pravrudhi.application import bail_draft as bd
from pravrudhi.application.bail_draft import Completer, Completion

VERSION = "synopsis-dates-1"
MAX_ROWS = 60
MAX_EVENT_WORDS = 40
MAX_SYNOPSIS_WORDS = 180
QUOTE_CHARS = 200
TABLE_HEADINGS = ("Date", "Event", "Source")
LIST_HEADING = "List of dates and events"
SYNOPSIS_HEADING = "Synopsis"
TABLE_ONLY_LINE = "No synopsis could be drafted from what was entered, so only the list of dates is shown. A lawyer writes the synopsis."  # R1's a8_fallback
DATES_NOTE = "Each date is shown as the papers give it, with the paper it came from. A date the papers do not give is not filled in."  # R1's a8_dates_note
#: R1: barred in the synopsis body (besides the bail list), exempt where the paragraph copies the lawyer's own words.
EXTRA_BARRED = (
    "undisputed",
    "admittedly",
    "evidently",
    "false",
    "frivolous",
    "malicious",
    "baseless",
    "innocent",
    "guilty",
    "proved",
    "established",
)
TOOL_VOICE_RE = re.compile(r"\s*(?:this|the)\s+(?:synopsis|draft|document|page)\b|\s*(?:below|the following)\b", re.I)
NO_ROWS_LINE = "No date was found in the papers added, so the list of dates is empty."
CUT_LINE = "Showing the first {shown} of {total} dates the papers show."
NOTE_SYNOPSIS_CAP = "A synopsis paragraph was not used because the synopsis is limited to {n} words."

SYSTEM = (
    "You write two parts of the opening pages of a draft court document for a lawyer to rewrite. Answer with ONE JSON object and nothing else: "
    '{"event_lines": [{"ref": "F7", "text": "..."}], "synopsis": [{"text": "...", "facts": ["F1", "F7"]}], "notes_for_lawyer": ["..."]}. '
    "The message lists FACTS and DATE ROWS, each with an id [F1], [F2]... Use ONLY them. "
    f"event_lines: for a date row, one short line of ONE sentence (at most {MAX_EVENT_WORDS} words; count them, and stop at the limit rather than add detail) saying what that row's own sentence says happened, in its words; do not write the date, do not add anything the sentence does not say, keep 'alleged' or 'stated' where it says so. "
    f"synopsis: 1 to 3 short paragraphs (at most {MAX_SYNOPSIS_WORDS} words in all) in neutral wording ('the applicant states that ...'); each paragraph names in \"facts\" the ids it rests on and carries the same ids in its text as [F1], [F2]. "
    "Copy any square-bracket placeholder exactly as written; never replace it with a guessed word. "
    "State no fact, place, institution, person, date or amount that is not in the facts and rows. Do not predict an outcome and do not write 'entitled', 'fit case' or 'should be granted'. "
    "Open each paragraph with the party the papers or the lawyer name (the petitioner, applicant, complainant, prosecution) and a verb of stating ('The petitioner states that ...'); never write 'It is submitted that' or 'It is clear that'. "
    "Do not repeat the subject ('The applicant states that the applicant'). In the paragraphs write nothing about what is missing or not given; put every gap or question for the lawyer in notes_for_lawyer. "
    "Name no authority. No headings, no titles, no placeholders of your own."
)


def _quote(row: dict[str, Any]) -> str:
    return " ".join(str(row.get("quote") or "").split())[:QUOTE_CHARS]


def select_rows(chronology: dict[str, Any], include_row_ids: list[Any] | None = None) -> tuple[list[dict[str, Any]], int]:
    """A3's exact and two-reading rows (date order, two-reading ones last), those the lawyer left ticked, at most MAX_ROWS; (rows, total before the cut)."""
    exact = sorted(
        chronology.get("events") or [], key=lambda r: (str(r.get("normalised") or "9999"), str(r.get("document_name")))
    )
    rows = exact + list(chronology.get("ambiguous") or [])
    if include_row_ids is not None:
        keep = {str(x) for x in include_row_ids}
        rows = [r for r in rows if str(r.get("id")) in keep]
    return rows[:MAX_ROWS], len(rows)


def table_rows(rows: list[dict[str, Any]], first_ref: int) -> list[dict[str, Any]]:
    """The engine's table: nothing here is model text. Rows of ONE sentence (A3 gives one row per date) become ONE table row with each date as written, in the order
    A3 gave them; `ref` is the id the model sees for the row."""
    groups: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r.get("doc_id"), r.get("start"), r.get("end")), []).append(r)
    out: list[dict[str, Any]] = []
    for i, grp in enumerate(groups.values()):
        r = grp[0]
        dates = [
            {
                "date_as_written": g.get("date_as_written"),
                "read_as": g.get("normalised"),
                "readings": list(g["readings"]) if g.get("readings") else None,
                "ambiguous": bool(g.get("readings") and len(g["readings"]) > 1),
                "row_id": g.get("id"),
                **{k: g[k] for k in ("from_ocr", "ocr_marker") if k in g},  # R1: only for a matter with an image-read page
            }
            for g in grp
        ]
        out.append(
            {
                "ref": f"F{first_ref + i}",
                "row_id": r.get("id"),
                "row_ids": [d["row_id"] for d in dates],
                "dates": dates,
                "date_as_written": " and ".join(str(d["date_as_written"]) for d in dates),
                "read_as": dates[0]["read_as"] if len(dates) == 1 else None,
                "readings": dates[0]["readings"] if len(dates) == 1 else None,
                "ambiguous": any(d["ambiguous"] for d in dates),
                "event_quote": _quote(r),
                "event_line": None,
                "document_name": r.get("document_name"),
                "page": r.get("page"),
                "source": f"{r.get('document_name')} p.{r.get('page')}",
                **{k: r[k] for k in ("from_ocr", "ocr_marker") if k in r},
            }
        )
    return out


def build_pack(case: Any, table: list[dict[str, Any]]) -> str:
    lines = [f"Matter {case.matter_id}. Document: {getattr(case, 'document_label', None) or 'a court document'}.", "", "FACTS:"]
    lines += [f"[{f.fact_id}] {f.text}" for f in case.facts] or ["(none ticked)"]
    lines += ["", "DATE ROWS (each is one sentence from a paper, with its date as written):"]
    lines += [f"[{t['ref']}] {t['source']}, dates as written '{t['date_as_written']}': {t['event_quote']}" for t in table] or [
        "(none)"
    ]
    return "\n".join(lines)


def parse_answer(text: str) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[str]] | None:
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", body).strip()
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("synopsis"), list) or not isinstance(obj.get("event_lines", []), list):
        return None
    lines: list[dict[str, str]] = []
    for e in obj.get("event_lines", []):
        if not (isinstance(e, dict) and isinstance(e.get("ref"), str) and isinstance(e.get("text"), str)):
            return None
        lines.append({"ref": e["ref"], "text": e["text"].strip()})
    paras: list[dict[str, Any]] = []
    for g in obj["synopsis"]:
        if not (
            isinstance(g, dict)
            and isinstance(g.get("text"), str)
            and isinstance(g.get("facts"), list)
            and all(isinstance(x, str) for x in g["facts"])
        ):
            return None
        paras.append({"text": g["text"].strip(), "facts": list(g["facts"])})
    notes = obj.get("notes_for_lawyer", [])
    if not isinstance(notes, list) or not all(isinstance(n, str) for n in notes):
        return None
    return lines, paras, [n.strip() for n in notes if n.strip()]


def _extra_reasons(pcase: Any, para: dict[str, Any]) -> list[str]:
    """R1's extra barred words in the synopsis body (not where copied from the lawyer's own facts) and the tool's voice at the start of a paragraph."""
    low = para["text"].lower()
    nf = bd._norm(" ".join(f.text for f in pcase.facts))
    why: list[str] = []
    bad = [w for w in EXTRA_BARRED if bd._has_phrase(low, w) and not bd._copied_from_facts(low, w, nf)]
    if bad:
        why.append(f"conclusory: {', '.join(bad[:3])}")
    if re.match(r"\s*it is (?:submitted|clear) that\b", low):
        why.append("blanket_opener: a paragraph opens with the party and a verb of stating, never with 'It is submitted that'")
    if TOOL_VOICE_RE.match(para["text"]):
        why.append("tool_voice: the paragraph speaks as the tool, not as a party")
    return why


def check_event_line(text: str, quote: str) -> list[str]:
    """Reasons an event line fails (empty = passes): too long, a number or date not in the row's sentence, outcome words, a sentence the row does not support, a new noun."""
    why: list[str] = []
    low = text.lower()
    if not text or len(text.split()) > MAX_EVENT_WORDS:
        why.append(f"event_line_length: empty or over {MAX_EVENT_WORDS} words")
    nums = [
        t
        for t in dict.fromkeys(x.rstrip(".,/-") for x in bd.NUMBER_RE.findall(bd.BRACKET.sub(" ", text)))
        if t not in quote and t.replace(",", "") not in quote.replace(",", "")
    ]
    if nums:
        why.append(f"number_not_in_facts: {', '.join(nums[:4])} is not written like that in the paper's sentence")
    nq = bd._norm(quote)
    concl = [p for p in bd.CONCLUSORY if bd._has_phrase(low, p) and not bd._copied_from_facts(low, p, nq)]
    if concl:
        why.append(f"conclusory: {', '.join(concl[:2])}")
    if bd._unsupported_sentences(text, quote):
        why.append("unsupported_sentence: the line says more than the paper's sentence")
    invented = [n for n in bd.INVENTED_NOUNS if bd._has_phrase(low, n) and not bd._has_phrase(quote.lower(), n)]
    if invented:
        why.append(f"new_noun: {', '.join(invented[:3])} is not in the sentence")
    return why


def _result(case: Any, run_id: str, state: str, table: list[dict[str, Any]], paras: list[dict[str, Any]], notes: list[str], more: list[str], fill_ins: list[str], checks: list[dict[str, Any]], calls: int, tin: int, tout: int, cost: float, *, refused: str | None = None, line: str | None = None, cut: str | None = None, incomplete: bool = False, regenerated: bool = False) -> dict[str, Any]:  # fmt: skip
    return {
        "run_id": run_id, "version": VERSION, "matter_id": case.matter_id, "state": state, "refused": refused,
        "list_of_dates": {"heading": LIST_HEADING, "columns": list(TABLE_HEADINGS), "rows": [] if refused else table, "none_line": NO_ROWS_LINE if not table and not refused else None, "cut_line": cut},
        "synopsis": {"heading": SYNOPSIS_HEADING, "paragraphs": paras, "fallback_line": line}, "dates_note": DATES_NOTE,
        "notes_for_lawyer": notes, "more_notes": more, "fill_ins": fill_ins, "checks": checks, "facts_used": len(case.facts), "facts_excluded": int(getattr(case, "n_facts_excluded", 0)),
        "calls": calls, "tokens_in": tin, "tokens_out": tout, "cost_usd": cost, "regenerated": regenerated, "incomplete": incomplete,
    }  # fmt: skip


def draft(
    case: Any,
    chronology: dict[str, Any],
    complete: Completer | None,
    cfg: Any,
    run_id: str,
    include_row_ids: list[Any] | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    rows, total = select_rows(chronology, include_row_ids)
    table = table_rows(rows, len(case.facts) + 1)
    cut = CUT_LINE.format(shown=len(rows), total=total) if total > len(rows) else None
    # fail-closed backstops on the lawyer's own facts and on the papers' sentences
    texts = [f.text for f in case.facts] + [t["event_quote"] for t in table]
    if any(bd.SENSITIVE_RE.search(x) for x in texts):
        checks.append(
            {
                "name": "sensitive_backstop",
                "passed": False,
                "detail": "a fact or a paper's sentence names a sensitive offence or a minor",
            }
        )
        return _result(case, run_id, "refused", [], [], [], [], [], checks, 0, 0, 0, 0.0, refused=bd.SENSITIVE_REFUSAL)
    mentions = [m for x in texts for m in bd.section_strings(x)]
    sec = bd.sensitive_section_check(mentions, "synopsis_dates")
    if sec is not None:
        checks.append(sec)
        return _result(case, run_id, "refused", [], [], [], [], [], checks, 0, 0, 0, 0.0, refused=bd.SENSITIVE_REFUSAL)
    base_notes = (
        [f"{int(getattr(case, 'n_facts_excluded', 0))} fact(s) were not marked as the client's side and were not used."]
        if int(getattr(case, "n_facts_excluded", 0))
        else []
    )
    if complete is None or (not case.facts and not table):
        checks.append(
            {
                "name": "table_only",
                "passed": True,
                "detail": "no model call was available" if complete is None else "no fact and no date row",
            }
        )
        return _result(
            case,
            run_id,
            "table_only",
            table,
            [],
            base_notes,
            [],
            bd._fill_ins(case, []),
            checks,
            0,
            0,
            0,
            0.0,
            line=TABLE_ONLY_LINE,
            cut=cut,
        )
    pack = build_pack(case, table)
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
        return _result(case, run_id, "refused", [], [], [], [], [], checks, 0, 0, 0, 0.0, refused=refuse)
    allfacts = [*case.facts, *[SimpleNamespace(fact_id=t["ref"], text=t["event_quote"], grounds=()) for t in table]]
    pcase = SimpleNamespace(
        matter_id=case.matter_id, kind="regular", facts=allfacts, authorities=[], parties={}, n_facts_excluded=0
    )
    by_ref = {t["ref"]: t for t in table}
    calls = tin = tout = 0
    spent = 0.0
    user = pack
    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    dropped: list[str] = []
    line_fail: list[str] = []
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
        lines, paras, notes = parsed
        failed_lines: dict[str, list[str]] = {}
        good_lines: dict[str, str] = {}
        for ln in lines:
            t = by_ref.get(ln["ref"])
            if t is None:
                failed_lines[ln["ref"]] = ["fact_ref_unknown: not a date row"]
                continue
            why = check_event_line(ln["text"], t["event_quote"])
            if why:
                failed_lines[ln["ref"]] = why
            else:
                good_lines[ln["ref"]] = ln["text"]
        results = [(i, [*bd.check_paragraph(pcase, p, i), *_extra_reasons(pcase, p)]) for i, p in enumerate(paras)]
        failed = [(i, r) for i, r in results if r]
        kept = [p for (_i, r), p in zip(results, paras, strict=True) if not r]
        for ref, txt in good_lines.items():
            by_ref[ref]["event_line"] = txt
        for t in table:
            if t["ref"] in failed_lines:
                t["event_line"] = None
        line_fail = [f"{r}: {'; '.join(w)}" for r, w in failed_lines.items()]
        checks.append({"name": f"checks_attempt_{attempt + 1}", "passed": not failed and not failed_lines, "detail": f"{len(kept)} of {len(paras)} paragraphs and {len(good_lines)} of {len(lines)} event lines passed", "failed_paragraphs": [{"paragraph": i + 1, "reasons": r} for i, r in failed], "failed_event_lines": failed_lines})  # fmt: skip
        if not failed and not failed_lines:
            dropped = []
            break
        dropped = [bd._dropped_note(pcase, paras[i], i, r) for i, r in failed]
        if attempt == 0 and min(2, cfg.max_calls_per_matter) > 1:
            user = pack + bd._feedback(failed) + ("\n- event lines to fix: " + "; ".join(line_fail) if line_fail else "")
            regenerated = True
            continue
        break
    # the synopsis is capped in words: later paragraphs that do not fit go to the notes, never trimmed
    words = 0
    fit: list[dict[str, Any]] = []
    over = 0
    for p in kept:
        n = len(p["text"].split())
        if words + n <= MAX_SYNOPSIS_WORDS:
            fit.append(p)
            words += n
        else:
            over += 1
    if over:
        dropped.append(NOTE_SYNOPSIS_CAP.format(n=MAX_SYNOPSIS_WORDS))
    kept = fit
    tiered: list[tuple[int, str]] = []
    flags = bd._adverse_flags(case)
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
        tiered += [(0, bd.ADVERSE_NOTE.format(fid=f, why=why)) for f, why in flags]
    tiered += [(0, n) for n in dropped]
    if line_fail:
        tiered.append((1, f"{len(line_fail)} event line(s) were not used and the paper's own sentence is shown instead."))
    ok_notes = [
        n for n in notes if not any(bd._has_phrase(n.lower(), w) for w in bd.PREDICTIONS) and not bd.NOTE_PROVISION_RE.search(n)
    ]
    if len(ok_notes) != len(notes):
        checks.append(
            {
                "name": "notes_filtered",
                "passed": True,
                "info": True,
                "detail": f"{len(notes) - len(ok_notes)} note(s) left out (a prediction of the result)",
            }
        )
    tiered += [(1 if any(k in n.lower() for k in bd.NOTE_KEYS) else 2, n) for n in ok_notes]
    tiered += [(3, n) for n in base_notes]
    ordered = [t[1] for _, t in sorted(enumerate(tiered), key=lambda x: (x[1][0], x[0]))]
    shown_notes, more = ordered[: bd.NOTES_SHOWN], ordered[bd.NOTES_SHOWN :]
    state = "drafted" if kept else "table_only"
    line = None if kept else (bd.INCOMPLETE_LINE if incomplete else TABLE_ONLY_LINE)
    return _result(case, run_id, state, table, kept, shown_notes, more, bd._fill_ins(case, kept), checks, calls, tin, tout, spent, line=line, cut=cut, incomplete=incomplete, regenerated=regenerated)  # fmt: skip


__all__ = ["Completer", "Completion", "check_event_line", "draft", "select_rows", "table_rows"]
