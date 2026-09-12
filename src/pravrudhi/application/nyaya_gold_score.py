"""Score the constructed hetvabhasa gold set (`nyaya_gold.build_gold_set`) against prabhasa-nyaya's Lean
`Verdict.of`, independent of this repo's own generator -- so the gold set that ships with this repo cannot
grade itself. Per class, with Wilson intervals, never as a single aggregate (measurement-design spec section
4.2): `satpratipaksa` and `badhita` are reported "not decided (heuristic path)" rather than scored, because
`Verdict.of` does not decide them either (see prabhasa-nyaya/lean/PrabhasaNyaya/Hetvabhasa.lean).

session-3's A2 decisions (2026-09-12):
  1. taxonomy -- see `nyaya_gold.py`.
  2. verifier of record: prabhasa-nyaya's Lean `Verdict.of`, via its `score` executable -- not this repo's
     own `derive_verdict` (the thing under test here), and not a new Z3 encoding.
  3. `nyaya_validity pass_rate` = the worst DECIDED class's pass rate (min over decided classes), so the
     aggregate can never hide a class; the full per-class table travels in the ledger row's detail.
"""

from __future__ import annotations

import math
import os
import subprocess
from pathlib import Path
from typing import Any

from pravrudhi.application.nyaya_gold import CLASSES

DECIDED_CLASSES = ("valid", "asiddha", "viruddha", "savyabhicara")
NOT_DECIDED_CLASSES = ("satpratipaksa", "badhita")

#: Relative to this repo's root, the sibling checkout's convention (`~/projects/prabhasa-nyaya`). Overridable
#: because a night run's checkout layout is not guaranteed to match a developer's.
DEFAULT_SCORE_BIN = "../prabhasa-nyaya/lean/.lake/build/bin/score"
SCORE_BIN_ENV = "PRABHASA_NYAYA_SCORE_BIN"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """A 95% interval for k successes in n, by Wilson's score method.

    Vendored, not imported, from prabhasa-nyaya's `scripts/score_a1_1.py`: CHARTER's independence rule keeps
    stats vendored per repo rather than shared at runtime. Wilson rather than the normal approximation
    because that one gives [0, 0] at k=0, which reads as certainty from no evidence.
    """
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    d = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return (max(0.0, (centre - half) / d), min(1.0, (centre + half) / d))


def score_bin_path(root: Path) -> Path:
    """The Lean scorer's path: the env var wins, then `configs/nyaya_gold.yaml`'s `score_bin` (config-driven
    per house rule, not a path buried in code), then the sibling-checkout default."""
    override = os.environ.get(SCORE_BIN_ENV)
    if override:
        return Path(override)
    config_path = Path(root) / "configs" / "nyaya_gold.yaml"
    configured = DEFAULT_SCORE_BIN
    if config_path.exists():
        import yaml

        body = yaml.safe_load(config_path.read_text()) or {}
        configured = str(body.get("score_bin") or DEFAULT_SCORE_BIN)
    return (Path(root) / configured).resolve()


def _serialize_world(world: dict[str, list[str]]) -> str:
    return ";".join(f"{locus}:{','.join(props)}" for locus, props in world.items())


def _run_lean_scorer(items: list[dict[str, Any]], score_bin: Path) -> dict[str, str]:
    """Pipe the decidable items to prabhasa-nyaya's `score` executable; return item id -> verdict.

    A subprocess call across the repo boundary, not an import: the two repos declare no runtime dependency on
    each other (CLAUDE.md's independence rule), and the Lean binary is the actual verifier, not a Python
    port of it that could silently drift from what `make gate` in prabhasa-nyaya builds.
    """
    if not items:
        return {}
    if not score_bin.exists():
        raise FileNotFoundError(
            f"the Lean scorer is not built at {score_bin} -- run `make gate` in prabhasa-nyaya first "
            f"(or point {SCORE_BIN_ENV} at an existing build)"
        )
    stdin = "\n".join(
        f"{it['id']}\t{it['paksa']}\t{it['sadhya']}\t{it['hetu']}\t{_serialize_world(it['world'])}"
        for it in items
    ) + "\n"
    proc = subprocess.run(  # noqa: S603
        [str(score_bin)], input=stdin, capture_output=True, text=True, check=True, timeout=60,
    )
    verdicts: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        item_id, _, verdict = line.partition("\t")
        verdicts[item_id] = verdict
    return verdicts


def score_gold_set(items: list[dict[str, Any]], score_bin: Path) -> dict[str, Any]:
    """Score `items` (from `nyaya_gold.build_gold_set`) per class, never as one pooled number.

    Decided classes are scored against prabhasa-nyaya's Lean `Verdict.of`; `satpratipaksa`/`badhita` are
    reported not-decided rather than scored against this repo's own generator or derivation, which would be
    the self-grading problem `nyaya_gold.py` already refuses.
    """
    by_class: dict[str, list[dict[str, Any]]] = {c: [] for c in CLASSES}
    for it in items:
        by_class.setdefault(it["expected"], []).append(it)

    decidable_items = [it for c in DECIDED_CLASSES for it in by_class.get(c, [])]
    lean_verdicts = _run_lean_scorer(decidable_items, score_bin)

    per_class: dict[str, Any] = {}
    decided_rates: list[float] = []
    for cls in CLASSES:
        cls_items = by_class.get(cls, [])
        n = len(cls_items)
        if cls in NOT_DECIDED_CLASSES:
            per_class[cls] = {"n": n, "decided": False, "note": "not decided (heuristic path)"}
            continue
        passed = sum(1 for it in cls_items if lean_verdicts.get(it["id"]) == it["expected"])
        rate = passed / n if n else 0.0
        lo, hi = wilson(passed, n)
        per_class[cls] = {
            "n": n, "passed": passed, "pass_rate": rate, "pass_rate_ci95": [lo, hi], "decided": True,
        }
        if n:
            decided_rates.append(rate)

    return {
        "gate": "nyaya_validity",
        "n": len(items),
        # The worst decided class, never a pooled average: an aggregate that improves because one easy class
        # got bigger while a hard one stayed broken is exactly what a per-class taxonomy exists to catch.
        "pass_rate": min(decided_rates) if decided_rates else 0.0,
        "per_class": per_class,
        "decided_classes": [c for c in CLASSES if per_class[c]["decided"]],
        "not_decided_classes": [c for c in CLASSES if not per_class[c]["decided"]],
    }
