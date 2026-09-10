"""Kernel-launched scorer for the harness track's APPS pool: runs each candidate's `solve(stdin)` against the
hidden stdin/stdout pairs sealed with the item. Reads /in/samples.jsonl (id, solution) and /in/answers.jsonl
(id, task_id), and the sealed pool mounted read-only at /in/pool; writes /out/scores.jsonl and /out/job_meta.json.
No network, CPU only.

MBPP+ is scored by handing EvalPlus a solution and a task id because EvalPlus ships the tests. APPS ships none:
the hidden pairs exist only inside the sealed pool, which is why this job reads them from a mount the agent image
never gets, instead of from the /cache that both images share.

Each item's pool file is verified against the pool manifest before its tests are used. `seal_pool` writes each
item as the canonical blob it hashes, so the file's sha256 is the manifest's entry: a pool item that was edited
after sealing fails here rather than quietly scoring a run against tests nobody sealed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from apps_check import run_solve

PER_TEST_TIMEOUT_S = 6.0


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def read_pool_item(pool: Path, item_id: str) -> dict[str, Any]:
    blob = (pool / "items" / f"{item_id}.json").read_bytes()
    manifest = json.loads((pool / "manifest.json").read_text())
    if hashlib.sha256(blob).hexdigest() != manifest["item_hashes"][item_id]:
        raise ValueError(f"item {item_id} hash mismatch against the pool manifest")
    item: dict[str, Any] = json.loads(blob)
    return item


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", default="/in/samples.jsonl")
    ap.add_argument("--answers", default="/in/answers.jsonl")
    ap.add_argument("--pool", default="/in/pool")
    ap.add_argument("--out", default="/out")
    ap.add_argument("--timeout", type=float, default=PER_TEST_TIMEOUT_S)
    a = ap.parse_args()
    t0 = time.monotonic()
    pool = Path(a.pool)
    id2task = {
        json.loads(line)["id"]: json.loads(line)["task_id"]
        for line in Path(a.answers).read_text().splitlines()
        if line.strip()
    }
    rows = []
    for line in Path(a.samples).read_text().splitlines():
        if not line.strip():
            continue
        s = json.loads(line)
        try:
            tests = json.loads(read_pool_item(pool, s["id"])["answer"])
            res = run_solve(
                s["solution"],
                list(tests["inputs"]),
                list(tests["outputs"]),
                timeout_s=float(a.timeout),
                fn_name=str(tests.get("fn_name", "solve")),
            )
        except (KeyError, OSError, ValueError) as e:
            res = {"passed": 0, "total": 0, "timed_out": 0,
                   "failures": [f"pool read failed: {type(e).__name__}: {e}"]}
        ok = int(res["total"] > 0 and res["passed"] == res["total"])
        rows.append(
            {
                "id": s["id"],
                "task_id": id2task.get(s["id"]),
                # `score` is the field the kernel's admission path reads; `pass` is the same all-hidden-pass
                # verdict under the name ADR-0029 gives it.
                "score": ok,
                "pass": ok,
                "passed": res["passed"],
                "total": res["total"],
                # Carried per item and totalled below. A timeout is not the same evidence as a wrong answer:
                # under load a correct-but-slow solution exceeds the budget, so the score would depend on
                # what else the machine was doing, and a rotation of timeouts reads identically to a rotation
                # of wrong answers unless the count travels with it.
                "timed_out": res.get("timed_out", 0),
                "failures": res["failures"],
            }
        )
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    out.joinpath("scores.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    n = len(rows)
    meta = {
        "job": "score_apps",
        "samples_sha256": sha256_file(Path(a.samples)),
        "answers_sha256": sha256_file(Path(a.answers)),
        "pool_manifest_sha256": sha256_file(pool / "manifest.json"),
        "n_items": n,
        "n_pass": sum(r["score"] for r in rows),
        "n_timed_out_tests": sum(int(r["timed_out"]) for r in rows),
        "n_items_with_a_timeout": sum(1 for r in rows if r["timed_out"]),
        "timeout_s": float(a.timeout),
        "wall_s": time.monotonic() - t0,
    }
    out.joinpath("job_meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"n": n, "pass": meta["n_pass"] / max(1, n), "wall_s": meta["wall_s"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
