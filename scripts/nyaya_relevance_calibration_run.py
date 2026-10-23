#!/usr/bin/env python3
"""Issue #51 calibration run on the CURRENT ranking: the absolute floor vs the three named candidates.

Selection rule (fixed in the issue before any run): minimise false citations at recall >= the current recall.
Usage: python scripts/nyaya_relevance_calibration_run.py [tests/fixtures/nyaya_relevance_calibration.json ...]
Prints, per fixture, the absolute-only floor and each candidate's best threshold, recall and false citations.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pravrudhi.application import nyaya

ABS = 8.0  # the configured absolute floor, applied to every candidate as its first gate
NOISY = ("off_topic", "off_topic_verbose", "on_topic_uncovered")

_corpus = nyaya.load_corpus()
_corpus.min_relevance_score = -1.0  # features for every question; each candidate applies its own gate below
_corpus.min_relevance_norm = -1.0
_index = {d.id: i for i, d in enumerate(_corpus.documents)}
_avg = sum(_corpus._len) / len(_corpus.documents)


def features(question: str) -> dict[str, Any] | None:
    hits = _corpus.retrieve(question, k=2)
    if not hits:
        return None
    top, s1 = hits[0]
    s2 = hits[1][1] if len(hits) > 1 else 0.0
    tokens = nyaya._tokens(nyaya.expand(question, _corpus.expansions))
    self_score = _corpus._self_score(tokens, _avg)
    distinct = set(tokens)
    tf = _corpus._tf[_index[top.id]]
    return {
        "top": top.id,
        "s1": s1,
        "norm": s1 / self_score if self_score > 0 else 0.0,
        "cov": sum(1 for t in distinct if t in tf) / len(distinct) if distinct else 0.0,
        "margin": (s1 - s2) / s1 if s1 > 0 else 0.0,
    }


def run(path: str) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    cases = json.loads(Path(path).read_text())["cases"]
    return [(case, features(case["question"])) for case in cases]


def passes(f: dict[str, Any] | None, cand: str, thr: float) -> bool:
    if f is None or f["s1"] < ABS:
        return False
    return True if cand == "absolute" else bool(f[cand] >= thr)


def score(rows: list, cand: str, thr: float) -> tuple[int, int, int, int, list[str]]:
    on = [(k, f) for k, f in rows if k["kind"] == "on_topic"]
    noisy = [(k, f) for k, f in rows if k["kind"] in NOISY]
    recall = sum(1 for k, f in on if passes(f, cand, thr) and f["top"] in k["expected_ids"])
    leaked = [k["id"] for k, f in noisy if passes(f, cand, thr)]
    return recall, len(on), len(leaked), len(noisy), leaked


def sweep(rows: list, cand: str) -> tuple[float, int, int, int, int, list[str]] | None:
    base_recall = score(rows, "absolute", 0.0)[0]
    values = sorted({round(f[cand], 4) for _k, f in rows if f is not None} | {0.0})
    best = None
    for thr in sorted(set(values) | {v + 1e-4 for v in values}, reverse=True):
        recall, n_on, n_leak, n_noisy, leaked = score(rows, cand, thr)
        if recall >= base_recall and (best is None or n_leak < best[3]):
            best = (thr, recall, n_on, n_leak, n_noisy, leaked)
    return best


def main(paths: list[str]) -> None:
    for path in paths or ["tests/fixtures/nyaya_relevance_calibration.json"]:
        rows = run(path)
        print("==", Path(path).name, "cases", len(rows))
        recall, n_on, n_leak, n_noisy, _leaked = score(rows, "absolute", 0.0)
        print(f"absolute-only (main): recall {recall}/{n_on}, false citations {n_leak}/{n_noisy}")
        for cand in ("norm", "cov", "margin"):
            best = sweep(rows, cand)
            if best is None:
                print(cand, "no threshold reaches the baseline recall")
                continue
            thr, recall, n_on, n_leak, n_noisy, leaked = best
            print(f"{cand}: threshold {thr:.4f} (highest giving the minimum): recall {recall}/{n_on}, "
                  f"false citations {n_leak}/{n_noisy}, leaks={leaked}")
        recall, n_on, n_leak, n_noisy, leaked = score(rows, "norm", 0.28)
        print(f"shipped norm 0.28: recall {recall}/{n_on}, false citations {n_leak}/{n_noisy}, leaks={leaked}")


if __name__ == "__main__":
    main(sys.argv[1:])
