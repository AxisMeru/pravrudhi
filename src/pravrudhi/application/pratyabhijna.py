"""pratyabhijñā — re-cognition of testimony (āgama) by execution (pratyakṣa).

CHARTER.md §1 names pratyabhijñā, alongside pramāṇa-tagged evidence and bādha, as one of the three darśana-derived
pieces of the engine's epistemology. `application/execute.py` names it in a docstring but never performs it as a
separate, auditable act: a candidate's sealed prediction (`propose.py` writes a hash-committed `predict` row with
provenance `agama` before the candidate is ever run) is compared to its outcome only inside `evaluate_and_dispose`,
as a Brier number folded into the candidate `observe` row's payload. That number says how confident the testimony
was; it does not say whether the testimony being scored is even the one that was committed, and it leaves no row
of its own for a reader or a later gate to find.

This module is that separate act. Given a candidate, it:

1. re-derives the sealed prediction's commitment hash from the revealed `(candidate_id, delta_in, conf, salt)`
   and checks it against the hash the `predict` row locked into the chain *before* execution -- the testimony
   being re-cognized must be the one that was actually committed, not a rewritten one;
2. compares the testimony's claimed direction of improvement to the kernel-executed (`pratyakṣa`) observation's
   direction, by sign, inside a small zero band so that float noise near zero is never read as a claim; and
3. writes the verdict as its own `reflect` ledger row, tagged `provenance: pratyaksha` because the re-cognition
   is grounded in the kernel's execution, not in the testimony it is judging.

A testimony that is not re-cognized is defeated. Per CHARTER.md §1, bādha is the engine's only update rule, so a
defeat is recorded exactly the way every other defeat in this ledger is: a `sublate` row naming the row it
supersedes (ADR-0015's mechanism, already used for withdrawn observations and promotions), never a bespoke one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pravrudhi_kernel.ledger import LedgerWriter
from pravrudhi_kernel.ledger.verify import iter_events
from pravrudhi_kernel.schema import Hetvabhasa, LedgerEvent, Pramana

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "assets" / "configs" / "pratyabhijna.yaml"
_CONFIG = yaml.safe_load(_CONFIG_PATH.read_text())
ZERO_BAND = float(_CONFIG["zero_band"])


@dataclass(frozen=True)
class Recognition:
    """The verdict of one re-cognition: whether execution re-cognized the testimony it is compared against."""

    commitment_verified: bool
    sign_agrees: bool
    recognized: bool
    hetvabhasa: str | None
    predicted_delta_in: float
    predicted_conf: float
    observed_delta_in: float


def _sign(x: float, zero_band: float) -> int:
    """-1/0/+1, with anything inside the zero band read as "no direction", never as a coin toss on rounding."""
    if abs(x) <= zero_band:
        return 0
    return 1 if x > 0 else -1


def recognize_testimony(
    sealed: dict[str, Any],
    committed_hash: str,
    observed_delta_in: float,
    *,
    zero_band: float = ZERO_BAND,
) -> Recognition:
    """Compare one sealed testimony to one execution outcome. Pure: no ledger I/O.

    `sealed` is the revealed record `propose.py` writes at proposal time (`candidate_id`, `delta_in`, `conf`,
    `salt`, `hash`); `committed_hash` is the hash the ledger's `predict` row locked in before execution.
    """
    predicted_delta_in = float(sealed["delta_in"])
    predicted_conf = float(sealed["conf"])
    recomputed = hashlib.sha256(
        f"{sealed['candidate_id']}|{predicted_delta_in}|{predicted_conf}|{sealed['salt']}".encode()
    ).hexdigest()
    commitment_verified = recomputed == sealed["hash"] == committed_hash
    p_sign, o_sign = _sign(predicted_delta_in, zero_band), _sign(observed_delta_in, zero_band)
    sign_agrees = p_sign == o_sign
    if not commitment_verified:
        # The testimony on the table is not the one that was committed, so there is nothing established to
        # re-cognize -- unestablished (asiddha), not defeated: defeat requires a real, comparable claim.
        hetvabhasa: Hetvabhasa | None = Hetvabhasa.asiddha
    elif p_sign == 0 and o_sign != 0:
        # A flat testimony asserted no direction; a directional execution cannot defeat a claim never made.
        hetvabhasa = Hetvabhasa.asiddha
    elif not sign_agrees:
        hetvabhasa = Hetvabhasa.badhita
    else:
        hetvabhasa = None
    return Recognition(
        commitment_verified=commitment_verified,
        sign_agrees=sign_agrees,
        recognized=hetvabhasa is None,
        hetvabhasa=hetvabhasa.value if hetvabhasa is not None else None,
        predicted_delta_in=predicted_delta_in,
        predicted_conf=predicted_conf,
        observed_delta_in=float(observed_delta_in),
    )


def _load_sealed(sealed_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if sealed_dir.exists():
        for p in sorted(sealed_dir.glob("*.jsonl")):
            for line in p.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    out[r["candidate_id"]] = r
    return out


def _find_predict(ledger_path: Path, candidate_id: str) -> LedgerEvent:
    found: LedgerEvent | None = None
    for ev in iter_events(ledger_path):
        if ev.kind == "predict" and ev.candidate_id == candidate_id:
            found = ev  # propose.py seals one prediction per candidate; the last one written is the live one
    if found is None:
        raise LookupError(f"no predict row for {candidate_id!r} in {ledger_path}")
    return found


def _find_observe(ledger_path: Path, candidate_id: str, observe_seq: int) -> LedgerEvent:
    for ev in iter_events(ledger_path):
        if ev.kind == "observe" and ev.seq == observe_seq:
            if ev.candidate_id != candidate_id:
                raise LookupError(f"observe row {observe_seq} belongs to {ev.candidate_id!r}, not {candidate_id!r}")
            return ev
    raise LookupError(f"no observe row at seq {observe_seq} in {ledger_path}")


def recognize(
    ledger_path: Path,
    sealed_dir: Path,
    w: LedgerWriter,
    *,
    candidate_id: str,
    observe_seq: int,
    epoch: int,
    night: int,
    zero_band: float = ZERO_BAND,
) -> LedgerEvent:
    """Re-cognize one candidate's testimony against one of its observations, and record the verdict.

    Writes a `reflect{kind: pratyabhijna}` row, provenance `pratyaksha`, always. When the testimony is not
    recognized it additionally writes the `sublate` row that defeats the `predict` row (bādha, ADR-0015's
    mechanism), naming that row's seq as `target_seq` -- it does not withdraw the observation itself, which was
    kernel-executed and stands regardless of what was testified about it.

    Refuses (raises `LookupError`) rather than skipping silently when the candidate has no predict row, no
    sealed record, or when `observe_seq` does not name that candidate's own observation: a re-cognition this
    module cannot perform is not evidence that the testimony held.
    """
    predict_row = _find_predict(ledger_path, candidate_id)
    observe_row = _find_observe(ledger_path, candidate_id, observe_seq)
    sealed = _load_sealed(sealed_dir).get(candidate_id)
    if sealed is None:
        raise LookupError(f"no sealed prediction for {candidate_id!r} in {sealed_dir}")
    observed_delta_in = float(observe_row.payload["observed"]["delta_in"])
    verdict = recognize_testimony(sealed, str(predict_row.payload["hash"]), observed_delta_in, zero_band=zero_band)
    ev = w.append(
        "reflect",
        "auditor",
        {
            "kind": "pratyabhijna",
            "target_predict_seq": predict_row.seq,
            "target_observe_seq": observe_row.seq,
            "commitment_verified": verdict.commitment_verified,
            "sign_agrees": verdict.sign_agrees,
            "recognized": verdict.recognized,
            "hetvabhasa": verdict.hetvabhasa,
            "predicted_delta_in": verdict.predicted_delta_in,
            "predicted_conf": verdict.predicted_conf,
            "observed_delta_in": verdict.observed_delta_in,
        },
        epoch=epoch,
        night=night,
        candidate_id=candidate_id,
        surface=predict_row.surface,
        bucket=predict_row.bucket,
        provenance=Pramana.pratyaksha,
    )
    if not verdict.recognized:
        w.append(
            "sublate",
            "auditor",
            {
                "kind": "testimony_defeated",
                "target_seq": predict_row.seq,
                "reason": f"pratyabhijna: {verdict.hetvabhasa}",
            },
            epoch=epoch,
            night=night,
            candidate_id=candidate_id,
            surface=predict_row.surface,
            provenance=Pramana.pratyaksha,
        )
    return ev
