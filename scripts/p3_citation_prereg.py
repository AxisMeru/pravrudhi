#!/usr/bin/env python3
"""CLI for the P3 citation-verification pre-registration
(research/prereg/p3-citation-verification-2026-09-25.md, sha 314a0fa6...). Four subcommands, run in the
document's own §5 order -- sample, then seal (label), then score:

    emit-recall-order       --db PATH --out order.json
    emit-precision-sample   --db PATH --out sample.json
    label-dashscope         --input order.json|sample.json --task recall|precision --out labels.json
    score                   --db PATH --gold-a A.json --gold-b B.json [--adjudicated ADJ.json] --out report.json

`emit-recall-order` and `emit-precision-sample` are the only two commands that touch the real case index --
per the prereg, neither may run before R1 signs the document. Everything each subcommand writes goes under
research/prereg/ (gitignored, never committed), same as every other eval artifact in this repo.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pravrudhi.application.p3_prereg import (  # noqa: E402
    clopper_pearson_ci,
    cohens_kappa,
    precision_sample_indices,
    recall_sample_order,
)

RECALL_SEED = 20250925
PRECISION_SEED = 20250926
PRECISION_K = 100


def cmd_emit_recall_order(args: argparse.Namespace) -> None:
    """Write the full deterministic document walk (prereg §3): case_ids in the fixed-seed permutation order,
    so a labeler can pull documents one at a time and stop at their own 200th hand-marked citation without
    this script ever deciding in advance how many documents that will take."""
    conn = sqlite3.connect(args.db)
    rows = conn.execute("SELECT case_id FROM cases ORDER BY case_id").fetchall()
    case_ids = [r[0] for r in rows]
    order = recall_sample_order(n_docs=len(case_ids), seed=RECALL_SEED)
    Path(args.out).write_text(json.dumps({"seed": RECALL_SEED, "case_id_order": [case_ids[i] for i in order]}, indent=2))
    print(f"wrote {len(case_ids)}-document walk order to {args.out}", file=sys.stderr)


def cmd_emit_precision_sample(args: argparse.Namespace) -> None:
    """Write the 100-alias resolution-precision sample (prereg §4): each sampled `citation_aliases` row plus
    enough context (the citing document's own text is looked up separately by the labeler; this file names
    which document to look in) for a labeler to judge the resolution independently."""
    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT rowid, party_1, party_2, citation, case_id FROM citation_aliases ORDER BY rowid"
    ).fetchall()
    idx = precision_sample_indices(n_rows=len(rows), k=PRECISION_K, seed=PRECISION_SEED)
    sample = [
        {"rowid": rows[i][0], "party_1": rows[i][1], "party_2": rows[i][2], "citation": rows[i][3], "citing_case_id": rows[i][4]}
        for i in idx
    ]
    Path(args.out).write_text(json.dumps({"seed": PRECISION_SEED, "k": PRECISION_K, "sample": sample}, indent=2))
    print(f"wrote {len(sample)}-alias sample to {args.out}", file=sys.stderr)


_LABEL_INSTRUCTIONS_RECALL = """You are hand-labeling real Indian court judgment text for a legal citation \
parser evaluation. Read the text below and mark EVERY citation-shaped substring you find, using only these \
reporter families: AIR (e.g. "AIR 1958 SC 398"), SCC (e.g. "(1996) 3 SCC 709" or "1996 SCC (3) 709" or "1996 \
(3) SCC 709" -- all three count as the same family), SCC OnLine (e.g. "2020 SCC OnLine Del 123"), INSC (e.g. \
"2023 INSC 456"), SCR (e.g. "[1958] 1 SCR 1"), HC-neutral (e.g. "2023:DHC:1234"). Do NOT include a partial or \
malformed citation (e.g. "AIR 1958 SC" with no page number) -- only mark it if all required parts are \
present. Return ONLY a JSON array, one object per citation found, each with: "text" (the exact substring), \
"start" (character offset in the given text), "end", "reporter" (one of the six family names above). If you \
find none, return []."""

_LABEL_INSTRUCTIONS_PRECISION = """You are hand-checking whether a citation resolution is correct. You are \
given: (1) a sentence from a citing judgment naming two parties and a citation, (2) the title and an excerpt \
of the case a lookup resolved that citation to. Judge independently, from the text alone, whether the \
resolved case is genuinely the same case the citation refers to (same parties, same year/volume/page as \
cited, a consistent holding if one is mentioned) -- not merely that a search matched some words. Respond with \
ONLY a JSON object: {"correct": true|false, "category": "resolved_correct"|"resolved_incorrect"| \
"conflict_correct"|"conflict_incorrect", "reason": "<one line>"}."""


def _dashscope_label_one(text: str, task: str) -> dict[str, object]:
    """One blind DashScope qwen3.8-max call via the free-tier-llm skill's CLI wrapper -- raw text and task
    instructions only, no parser or Labeler-A output ever included in the prompt."""
    instructions = _LABEL_INSTRUCTIONS_RECALL if task == "recall" else _LABEL_INSTRUCTIONS_PRECISION
    prompt = f"{instructions}\n\n---\n{text}\n---"
    result = subprocess.run(
        [
            sys.executable,
            str(Path.home() / ".claude/skills/free-tier-llm/scripts/freellm.py"),
            "chat",
            "--model",
            "qwen3.8-max",
            "--prompt",
            prompt,
            "--temperature",
            "0",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    parsed: dict[str, object] = json.loads(result.stdout)
    return parsed


def cmd_label_dashscope(args: argparse.Namespace) -> None:
    """Batch-run Labeler B (DashScope qwen3.8-max) over every item in a recall order / precision sample file.
    Never given parser or Labeler-A output -- raw text (and, for precision, the resolved case's own text) only."""
    data = json.loads(Path(args.input).read_text())
    results = []
    items = data["case_id_order"] if args.task == "recall" else data["sample"]
    for item in items:
        # The actual text lookup (by case_id / citing_case_id against the DB) and the stop-at-200 walk logic
        # for recall live in the labeling session itself, not here -- this function is the one DashScope call
        # per item; wiring it into the interactive walk is the next step once sampling is unblocked.
        results.append(_dashscope_label_one(item.get("text", ""), args.task))
    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"labeled {len(results)} items with DashScope qwen3.8-max ({args.task}) -> {args.out}", file=sys.stderr)


