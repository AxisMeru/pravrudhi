#!/usr/bin/env python3
"""Fetch the legal-hallucination tasks into the external cache as parquet, recording a sha256 per file.

`nguha/legal_hallucinations_subset` -- the hallucination subset of the Legal LLM Leaderboard -- ships raw
Arrow IPC files under `legal_hallucinations_subset/<task>/data-*.arrow`, while its README declares
`configs: data_files: data/<split>-*`, a path the repository does not contain. `datasets.load_dataset` fails
with `DataFilesNotFoundError`, so lm-eval cannot name the dataset directly. This converts once to parquet the
harness can load, and writes a manifest so the conversion is traceable rather than a file that appeared.

Fetch is a separate act from scoring for the same reason `fetch_apps` is: a scorer that downloads its own
inputs lets a result depend on whatever the network returned that night, with nothing able to tell afterwards.

Usage:
  scripts/fetch_nyaya_hallucinations.py --out .pravrudhi/ext_cache/nyaya_hallucinations
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

REPO = "nguha/legal_hallucinations_subset"
BASE = f"https://huggingface.co/datasets/{REPO}/resolve/main/legal_hallucinations_subset"

# Only tasks whose gold answer is unambiguous enough for exact match. The exclusions matter more than the
# inclusions, so they are recorded here rather than left as an absence:
#   court_id        -- gold mixes "Supreme Court" with bare circuit numbers ("9", "2"); one target format
#                      does not exist, so exact match would score a correct answer wrong.
#   majority_author -- free-text judge names plus "PER CURIAM." / "PER CURIAM:" variants; needs fuzzy name
#                      matching, and a fuzzy matcher is a judgement call rather than a measurement.
#   cited_precedent -- 964 distinct golds among 1000 items because ANY cited precedent is correct and only
#                      one is listed. The column is called `example_correct_answer` for a reason; scoring it
#                      by exact match would mark right answers wrong.
TASKS = ("affirm_reverse", "citation_retrieval", "year_overruled")
EXCLUDED = {
    "court_id": "gold mixes court names with bare circuit numbers; no single target format",
    "majority_author": "free-text judge names and PER CURIAM variants; needs fuzzy matching",
    "cited_precedent": "any cited precedent is correct but only one is listed (example_correct_answer)",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=Path(".pravrudhi/ext_cache/nyaya_hallucinations"))
    args = ap.parse_args()

    import pyarrow as pa
    import pyarrow.parquet as pq

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, object]] = []
    for task in TASKS:
        url = f"{BASE}/{task}/data-00000-of-00001.arrow"
        raw = out / f"{task}.arrow"
        with urllib.request.urlopen(url) as response:  # noqa: S310 - a pinned https URL on the Hub
            blob = response.read()
        raw.write_bytes(blob)
        with pa.memory_map(str(raw), "rb") as src:
            table = pa.ipc.open_stream(src).read_all()
        target = out / f"{task}.parquet"
        pq.write_table(table, target)
        raw.unlink()
        files.append(
            {
                "task": task,
                "file": target.name,
                "n_rows": table.num_rows,
                "columns": table.column_names,
                "arrow_sha256": hashlib.sha256(blob).hexdigest(),
                "parquet_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "source": url,
            }
        )
        print(f"{task}: {table.num_rows} rows -> {target.name}")

    manifest = {
        "repo": REPO,
        "origin": f"https://huggingface.co/datasets/{REPO}",
        "note": (
            "Converted from Arrow IPC to parquet because the repository's declared data_files path "
            "(data/<split>-*) does not exist, so datasets.load_dataset cannot read it."
        ),
        "files": files,
        "excluded_tasks": EXCLUDED,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
