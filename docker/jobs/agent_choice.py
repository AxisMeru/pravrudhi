"""Harness-track job: a fixed model answers MULTIPLE-CHOICE items under a MUTABLE harness.

Reads /in/items.jsonl (id, question) and /in/harness.json; writes /out/samples.jsonl (id, solution, attempts,
votes, parsed) and /out/job_meta.json. The gold answers are never available here.

`agent_code.py` is the sibling for code pools and cannot serve this one: it extracts a fenced Python block and
selects among candidates by RUNNING the visible tests a code prompt carries. A choice question has no visible
tests and its answer is a letter, so on this bench that whole selection machinery is inert -- `retries`,
`n_samples` and `use_visible_tests` would all be no-ops, and the harness track would explore a space where
most of its knobs do nothing while reporting that it had explored it.

What the knobs mean here, all of them gold-free:

* `n_samples`  -- self-consistency. Sample n times and take the MAJORITY letter. A standard technique, and it
                  needs no answer key: it asks the model to agree with itself.
* `retries` + `feedback_template` -- retry when the completion committed to NO letter. That is not a
                  hypothetical failure: at a 512-token budget a third of completions on this pool were cut off
                  before reaching an answer, which depressed the measured pass rate by about 0.21.
* `system_prompt`, `template`, `temperature`, `max_new_tokens`, `thinking` -- as for code.
* `use_visible_tests` -- meaningless on this bench. Reported as inert in job_meta rather than silently
                  ignored, so a recipe that varies only that knob is visibly a null candidate rather than a
                  mysteriously flat result.

IMPORTANT: `_letter` below is a HARNESS POLICY, not a scorer. It decides whether to retry and which sample
wins a vote. The authoritative score comes from `pravrudhi_kernel.metrics.mmlu`, host-side, which is not
importable here by design -- the kernel is not installed in this image, and evidence is the kernel's alone. If
the two disagree at the margin the cost is a suboptimal retry, never a wrong number.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import time
from pathlib import Path

from common import load_model, model_dir_hash, read_jsonl, sha256_file, write_jsonl

LETTERS = "ABCDEFGHIJ"

# Deliberately the same ordered forms `pravrudhi_kernel.metrics.mmlu` uses, so the retry policy and the scorer
# usually agree about whether an answer is present. Kept as a copy rather than an import because the kernel is
# absent from this image on purpose; see the module docstring.
_EXPLICIT = re.compile(r"(?i:\banswers?\b)[*_\s]*(?i:is\b)?[*_\s]*[:=][*_\s]*\(?([A-Ja-j])\)?(?![A-Za-z])")
_BOXED = re.compile(r"\\boxed\{[*_\s]*\(?([A-Ja-j])\)?[*_\s]*\}")
_BARE = re.compile(r"(?i:\banswers?\b)[*_\s]*(?i:is\b)?[*_\s]*\(?([A-J])\)?(?![A-Za-z])")
_LABELLED = re.compile(r"(?i:\b(?:option|choice)\b)[*_\s]*(?i:is\b)?[*_\s]*\(?([A-J])\)?(?![A-Za-z])")
_ALONE = re.compile(r"^[*_\s]*\(?([A-Ja-j])\)?[).:,]?[*_\s]*$")


def _letter(completion: str) -> str | None:
    """The option letter the completion commits to, or None. A harness policy; see the module docstring."""
    for pattern in (_EXPLICIT, _BOXED, _BARE, _LABELLED):
        found = pattern.findall(completion)
        if found:
            return str(found[-1]).upper()
    lines = [ln for ln in completion.splitlines() if ln.strip()]
    if lines:
        m = _ALONE.match(lines[-1])
        if m:
            return m.group(1).upper()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--items", default="/in/items.jsonl")
    ap.add_argument("--harness", default="/in/harness.json")
    ap.add_argument("--out", default="/out")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--batch-size", type=int, default=16)
    a = ap.parse_args()
    import torch

    torch.manual_seed(a.seed)
    torch.cuda.manual_seed_all(a.seed)
    h = json.loads(Path(a.harness).read_text())
    t0 = time.monotonic()
    model, tok = load_model(Path(a.model_dir), None)
    model.eval()
    items = read_jsonl(Path(a.items))
    pad = tok.pad_token_id or tok.eos_token_id
    temperature = float(h["temperature"])
    max_new, retries, n_samples = int(h["max_new_tokens"]), int(h["retries"]), int(h["n_samples"])

    def gen(prompts: list[str]) -> list[str]:
        outs: list[str] = []
        for b in range(0, len(prompts), a.batch_size):
            enc = tok(prompts[b : b + a.batch_size], return_tensors="pt", padding=True).to("cuda")
            with torch.no_grad():
                g = model.generate(
                    **enc,
                    max_new_tokens=max_new,
                    do_sample=temperature > 0,
                    temperature=max(temperature, 1e-5),
                    top_p=0.95,
                    pad_token_id=pad,
                )
            outs += [tok.decode(r, skip_special_tokens=True) for r in g[:, enc["input_ids"].shape[1] :]]
        return outs

    def build(question: str, feedback: str | None) -> str:
        user = h["template"].replace("{question}", question)
        if feedback:
            user += "\n\n" + h["feedback_template"].replace("{feedback}", feedback)
        msgs = [{"role": "system", "content": h["system_prompt"]}, {"role": "user", "content": user}]
        return str(
            tok.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True, enable_thinking=bool(h.get("thinking", False))
            )
        )

    n_tok_calls = 0
    results: dict[str, dict[str, object]] = {}
    pending = {it["id"]: {"q": it["question"], "feedback": None, "attempt": 0} for it in items}
    while pending:
        ids = list(pending)
        prompts = [build(str(pending[i]["q"]), pending[i]["feedback"]) for i in ids for _ in range(n_samples)]  # type: ignore[arg-type]
        outs = gen(prompts)
        n_tok_calls += len(prompts)
        nxt: dict[str, dict[str, object]] = {}
        for k, i in enumerate(ids):
            cands = outs[k * n_samples : (k + 1) * n_samples]
            letters = [x for x in (_letter(c) for c in cands) if x is not None]
            attempt = int(pending[i]["attempt"])  # type: ignore[call-overload]
            if letters:
                # Majority across samples; ties break on the first, which is the lowest-temperature draw's.
                votes = collections.Counter(letters)
                winner = max(votes.items(), key=lambda kv: (kv[1], -letters.index(kv[0])))[0]
                chosen = next(c for c in cands if _letter(c) == winner)
                results[i] = {
                    "id": i,
                    "solution": chosen,
                    "attempts": attempt + 1,
                    "votes": dict(sorted(votes.items())),
                    "parsed": True,
                }
                continue
            # Nothing committed to a letter. Retry if the recipe allows, with the feedback it carries.
            if attempt < retries:
                nxt[i] = {
                    "q": pending[i]["q"],
                    "feedback": h["feedback_template"].replace(
                        "{feedback}", "Your previous answer did not state an option letter. Answer with the letter only."
                    ),
                    "attempt": attempt + 1,
                }
                continue
            results[i] = {
                "id": i,
                "solution": cands[0] if cands else "",
                "attempts": attempt + 1,
                "votes": {},
                "parsed": False,
            }
        pending = nxt  # type: ignore[assignment]

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    write_jsonl(out / "samples.jsonl", [results[i["id"]] for i in items])
    n_unparsed = sum(1 for r in results.values() if not r["parsed"])
    # The key NAMES matter: `pravrudhi_kernel.sandbox.observe` verifies `items_sha256` and `model_sha256`
    # against what it expected and raises HashMismatch otherwise, and it records `model`, `temperature`,
    # `max_new_tokens`, `peak_gib_torch` and `wall_s` into the observation. The first version of this file
    # invented its own names and the kernel refused every row with "model: job saw None" -- which is the
    # check doing its job, and the reason a job cannot quietly report whatever it likes.
    meta = {
        "job": "agent_choice",
        "model": model_dir_hash(Path(a.model_dir)),
        "model_sha256": model_dir_hash(Path(a.model_dir)),
        "items_sha256": sha256_file(Path(a.items)),
        "harness_sha256": sha256_file(Path(a.harness)),
        "n_items": len(items),
        "model_calls": n_tok_calls,
        "tokens_generated": None,
        "wall_s": time.monotonic() - t0,
        "peak_gib_torch": torch.cuda.max_memory_allocated() / 2**30,
        "seed": a.seed,
        "temperature": temperature,
        "max_new_tokens": max_new,
        "n_unparsed": n_unparsed,
        # Declared inert rather than quietly ignored: this bench has no visible tests, so a recipe that
        # varies only that knob is a null candidate and the ledger should be able to say so.
        "use_visible_tests_inert": True,
        "harness": h,
    }
    (out / "job_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: meta[k] for k in ("n_items", "model_calls", "n_unparsed", "wall_s")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
