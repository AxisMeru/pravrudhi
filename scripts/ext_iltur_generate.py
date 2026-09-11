#!/usr/bin/env python3
"""Generate completions for IL-TUR LSI test split under a harness recipe WITH RETRY LOOP.

Applies the same retry-with-feedback loop as the internal harness track but for
set-valued answers (statute section identification). This makes the external
measurement faithful to the harness semantics, rather than being a lower bound
due to missing retries.

Loads recipe, JSONL, and model; generates with retry loop; scores with external
parser (parse_sections); writes results.json with per-item scores and attempt stats.

Usage:
  python ext_iltur_generate.py --recipe harness.json --jsonl iltur-lsi-test.jsonl \\
    --model-dir /path/to/model --output results.json [--limit N] [--dry-run] [--batch-size 4]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

SECTION_PATTERN = re.compile(r"(?i)\bs(?:ection|ec)?s?\.?\s*")
SECTION_NUM = re.compile(r"(\d{1,3}[A-Za-z]{0,2}(?:\([0-9A-Za-z]{1,4}\))?)")
SECTION_JOIN = re.compile(r"(?i)\s*(?:,|&|/|and|or|r/w|read with)\s*")
SECTION_NOTHING = re.compile(
    r"(?i)\bno(?:ne|t any)?\s+(?:section|statute|provision)s?\b(?![^.]*\bother than\b)"
)


def normalise_section_number(num: str) -> str:
    """Normalise section numbers: `302` / `498a` / `294(B)` -> `302` / `498A` / `294(b)`."""
    m = re.match(r"^(\d{1,3})([A-Z]{0,2})(?:\(([0-9A-Z]{1,4})\))?$", num, re.I)
    if not m:
        return num.upper()
    number, suffix, clause = m.group(1), (m.group(2) or "").upper(), m.group(3)
    return f"{number}{suffix}" + (f"({clause.lower()})" if clause else "")


def parse_sections(completion: str) -> set[str] | None:
    """Parse section labels from completion. INDEPENDENT implementation matching spec.

    Returns a set of canonical section names like {"Section 302", "Section 498A"}.
    Returns None if the completion commits to nothing (trailing off).
    Returns empty set if it explicitly says no section applies.

    This is a COPY of the kernel's parser, kept independent to avoid importing
    the kernel. Where this and the kernel disagree, that disagreement is evidence.
    """
    if not completion or not completion.strip():
        return None

    # Check for explicit denial first
    if SECTION_NOTHING.search(completion):
        return set()

    found = set()
    for marker in SECTION_PATTERN.finditer(completion):
        at = marker.end()
        while True:
            num_match = SECTION_NUM.match(completion, at)
            if not num_match:
                break
            found.add(f"Section {normalise_section_number(num_match.group(1))}")
            at = num_match.end()
            join_match = SECTION_JOIN.match(completion, at)
            if not join_match:
                break
            at = join_match.end()

    return found if found else None


def _sort_key_sections(label: str) -> tuple[int, str, str]:
    """Numeric sort key: number, suffix, sub-clause.

    Matches the kernel's sorting for consistency with canonical forms.
    """
    m = re.match(r"Section (\d+)([A-Za-z]*)(\([0-9A-Za-z]+\))?$", label)
    return (int(m.group(1)), m.group(2) or "", m.group(3) or "") if m else (10**6, label, "")


def canonical_sections(labels: set[str] | None) -> str:
    """Convert set of labels to canonical form: sorted, pipe-separated."""
    if labels is None:
        return ""  # Not answered
    if not labels:
        return ""  # Explicitly empty
    return "|".join(sorted(labels, key=_sort_key_sections))


def build_prompt(recipe: dict[str, Any], question: str, feedback: str | None, tokenizer: Any) -> str:
    """Build full prompt using tokenizer's chat template."""
    user = recipe["template"].replace("{question}", question)
    if feedback:
        user += "\n\n" + recipe["feedback_template"].replace("{feedback}", feedback)
    msgs = [
        {"role": "system", "content": recipe["system_prompt"]},
        {"role": "user", "content": user},
    ]
    return str(
        tokenizer.apply_chat_template(
            msgs,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=bool(recipe.get("thinking", False)),
        )
    )


