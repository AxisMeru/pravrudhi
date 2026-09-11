#!/usr/bin/env python3
"""Generate completions for CaseHOLD test split under a harness recipe WITH RETRY LOOP.

Applies the same retry-with-feedback loop as the internal harness track (agent_choice.py)
but for the external proof tier. This makes the external measurement faithful to the
harness semantics, rather than being a lower bound due to missing retries.

Loads recipe, CSV, and model; generates with retry loop; scores with external parser
(casehold_utils.option_letter); writes results.json with attempts statistics.

Usage:
  python ext_casehold_generate.py --recipe harness.json --csv casehold-test.csv \
    --model-dir /path/to/model --output results.json [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

LETTERS = "ABCDEFGHIJ"
CASEHOLD_HOLDINGS = 5
INSTRUCTION = (
    "The passage below cites a case whose holding has been masked as (<HOLDING>). "
    "Which of the following is the masked holding?"
)


def render_question(stem: str, holdings: list[str]) -> str:
    """Format a CaseHOLD question with options."""
    lines = [stem, ""]
    lines += [f"{LETTERS[i]}. {h.strip()}" for i, h in enumerate(holdings)]
    return "\n".join(lines)


def load_items(csv_path: Path, limit: int = 0) -> list[dict[str, str]]:
    """Load items from CaseHOLD test CSV."""
    csv.field_size_limit(sys.maxsize)
    items: list[dict[str, str]] = []
    _ID, _PROMPT, _FIRST_HOLDING, _LABEL = 0, 1, 2, 12
    with csv_path.open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)
        for record in reader:
            if len(record) <= _LABEL:
                continue
            label = str(record[_LABEL]).strip()
            if not label.isdigit() or not 0 <= int(label) < CASEHOLD_HOLDINGS:
                continue
            holdings = record[_FIRST_HOLDING : _FIRST_HOLDING + CASEHOLD_HOLDINGS]
            items.append(
                {
                    "id": str(record[_ID]),
                    "question": render_question(
                        f"{INSTRUCTION}\n\n{record[_PROMPT].strip()}", holdings
                    ),
                    "answer": LETTERS[int(label)],
                }
            )
            if limit and len(items) >= limit:
                break
    return items


def option_letter(completion: str) -> str | None:
    """Parse option letter from completion. Independent implementation matching spec.

    This is a COPY of the external scorer's parser, kept independent to avoid
    importing the kernel. Where this and the kernel disagree on the same completions,
    that disagreement is evidence about the parsers.
    """
    import re

    PATTERNS = (
        re.compile(r"(?:answer|option|choice)\s*(?:is)?\s*[:\-]?\s*\(?([A-J])\)?\b", re.I),
        re.compile(r"\\boxed\{\s*([A-J])\s*\}"),
        re.compile(r"^\s*\(?([A-J])\)?\s*[.):]", re.M),
    )
    ALONE = re.compile(r"^\s*\(?([A-J])\)?\s*$")

    text = completion or ""
    for pattern in PATTERNS:
        found = pattern.findall(text)
        if found:
            return str(found[-1]).upper()
    for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
        match = ALONE.match(line)
        if match:
            return match.group(1).upper()
    return None


def build_prompt(
    recipe: dict[str, Any],
    question: str,
    feedback: str | None,
    tokenizer: Any,
) -> str:
    """Build the full prompt using tokenizer's chat template, just like agent_choice.py."""
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


