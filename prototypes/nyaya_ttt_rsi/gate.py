"""Regression-probe gate: the cheapest possible stand-in for Pravrudhi's
check_gate/sign_gate flow (propose -> validate against a fixed probe ->
accept or reject), generalized from the throwaway spike's
`apply_gate`/`probe_nll`.

A `RegressionProbe` is a fixed canary set (prompt, continuation) pairs drawn
from a general-domain held-out slice plus a small hand-authored settled-law
list. `decide()` turns a before/after probe NLL pair plus a groundedness
flag into a `GateDecision`. `GateLedger` appends decisions to an
append-only JSONL file for audit.
"""

from __future__ import annotations

import json
import os
import random
from collections import Counter
from dataclasses import asdict, dataclass, field

# Ten settled points of Indian law that a law-tuned model must never lose.
# Hand-authored; deliberately unambiguous, single-fact answers so NLL over
# the continuation is a meaningful drift signal.
SETTLED_LAW_CANARIES: list[tuple[str, str]] = [
    ("Which Article of the Constitution of India abolishes untouchability?", "Article 17"),
    ("Which Article of the Constitution of India abolishes titles?", "Article 18"),
    ("Which Article guarantees the right to equality before the law?", "Article 14"),
    ("Which Article prohibits discrimination on grounds of religion, race, caste, sex or place of birth?", "Article 15"),
    ("Which Article provides for the right to freedom of speech and expression?", "Article 19"),
    ("Which Article protects life and personal liberty?", "Article 21"),
    ("Which Article abolishes the practice of untouchability and forbids its practice in any form?", "Article 17"),
    ("Under which Article can a writ of habeas corpus be sought from the Supreme Court?", "Article 32"),
    ("Which Article makes the Directive Principles of State Policy non-justiciable?", "Article 37"),
    ("Which Article defines the territory of India?", "Article 1"),
]


@dataclass
class RegressionProbe:
    items: list[tuple[str, str]]

    @classmethod
    def from_files(
        cls,
        general_jsonl: str,
        law_canaries_jsonl: str | None = None,
        n_general: int = 32,
        seed: int = 0,
    ) -> "RegressionProbe":
        """Deterministically sample `n_general` (prompt, target) pairs from a
        general-domain validation file (records have `prompt`/`target` keys,
        e.g. Track B's instruct_v1_val.jsonl) and append settled-law
        canaries. `law_canaries_jsonl`, if given and present, must contain
        JSON objects with `prompt`/`target` (or `continuation`) keys and is
        used INSTEAD of the built-in SETTLED_LAW_CANARIES list; otherwise the
        built-in 10-item list is used.
        """
        general: list[tuple[str, str]] = []
        with open(general_jsonl) as f:
            lines = [line for line in f if line.strip()]
        rng = random.Random(seed)
        rng.shuffle(lines)
        for line in lines[:n_general]:
            rec = json.loads(line)
            prompt = rec.get("prompt")
            target = rec.get("target", rec.get("continuation"))
            if prompt is None or target is None:
                continue
            general.append((prompt, target))

        if law_canaries_jsonl and os.path.exists(law_canaries_jsonl):
            law_items: list[tuple[str, str]] = []
            with open(law_canaries_jsonl) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    prompt = rec.get("prompt")
                    target = rec.get("target", rec.get("continuation"))
                    if prompt is not None and target is not None:
                        law_items.append((prompt, target))
        else:
            law_items = list(SETTLED_LAW_CANARIES)

        return cls(items=general + law_items)

    def nll(self, model, tok, nll_fn) -> float:
        """Mean NLL over all canary items. `nll_fn(model, tok, prompt,
        continuation) -> float` is injected (e.g. model_io.sequence_nll) so
        this module has no dependency on model_io's internals."""
        if not self.items:
            return 0.0
        total = 0.0
        for prompt, continuation in self.items:
            total += float(nll_fn(model, tok, prompt, continuation))
        return total / len(self.items)


@dataclass
class GateDecision:
    accepted: bool
    reason: str
    probe_before: float
    probe_after: float
    probe_delta_rel: float
    grounded: bool
    delta_norm: float | None = None
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def decide(
    probe_before: float,
    probe_after: float,
    grounded: bool,
    threshold: float = 0.15,
    require_grounded: bool = True,
    delta_norm: float | None = None,
) -> GateDecision:
    """Accept an update iff (a) groundedness is satisfied (when required)
    and (b) the relative rise in probe NLL stays within `threshold`."""
    probe_delta_rel = (probe_after - probe_before) / max(abs(probe_before), 1e-6)

    if require_grounded and not grounded:
        return GateDecision(
            accepted=False,
            reason="ungrounded",
            probe_before=probe_before,
            probe_after=probe_after,
            probe_delta_rel=probe_delta_rel,
            grounded=grounded,
            delta_norm=delta_norm,
        )

    if probe_delta_rel > threshold:
        return GateDecision(
            accepted=False,
            reason="probe_regression",
            probe_before=probe_before,
            probe_after=probe_after,
            probe_delta_rel=probe_delta_rel,
            grounded=grounded,
            delta_norm=delta_norm,
        )

    return GateDecision(
        accepted=True,
        reason="ok",
        probe_before=probe_before,
        probe_after=probe_after,
        probe_delta_rel=probe_delta_rel,
        grounded=grounded,
        delta_norm=delta_norm,
    )


class GateLedger:
    """Append-only JSONL writer for gate decisions, one line per
    (qid, decision). Written to `runs/<run>/gate_ledger.jsonl`."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        os.makedirs(run_dir, exist_ok=True)
        self.path = os.path.join(run_dir, "gate_ledger.jsonl")

    def append(self, qid: str, decision: GateDecision) -> None:
        record = {"qid": qid, **asdict(decision)}
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        records = []
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def summary(self) -> dict:
        records = self._read_all()
        n = len(records)
        accepted = sum(1 for r in records if r["accepted"])
        rejected = n - accepted
        reasons = Counter(r["reason"] for r in records)
        return {
            "n": n,
            "accepted": accepted,
            "rejected": rejected,
            "reasons": dict(reasons),
        }