def dry_run_generate(recipe: dict[str, Any], items: list[dict[str, str]], tokenizer: Any) -> None:
    """Print exact requests without actually generating."""
    retries = int(recipe.get("retries", 0))
    temperature = float(recipe.get("temperature", 0.2))
    max_new = int(recipe.get("max_new_tokens", 512))

    print(f"DRY-RUN: Would generate {len(items)} items with up to {retries} retries each")
    print(f"Temperature: {temperature}, max_tokens: {max_new}")
    print()

    for i, item in enumerate(items[:min(3, len(items))]):
        print(f"Item {i}: {item['id']}")
        print("=" * 80)

        # Initial prompt
        prompt = build_prompt(recipe, item["question"], None, tokenizer)
        print("ATTEMPT 1 - Initial prompt:")
        print(f"Prompt length: {len(prompt)} chars")
        print(f"Prompt (first 300 chars): {prompt[:300]}...")
        print()

        # Show retry prompts
        for attempt in range(2, retries + 2):
            feedback = "No sections were identified. Reply with section names, or 'no section' if none apply."
            prompt = build_prompt(recipe, item["question"], feedback, tokenizer)
            print(f"ATTEMPT {attempt} - Prompt with feedback:")
            print(f"Prompt length: {len(prompt)} chars")
            print(f"Prompt (first 300 chars): {prompt[:300]}...")
            print()


def generate_with_retries(
    recipe: dict[str, Any],
    items: list[dict[str, str]],
    model: Any,
    tokenizer: Any,
    batch_size: int = 4,
) -> dict[str, dict[str, Any]]:
    """Generate completions with retry loop, like agent_choice.py but for set answers."""
    retries = int(recipe.get("retries", 0))
    temperature = float(recipe.get("temperature", 0.2))
    max_new = int(recipe.get("max_new_tokens", 512))
    n_samples = int(recipe.get("n_samples", 1))

    def gen_batch(prompts: list[str]) -> list[str]:
        """Generate a batch of completions."""
        outs = []
        import torch

        for b in range(0, len(prompts), batch_size):
            enc = tokenizer(
                prompts[b : b + batch_size],
                return_tensors="pt",
                padding=True,
            ).to("cuda")
            with torch.no_grad():
                g = model.generate(
                    **enc,
                    max_new_tokens=max_new,
                    do_sample=temperature > 0,
                    temperature=max(temperature, 1e-5),
                    top_p=0.95,
                    pad_token_id=tokenizer.pad_token_id or tokenizer.eos_token_id,
                )
            outs += [
                tokenizer.decode(r, skip_special_tokens=True)
                for r in g[:, enc["input_ids"].shape[1] :]
            ]
        return outs

    results: dict[str, dict[str, Any]] = {}
    pending: dict[str, dict[str, Any]] = {}
    for item in items:
        pending[item["id"]] = {
            "question": item["question"],
            "gold_answer": item["answer"],
            "feedback": None,
            "attempt": 0,
        }

    while pending:
        item_ids = list(pending.keys())
        prompts = [
            build_prompt(recipe, pending[i]["question"], pending[i]["feedback"], tokenizer)
            for i in item_ids
            for _ in range(n_samples)
        ]
        completions = gen_batch(prompts)

        nxt: dict[str, dict[str, Any]] = {}
        for k, item_id in enumerate(item_ids):
            cands = completions[k * n_samples : (k + 1) * n_samples]
            parsed = [parse_sections(c) for c in cands]
            attempt = int(pending[item_id]["attempt"])

            # Check if any sample parsed (was not None)
            parsed_results = [p for p in parsed if p is not None]
            if parsed_results:
                # Majority vote: count section sets, pick most common
                # For sets, convert to frozenset for hashing
                frozen = [frozenset(p) for p in parsed_results]
                votes = Counter(frozen)
                winner_frozen = max(votes.items(), key=lambda kv: (kv[1], frozen.index(kv[0])))[0]
                winner = set(winner_frozen)
                chosen = next(c for c in cands if parse_sections(c) == winner)

                results[item_id] = {
                    "id": item_id,
                    "completion": chosen,
                    "parsed_sections": sorted(winner),
                    "gold_answer": pending[item_id]["gold_answer"],
                    "attempts": attempt + 1,
                    "votes": len(parsed_results),
                    "parsed": True,
                    "unparsed": 0.0,
                }
                continue

            # Nothing parsed (all None). Retry if allowed.
            if attempt < retries:
                nxt[item_id] = {
                    "question": pending[item_id]["question"],
                    "gold_answer": pending[item_id]["gold_answer"],
                    "feedback": recipe["feedback_template"].replace(
                        "{feedback}",
                        "No sections were identified. Reply with section names, or 'no section' if none apply.",
                    ),
                    "attempt": attempt + 1,
                }
                continue

            # Retries exhausted, record as unparsed
            results[item_id] = {
                "id": item_id,
                "completion": cands[0] if cands else "",
                "parsed_sections": [],
                "gold_answer": pending[item_id]["gold_answer"],
                "attempts": attempt + 1,
                "votes": 0,
                "parsed": False,
                "unparsed": 1.0,
            }

        pending = nxt

    return results