def dry_run_generate(
    recipe: dict[str, Any],
    items: list[dict[str, str]],
    tokenizer: Any,
) -> None:
    """Print exact requests without actually generating."""
    retries = int(recipe.get("retries", 0))
    temperature = float(recipe.get("temperature", 0.2))
    max_new = int(recipe.get("max_new_tokens", 512))

    print(f"DRY-RUN: Would generate {len(items)} items with up to {retries} retries each")
    print(f"Temperature: {temperature}, max_tokens: {max_new}")
    print()

    for i, item in enumerate(items[:min(3, len(items))]):  # Show first 3
        print(f"Item {i}: {item['id']}")
        print("=" * 80)

        # Initial prompt
        prompt = build_prompt(recipe, item["question"], None, tokenizer)
        print("ATTEMPT 1 - Initial prompt:")
        print(f"Prompt length: {len(prompt)} chars")
        print(f"Prompt (first 300 chars): {prompt[:300]}...")
        print()

        # Show retry prompts (but don't actually generate to see feedback)
        for attempt in range(2, retries + 2):
            feedback = recipe["feedback_template"].replace(
                "{feedback}",
                "Your previous answer did not state an option letter. Answer with the letter only.",
            )
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
    batch_size: int = 16,
) -> dict[str, dict[str, Any]]:
    """Generate completions with retry loop, like agent_choice.py."""
    retries = int(recipe.get("retries", 0))
    temperature = float(recipe.get("temperature", 0.2))
    max_new = int(recipe.get("max_new_tokens", 512))
    n_samples = int(recipe.get("n_samples", 1))

    def gen_batch(prompts: list[str]) -> list[str]:
        """Generate a batch of completions."""
        outs: list[str] = []
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
            letters = [x for x in (option_letter(c) for c in cands) if x is not None]
            attempt = int(pending[item_id]["attempt"])

            if letters:
                # Majority vote across samples
                import collections

                votes = collections.Counter(letters)
                winner = max(votes.items(), key=lambda kv: (kv[1], -letters.index(kv[0])))[0]
                chosen = next(c for c in cands if option_letter(c) == winner)
                results[item_id] = {
                    "id": item_id,
                    "completion": chosen,
                    "parsed_letter": winner,
                    "gold_answer": pending[item_id]["gold_answer"],
                    "attempts": attempt + 1,
                    "votes": dict(sorted(votes.items())),
                    "exact_match": 1.0 if winner == pending[item_id]["gold_answer"] else 0.0,
                    "unparsed": 0.0,
                }
                continue

            # No letter found. Retry if allowed.
            if attempt < retries:
                nxt[item_id] = {
                    "question": pending[item_id]["question"],
                    "gold_answer": pending[item_id]["gold_answer"],
                    "feedback": recipe["feedback_template"].replace(
                        "{feedback}",
                        "Your previous answer did not state an option letter. Answer with the letter only.",
                    ),
                    "attempt": attempt + 1,
                }
                continue

            # Retries exhausted, record as unparsed
            results[item_id] = {
                "id": item_id,
                "completion": cands[0] if cands else "",
                "parsed_letter": None,
                "gold_answer": pending[item_id]["gold_answer"],
                "attempts": attempt + 1,
                "votes": {},
                "exact_match": 0.0,
                "unparsed": 1.0,
            }

        pending = nxt

    return results


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
    ap.add_argument("--csv", type=Path, required=True, help="CaseHOLD test CSV")
    ap.add_argument("--model-dir", required=True, help="Model directory")
    ap.add_argument("--output", type=Path, default=Path("results.json"), help="Output JSON")
    ap.add_argument("--limit", type=int, default=0, help="Limit items (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="Print requests without generating")
    ap.add_argument("--batch-size", type=int, default=16)
    args = ap.parse_args()

    # Load recipe
    recipe = json.loads(args.recipe.read_text())

    # Load items
    items = load_items(args.csv, limit=args.limit)
    print(f"Loaded {len(items)} items from {args.csv}")

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
    n_parsed = sum(1 for r in results.values() if r["unparsed"] == 0)
    n_unparsed = len(results) - n_parsed
    n_correct = sum(1 for r in results.values() if r["exact_match"] == 1)
    avg_attempts = sum(r["attempts"] for r in results.values()) / len(results) if results else 0

    # Write results
    output_data = {
        "meta": {
            "n_items": len(results),
            "n_parsed": n_parsed,
            "n_unparsed": n_unparsed,
            "exact_match_rate": n_correct / len(results) if results else 0,
            "avg_attempts": avg_attempts,
            "wall_s": elapsed,
            "recipe_retries": int(recipe.get("retries", 0)),
        },
        "results": [results[item["id"]] for item in items],
    }

    args.output.write_text(json.dumps(output_data, indent=2) + "\n")
    print(f"\nResults written to {args.output}")
    print(f"Exact match: {n_correct}/{len(results)} ({100*n_correct/len(results):.1f}%)")
    print(f"Unparsed: {n_unparsed} ({100*n_unparsed/len(results):.1f}%)")
    print(f"Average attempts: {avg_attempts:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