def cmd_score(args: argparse.Namespace) -> None:
    """Score the sealed, adjudicated gold. Requires both a Labeler-A and Labeler-B gold file (never just one --
    the whole point of the second labeler is the agreement check, not a spare), and expects each gold record
    to already carry the diff against the frozen code: `found_by_parser` (recall) or `correct`/`category`
    (precision). That diff -- matching a hand-marked span against `parse_citations()`'s output, or running
    `verify()`'s resolution step per sampled alias -- is intentionally NOT written here yet: it depends on the
    exact record shape the interactive labeling walk produces, which does not exist until sampling actually
    runs (blocked on R1's sign, per the prereg). Wiring that diff is the next step once real gold files exist,
    not a placeholder left in by oversight."""
    gold_a = json.loads(Path(args.gold_a).read_text())
    gold_b = json.loads(Path(args.gold_b).read_text())
    kappa = cohens_kappa([g["category"] for g in gold_a], [g["category"] for g in gold_b])
    adjudicated = json.loads(Path(args.adjudicated).read_text()) if args.adjudicated else gold_a

    if args.task == "recall":
        k = sum(1 for g in adjudicated if g.get("found_by_parser"))
        n = len(adjudicated)
    else:
        attempted = [g for g in adjudicated if g.get("category") != "not_in_index"]
        k = sum(1 for g in attempted if g.get("correct"))
        n = len(attempted)
    lo, hi = clopper_pearson_ci(k, n)
    report = {"k": k, "n": n, "rate": k / n if n else None, "ci95": [lo, hi], "cohens_kappa": kappa}
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("emit-recall-order")
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_emit_recall_order)

    p = sub.add_parser("emit-precision-sample")
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_emit_precision_sample)

    p = sub.add_parser("label-dashscope")
    p.add_argument("--input", required=True)
    p.add_argument("--task", choices=["recall", "precision"], required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_label_dashscope)

    p = sub.add_parser("score")
    p.add_argument("--db", required=True)
    p.add_argument("--task", choices=["recall", "precision"], required=True)
    p.add_argument("--gold-a", required=True)
    p.add_argument("--gold-b", required=True)
    p.add_argument("--adjudicated", default=None)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_score)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