def jaccard_score(predicted_canonical: str, gold_canonical: str) -> float:
    """Compute Jaccard overlap: |predicted ∩ gold| / |predicted ∪ gold|."""
    predicted = set(filter(None, predicted_canonical.split("|")))
    gold = set(filter(None, gold_canonical.split("|")))

    if not predicted and not gold:
        return 1.0  # Both empty
    if not predicted or not gold:
        return 0.0  # One empty, one not

    union = predicted | gold
    if not union:
        return 1.0
    return len(predicted & gold) / len(union)


def load_model_and_tokenizer(model_dir: str) -> tuple[Any, Any]:
    """Load model and tokenizer from a directory."""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model = AutoModelForCausalLM.from_pretrained(
        model_dir,
        device_map="auto",
        torch_dtype="bfloat16",
        trust_remote_code=False,
    )
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    return model, tokenizer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recipe", type=Path, required=True, help="Harness recipe JSON")
    ap.add_argument("--jsonl", type=Path, required=True, help="IL-TUR test JSONL")
    ap.add_argument("--model-dir", required=True, help="Model directory")
    ap.add_argument("--output", type=Path, default=Path("results.json"), help="Output JSON")
    ap.add_argument("--limit", type=int, default=0, help="Limit items (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="Print requests without generating")
    ap.add_argument("--batch-size", type=int, default=4)
    args = ap.parse_args()

    # Load recipe
    recipe = json.loads(args.recipe.read_text())

    # Load items
    items: list[dict[str, str]] = []
    with args.jsonl.open() as fh:
        for i, line in enumerate(fh):
            if args.limit and i >= args.limit:
                break
            items.append(json.loads(line))
    print(f"Loaded {len(items)} items from {args.jsonl}")

    # Check recipe validity
    if int(recipe.get("n_samples", 1)) > 1:
        print(
            "REFUSED: n_samples > 1 not supported (self-consistency is complex for the external tier)",
            file=sys.stderr,
        )
        return 1

    if args.dry_run:
        print("DRY-RUN MODE: Loading tokenizer only (no model)")
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(args.model_dir)
        dry_run_generate(recipe, items, tokenizer)
        return 0

    print(f"Loading model from {args.model_dir}...")
    try:
        model, tokenizer = load_model_and_tokenizer(args.model_dir)
    except Exception as e:
        print(f"ERROR loading model: {e}", file=sys.stderr)
        return 1

    print(f"Generating with {int(recipe.get('retries', 0))} retries per item...")
    t0 = time.monotonic()
    results = generate_with_retries(recipe, items, model, tokenizer, batch_size=args.batch_size)
    elapsed = time.monotonic() - t0

    # Compute statistics
    n_parsed = sum(1 for r in results.values() if r["parsed"])
    n_unparsed = len(results) - n_parsed
    scores = [
        jaccard_score(canonical_sections(set(r["parsed_sections"])), r["gold_answer"])
        for r in results.values()
    ]
    mean_jaccard = sum(scores) / len(scores) if scores else 0.0
    avg_attempts = sum(r["attempts"] for r in results.values()) / len(results) if results else 0

    # Write results
    output_data = {
        "meta": {
            "n_items": len(results),
            "n_parsed": n_parsed,
            "n_unparsed": n_unparsed,
            "mean_jaccard": mean_jaccard,
            "avg_attempts": avg_attempts,
            "wall_s": elapsed,
            "recipe_retries": int(recipe.get("retries", 0)),
        },
        "results": [results[item["id"]] for item in items],
    }

    args.output.write_text(json.dumps(output_data, indent=2) + "\n")
    print(f"\nResults written to {args.output}")
    print(f"Mean Jaccard: {mean_jaccard:.4f}")
    print(f"Unparsed: {n_unparsed} ({100*n_unparsed/len(results):.1f}%)")
    print(f"Average attempts: {avg_attempts:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
