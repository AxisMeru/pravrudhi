#!/usr/bin/env python3
"""Fetch IL-TUR's LSI `test` split and render it EXACTLY as the internal pool was sealed.

The internal pool (`.pravrudhi/kernel/pools/iltur-lsi-dev`) was built by `pool_admin.seal_lsi` from the `dev`
split: the same instruction, the same 100-section label block, the same head truncation, gold as canonical
section names. The external tier must present the held-out `test` split to the model the same way, or a
difference in the number is a difference in the prompt. So this imports the sealer's own constants and
canonical form rather than restating them; the only thing it does not do is write into the kernel's pools.

Both parquets are gated on the Hub; `HF_TOKEN` (from `~/.config/pravrudhi/hf-axismeru.env`) is sent as a
bearer token. Their sha256 and row counts are written beside the JSONL so the proof records what it read.

usage: HF_TOKEN=... ext_iltur_fetch.py [--output-dir .pravrudhi/ext_cache] [--limit N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HF_REPO = "Exploration-Lab/IL-TUR"
HF_TEST_PATH = "lsi/test-00000-of-00001.parquet"
HF_STATUTES_PATH = "lsi/statutes-00000-of-00001.parquet"
OUTPUT_JSONL = "iltur-lsi-test.jsonl"
META_JSON = "iltur-lsi-fetch.json"


def fetch(repo_id: str, file_path: str, dest: Path) -> dict[str, Any]:
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise SystemExit("HF_TOKEN is not set; source ~/.config/pravrudhi/hf-axismeru.env first")
    url = f"https://huggingface.co/datasets/{repo_id}/resolve/main/{file_path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=120) as response:  # noqa: S310 - a fixed https host
        data = response.read()
    if not data.endswith(b"PAR1"):
        raise SystemExit(f"{url} did not return a parquet file ({len(data)} bytes); is the token accepted?")
    dest.write_bytes(data)
    return {"file": file_path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output-dir", type=Path, default=Path.cwd() / ".pravrudhi" / "ext_cache")
    ap.add_argument("--limit", type=int, default=0, help="Limit rows written (0 = all)")
    args = ap.parse_args()
    out: Path = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    import pyarrow.parquet as pq

    from pravrudhi.application.pool_admin import LSI_INSTRUCTION, LSI_MAX_CASE_CHARS
    from pravrudhi_kernel.metrics.labels import canonical

    test_pq = out / "iltur-lsi-test-raw.parquet"
    statutes_pq = out / "iltur-lsi-statutes-raw.parquet"
    metas = []
    for path, dest in ((HF_TEST_PATH, test_pq), (HF_STATUTES_PATH, statutes_pq)):
        if dest.exists() and dest.read_bytes().endswith(b"PAR1"):
            metas.append({"file": path, "size": dest.stat().st_size,
                          "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "cached": True})
        else:
            metas.append(fetch(HF_REPO, path, dest))
        print(f"{path}: {metas[-1]['size']} bytes sha256 {metas[-1]['sha256'][:16]}…")

    names = [str(r["id"]) for r in pq.read_table(statutes_pq).to_pylist()]
    if len(names) != 100:
        raise SystemExit(f"expected IL-TUR's 100-section label space, got {len(names)}")
    label_block = "\n".join(f"- {n}" for n in names)

    rows = pq.read_table(test_pq).to_pylist()
    written = truncated = skipped = 0
    with (out / OUTPUT_JSONL).open("w") as fh:
        for i, record in enumerate(rows):
            gold = {names[j] for j in record["labels"]}
            if not gold:
                skipped += 1
                continue
            case = " ".join(record["text"]).strip()
            if len(case) > LSI_MAX_CASE_CHARS:
                case = case[:LSI_MAX_CASE_CHARS]
                truncated += 1
            item = {
                "id": f"iltur-lsi-test-{i:05d}",
                "question": f"{LSI_INSTRUCTION}\n\nSections:\n{label_block}\n\nCase:\n{case}",
                "answer": canonical(gold),
            }
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
            written += 1
            if args.limit and written >= args.limit:
                break
    meta = {
        "fetched_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "repo": HF_REPO, "files": metas, "rows_in_split": len(rows), "written": written,
        "skipped_no_gold": skipped, "truncated": truncated, "max_case_chars": LSI_MAX_CASE_CHARS,
        "rendering": "pool_admin.seal_lsi (same instruction, label block, truncation, canonical gold)",
    }
    (out / META_JSON).write_text(json.dumps(meta, indent=1) + "\n")
    print(f"wrote {written} items to {out / OUTPUT_JSONL} ({skipped} without gold skipped, {truncated} truncated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
