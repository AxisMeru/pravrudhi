"""Phase 1 (2026-09-13 pivot, see runs/<run>/report.json's `pivot_reason`):
build a harness-generated grounded-SFT set from the TRAIN split only.

Pilot verdict that triggered this module: conditions B and C on the
retrieval-grounded prompt scored ~0 citation/abstention on the held-out set
because the 370M model has never seen this prompt template at all -- it just
continues statute-boilerplate text regardless of the injected passages or the
"answer only from provisions below" instruction. No amount of per-query TTT
can fix a format the model has never learned, so this module teaches the
format itself, via ordinary supervised fine-tuning on TRAIN-split gold data
(gold is legitimate here -- this is the training set, never the held-out
set), before loop.py's SFT consolidation (Phase 2) trains a persistent LoRA
on it.

For each eligible train record (kind in {law_citation_retrieval,
law_cite_to_title, law_lookup}):
  - retrieve top-`k` passages for the record's own prompt, force the gold
    (act, section) passage into the top-`k` if BM25 didn't already retrieve
    it (replacing the lowest-ranked non-gold passage), shuffle final
    positions with a seeded RNG;
  - passages truncated to `max_passage_bytes` (same truncation as
    evaluate.truncate_passage_text, at a sentence/word boundary);
  - target = the record's own `target` for law_citation_retrieval /
    law_cite_to_title; for law_lookup, the raw target's provision TEXT is
    truncated to `lookup_text_bytes` and the trailing "Citation: <act>,
    <section>." line is kept verbatim and re-appended, so the citation
    always lands inside a realistic generation budget.

Abstain synthesis: a seeded `abstain_frac` (default 0.2) subset of the
sampled citation-kind examples are instead built with the gold passage
EXCLUDED from the top-`k` retrieved set and `target = ABSTAIN_PHRASE` --
this is exactly the MVP behaviour under test ("abstain when the shown
context does not support an answer"). The train split's own ~80
`law_abstain` records are included as-is (prompt built from whatever
top-`k` retrieves for them -- they are unanswerable by construction, no
gold-exclusion needed), on top of the `n` sampled citation-kind examples,
not counted against `n`.

Every constructed example whose target is a CITATION (law_citation_retrieval,
law_lookup, or any synthesized/real abstain target) is checked with
`retrieval.grounded(retrieval.parse_answer(target), passages)` before being
kept -- this is the harness's own gate on its own synthesized data, exactly
mirroring the runtime gate's groundedness check. `law_cite_to_title`'s target
is a subject/title string ("Commencement of period of limitation..") with no
citation in it at all -- `retrieval.parse_answer`/`grounded` are citation-
format checks and, correctly, never recognize a title as "grounded" (this
mirrors score_law_qa.py's own decision to exclude `law_cite_to_title` from
`_CITABLE_KINDS` and score it separately) -- so the groundedness gate is
skipped for non-abstain `law_cite_to_title` examples; the sanity check there
is simply "the title is non-empty". Rejects are counted, never silently
dropped.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from . import evaluate
from . import retrieval as retrieval_mod

DEFAULT_TRAIN = evaluate.DEFAULT_TRAIN
DEFAULT_HELDOUT = evaluate.DEFAULT_HELDOUT
CITATION_KINDS = ("law_citation_retrieval", "law_cite_to_title", "law_lookup")
ABSTAIN_KIND = "law_abstain"
DEFAULT_N = 3000
DEFAULT_ABSTAIN_FRAC = 0.2
DEFAULT_K = 3
DEFAULT_MAX_PASSAGE_BYTES = 300
DEFAULT_LOOKUP_TEXT_BYTES = 160

# 2026-09-13 pivot fix (round-1b, see FIXES-FOR-MAIN-SESSIONS.md F11): every
# training target gets this suffix appended (AFTER the groundedness gate
# checks the raw target, so the gate's citation/abstain-phrase parsing is
# unaffected). Round 1's targets had no terminator at all -- this model's
# byte tokenizer has no EOS, so nothing during training ever taught it to
# STOP after a correct or abstain answer, and eval's post-hoc stop-string
# truncation (`evaluate.STOP_STRINGS`) can only truncate a stop string the
# model actually emits. Training the model to predict this exact string
# right after every target teaches it to emit the same string
# `evaluate.STOP_STRINGS` looks for, so truncation actually fires at eval
# time instead of letting generation run the full `max_new_tokens` budget
# into a hallucinated continuation (round 1's mode collapse: e.g. an
# otherwise-correct abstain answer followed by a fabricated citation).
TARGET_STOP_SUFFIX = "\n\n"

_CITATION_TAIL_RE = re.compile(r"(?:\r?\n)+(Citation:[^\r\n]*)\s*\Z")


def build_lookup_target(raw_target: str, act: str, section: str, max_text_bytes: int) -> str:
    """Truncate a `law_lookup` record's provision text to `max_text_bytes`
    while always keeping its trailing "Citation: <act>, <section>." line
    intact -- so the citation the harness needs to see is never the part
    that generation runs out of budget before reaching."""
    match = _CITATION_TAIL_RE.search(raw_target)
    if match:
        text, citation_line = raw_target[: match.start()].strip(), match.group(1).strip()
    else:
        text, citation_line = raw_target.strip(), f"Citation: {act}, {section}."
    truncated = evaluate.truncate_passage_text(text, max_text_bytes)
    return f"{truncated}\n\n{citation_line}"


def _select_passages(store, query: str, gold, k: int, exclude_gold: bool):
    """Top-k passages for `query`, either forcing `gold` in (replacing the
    lowest-ranked non-gold passage if BM25 didn't retrieve it) or excluding
    it entirely (for synthesized abstention). Pads from a wider search if
    fewer than `k` non-gold candidates are available."""
    gold_key = (gold.act, gold.section) if gold is not None else None
    wide = store.search(query, max(k * 4, k))
    others = [p for p in wide if (p.act, p.section) != gold_key]

    if exclude_gold:
        return others[:k]

    kept = others[: max(k - 1, 0)]
    if gold is not None:
        kept.append(gold)
    return kept[:k]


def build_example(rec: dict, store, rng: random.Random, *, k: int = DEFAULT_K,
                   max_passage_bytes: int = DEFAULT_MAX_PASSAGE_BYTES,
                   lookup_text_bytes: int = DEFAULT_LOOKUP_TEXT_BYTES,
                   force_abstain: bool = False):
    """Returns (prompt, target, passages) or None if no gold passage exists
    in the corpus for this record (cannot build a grounded example)."""
    act, section = rec["act"], rec["section"]
    gold = store.lookup(act, section)
    if gold is None:
        return None

    passages = _select_passages(store, rec["prompt"], gold, k, exclude_gold=force_abstain)
    rng.shuffle(passages)
    trunc_passages = evaluate.truncate_passages(passages, max_passage_bytes)
    prompt = retrieval_mod.build_grounded_prompt(rec["prompt"], trunc_passages)

    if force_abstain:
        target = retrieval_mod.ABSTAIN_PHRASE
    elif rec["kind"] == "law_lookup":
        target = build_lookup_target(rec["target"], act, section, lookup_text_bytes)
    else:
        target = rec["target"]

    return prompt, target, trunc_passages


def build_real_abstain_example(rec: dict, store, rng: random.Random, *, k: int = DEFAULT_K,
                                max_passage_bytes: int = DEFAULT_MAX_PASSAGE_BYTES):
    """The train split's own `law_abstain` records: unanswerable by
    construction, so whatever top-k retrieves is used as-is (no gold to
    exclude)."""
    passages = store.search(rec["prompt"], k)
    rng.shuffle(passages)
    trunc_passages = evaluate.truncate_passages(passages, max_passage_bytes)
    prompt = retrieval_mod.build_grounded_prompt(rec["prompt"], trunc_passages)
    return prompt, retrieval_mod.ABSTAIN_PHRASE, trunc_passages


def build_dataset(
    train_path=DEFAULT_TRAIN,
    heldout_path=DEFAULT_HELDOUT,
    *,
    n: int = DEFAULT_N,
    abstain_frac: float = DEFAULT_ABSTAIN_FRAC,
    k: int = DEFAULT_K,
    max_passage_bytes: int = DEFAULT_MAX_PASSAGE_BYTES,
    lookup_text_bytes: int = DEFAULT_LOOKUP_TEXT_BYTES,
    seed: int = 0,
    store=None,
) -> tuple[list[dict], dict]:
    records = evaluate.load_jsonl(train_path)
    by_kind: dict[str, list[dict]] = {}
    for r in records:
        by_kind.setdefault(r["kind"], []).append(r)

    if store is None:
        store = retrieval_mod.PassageStore.from_law_files(train_path, heldout_path)

    rng = random.Random(seed)

    citation_pool = [r for k_ in CITATION_KINDS for r in by_kind.get(k_, [])]
    rng.shuffle(citation_pool)

    # Stratify by kind first (roughly equal share per kind), then within
    # that by act, by taking round-robin from each (kind, act) bucket so no
    # single act dominates the sample.
    per_kind_n = n // len(CITATION_KINDS)
    sampled: list[dict] = []
    for kind in CITATION_KINDS:
        pool = [r for r in citation_pool if r["kind"] == kind]
        by_act: dict[str, list[dict]] = {}
        for r in pool:
            by_act.setdefault(r["act"], []).append(r)
        for bucket in by_act.values():
            rng.shuffle(bucket)
        acts = list(by_act.keys())
        take = min(per_kind_n, len(pool))
        taken = 0
        i = 0
        while taken < take and acts:
            act = acts[i % len(acts)]
            bucket = by_act[act]
            if bucket:
                sampled.append(bucket.pop())
                taken += 1
            i += 1
            if all(not b for b in by_act.values()):
                break
    rng.shuffle(sampled)

    n_abstain = int(round(len(sampled) * abstain_frac))
    abstain_ids = set(id(r) for r in rng.sample(sampled, n_abstain)) if n_abstain else set()

    examples: list[dict] = []
    n_rejected_no_gold = 0
    n_rejected_ungrounded = 0
    n_synthetic_abstain = 0
    n_real_abstain = 0

    for rec in sampled:
        force_abstain = id(rec) in abstain_ids
        built = build_example(rec, store, rng, k=k, max_passage_bytes=max_passage_bytes,
                               lookup_text_bytes=lookup_text_bytes, force_abstain=force_abstain)
        if built is None:
            n_rejected_no_gold += 1
            continue
        prompt, target, passages = built
        needs_grounded_check = force_abstain or rec["kind"] != "law_cite_to_title"
        if needs_grounded_check:
            if not retrieval_mod.grounded(retrieval_mod.parse_answer(target), passages):
                n_rejected_ungrounded += 1
                continue
        elif not target.strip():
            n_rejected_ungrounded += 1
            continue
        if force_abstain:
            n_synthetic_abstain += 1
        examples.append({
            "id": rec["id"], "kind": rec["kind"], "act": rec["act"], "section": rec["section"],
            "prompt": prompt, "target": target + TARGET_STOP_SUFFIX, "synthetic_abstain": force_abstain,
        })

    for rec in by_kind.get(ABSTAIN_KIND, []):
        built = build_real_abstain_example(rec, store, rng, k=k, max_passage_bytes=max_passage_bytes)
        prompt, target, passages = built
        if not retrieval_mod.grounded(retrieval_mod.parse_answer(target), passages):
            n_rejected_ungrounded += 1
            continue
        n_real_abstain += 1
        examples.append({
            "id": rec["id"], "kind": rec["kind"], "act": rec["act"], "section": rec["section"],
            "prompt": prompt, "target": target + TARGET_STOP_SUFFIX, "synthetic_abstain": False,
        })

    prompt_bytes = [len(e["prompt"].encode("utf-8")) for e in examples]
    target_bytes = [len(e["target"].encode("utf-8")) for e in examples]
    stats = {
        "n_examples": len(examples),
        "n_rejected_no_gold_passage": n_rejected_no_gold,
        "n_rejected_ungrounded": n_rejected_ungrounded,
        "n_synthetic_abstain": n_synthetic_abstain,
        "n_real_abstain": n_real_abstain,
        "abstain_share": (n_synthetic_abstain + n_real_abstain) / len(examples) if examples else 0.0,
        "by_kind": {kind: sum(1 for e in examples if e["kind"] == kind) for kind in (*CITATION_KINDS, ABSTAIN_KIND)},
        "prompt_bytes": {"mean": sum(prompt_bytes) / len(prompt_bytes) if prompt_bytes else 0.0,
                         "max": max(prompt_bytes) if prompt_bytes else 0},
        "target_bytes": {"mean": sum(target_bytes) / len(target_bytes) if target_bytes else 0.0,
                         "max": max(target_bytes) if target_bytes else 0},
    }
    return examples, stats


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=DEFAULT_N)
    parser.add_argument("--abstain-frac", type=float, default=DEFAULT_ABSTAIN_FRAC)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)

    examples, stats = build_dataset(args.train, args.heldout, n=args.n,
                                     abstain_frac=args.abstain_frac, seed=args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    evaluate.write_jsonl(args.out / "grounded_sft.jsonl", examples)
    (args.out / "grounded_sft_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
