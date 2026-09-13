"""Condition A: frozen closed-book baseline on the law-QA held-out set.

Reproduces Track B's own M7 "after" eval (`research/m7/after_report.json` in
/trackB) on the 370M law-tuned checkpoint: closed-book, no retrieval, no TTT,
greedy batched-by-length generation exactly as
`/trackB/scripts/m7/generate.py` does it (same per-kind `max_new_tokens` caps,
same prompt-as-is, same absence of stop strings -- the byte vocab has no EOS,
so Track B's own eval never used one either).

Usage (inside the `ttt-lab` container):
    docker exec ttt-lab bash -lc "cd /lab && \\
        python3 -m prototypes.nyaya_ttt_rsi.baseline --out runs/baseline_A"

Writes, under `<out>/`:
  - answers.jsonl   {"id": ..., "answer": ...} for every held-out record
  - report.json     score_law_qa.py's report + run metadata (wall time, peak
                     VRAM, checkpoint path/sha256-of-first-1MB)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

TRACKB_ROOT = Path("/trackB")
DEFAULT_CHECKPOINT = Path("/trackB-local/m7/m7_retry_checkpoint.pt")
DEFAULT_HELDOUT = TRACKB_ROOT / "data" / "eval" / "law_qa_heldout_v3.jsonl"

from . import model_io

# Per-kind max_new_tokens -- identical to scripts/m7/generate.py's
# DEFAULT_MAX_NEW_TOKENS_BY_KIND (2026-09-12, session-3's decision; see that
# module's docstring for the p99-target-length rationale per kind).
MAX_NEW_TOKENS_BY_KIND = {
    "law_citation_retrieval": 64,
    "law_cite_to_title": 121,
    "law_abstain": 153,
    "law_lookup": 256,
}


def _load_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def generate_all(model, tok, records: list[dict], max_batch_size: int = 16) -> list[dict]:
    """One kind at a time (each kind has its own max_new_tokens cap), grouped
    by exact prompt length within a kind -- mirrors generate.py::generate_answers."""
    answers: dict[str, str] = {}
    for kind, max_new_tokens in MAX_NEW_TOKENS_BY_KIND.items():
        kind_records = [r for r in records if r["kind"] == kind]
        if not kind_records:
            continue
        prompts = [r["prompt"] for r in kind_records]
        texts = model_io.generate(
            model, tok, prompts, max_new_tokens=max_new_tokens, stop=None,
            max_batch_size=max_batch_size,
        )
        for rec, text in zip(kind_records, texts, strict=True):
            answers[rec["id"]] = text
    return [{"id": rid, "answer": ans} for rid, ans in answers.items()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", type=str, default="cuda:0")
    parser.add_argument("--max-batch-size", type=int, default=16)
    args = parser.parse_args()

    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    import torch  # local: GPU-container-only

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    t_load0 = time.perf_counter()
    model, tok = model_io.load_model(args.checkpoint, device=args.device)
    t_load1 = time.perf_counter()

    records = _load_jsonl(args.heldout)

    t_gen0 = time.perf_counter()
    answers = generate_all(model, tok, records, max_batch_size=args.max_batch_size)
    t_gen1 = time.perf_counter()

    answers_path = out_dir / "answers.jsonl"
    with answers_path.open("w", encoding="utf-8") as f:
        for a in answers:
            f.write(json.dumps(a, ensure_ascii=False) + "\n")

    peak_vram_mib = (
        torch.cuda.max_memory_allocated() / (1024 * 1024) if torch.cuda.is_available() else None
    )

    # Score with Track B's own scorer (import, do not reimplement).
    sys.path.insert(0, str(TRACKB_ROOT / "scripts" / "eval"))
    import score_law_qa  # type: ignore[import-not-found]  # noqa: E402

    score_report = score_law_qa.score(args.heldout, answers_path)

    report = {
        "condition": "A_frozen_closed_book",
        "checkpoint_path": str(args.checkpoint),
        "checkpoint_sha256_first_1mb": model_io.checkpoint_sha256_prefix(args.checkpoint),
        "heldout_path": str(args.heldout),
        "n_records": len(records),
        "n_answers": len(answers),
        "wall_clock_seconds": {
            "load_model": t_load1 - t_load0,
            "generation": t_gen1 - t_gen0,
            "total": t_gen1 - t_load0,
        },
        "peak_vram_mib": peak_vram_mib,
        "score": score_report,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
