"""Observation admission: five hashes verified, then `spend` + `observe` rows via the writer. Nothing else
writes them."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pravrudhi_kernel.ledger import LedgerWriter
from pravrudhi_kernel.schema import LedgerEvent
from pravrudhi_kernel.schema.common import KernelModel


class HashMismatch(RuntimeError):
    pass


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with Path(p).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path) -> str:
    """Hash of a directory: sorted relative paths and file hashes; empty or missing directory hashes as
    'empty'."""
    root = Path(root)
    if not root.exists():
        return hashlib.sha256(b"empty").hexdigest()
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(str(p.relative_to(root)).encode())
        h.update(sha256_file(p).encode())
    return h.hexdigest()


def model_dir_hash(model_dir: Path) -> str:
    """Hash over the weight files only (safetensors + config + tokenizer json); the same rule runs inside
    the job."""
    h = hashlib.sha256()
    for p in sorted(Path(model_dir).glob("*")):
        if p.is_file() and (p.suffix in {".safetensors", ".json"} or p.name.endswith(".model")):
            h.update(p.name.encode())
            h.update(sha256_file(p).encode())
    return h.hexdigest()


class KernelHashes(KernelModel):
    items: str
    manifest: str
    scorer: str
    harness: str
    model: str


def kernel_hashes(items_file: Path, manifest_path: Path, scorer_source: Path, harness_dir: Path, model_dir: Path) -> KernelHashes:
    return KernelHashes(
        items=sha256_file(items_file),
        manifest=sha256_file(manifest_path),
        scorer=sha256_file(scorer_source),
        harness=sha256_tree(harness_dir),
        model=model_dir_hash(model_dir),
    )


def admit_observation(
    writer: LedgerWriter,
    *,
    expected: KernelHashes,
    job_meta: dict[str, Any],
    per_item_scores: Mapping[str, float],
    per_item_ref: str,
    run_id: str,
    candidate_id: str,
    surface: str,
    bucket: dict[str, str],
    epoch: int,
    night: int,
    cycle: int | None,
    seed: int,
    rotation_id: str,
    value_ref: float | None,
    cost_gpu_h: float,
    wall_s: float,
    peak_gib: float | None,
    isolation: str,
    stage: str,
    extra: dict[str, Any] | None = None,
) -> tuple[LedgerEvent, LedgerEvent]:
    """Verify that what the job saw is what the kernel sealed; then write spend + observe. Raises
    HashMismatch."""
    seen = {"items": job_meta.get("items_sha256"), "model": job_meta.get("model_sha256")}
    for k, v in seen.items():
        if v != getattr(expected, k):
            raise HashMismatch(f"{k}: job saw {v}, kernel expected {getattr(expected, k)}")
    n = len(per_item_scores)
    value = sum(per_item_scores.values()) / n if n else 0.0
    spend = writer.append(
        "spend",
        "executor",
        {
            "gpu_h": cost_gpu_h,
            "wall_s": wall_s,
            "peak_gib": peak_gib,
            "run_id": run_id,
            "phase": "execute",
            "tokens_out": job_meta.get("tokens_generated"),
            "tok_s": job_meta.get("tok_s"),
        },
        epoch=epoch,
        night=night,
        cycle=cycle,
        candidate_id=candidate_id,
        surface=surface,
    )
    observed = {
        "metric": "pass_rate",
        "value": value,
        "n_items": n,
        "seeds": [seed],
        "delta_in": (value - value_ref) if value_ref is not None else 0.0,
        "value_ref": value_ref,
        "per_item_scores_ref": per_item_ref,
        "cost_gpu_h": cost_gpu_h,
    }
    payload: dict[str, Any] = {
        "run_id": run_id,
        "stage": stage,
        "seed_index": seed,
        "observed": observed,
        "hashes": expected.model_dump(),
        "rotation_id": rotation_id,
        "isolation": isolation,
        "measure_class": "model-measured",
        "job": {
            k: job_meta.get(k)
            for k in (
                "model",
                "dtype",
                "temperature",
                "max_new_tokens",
                "template",
                "tokens_generated",
                "tok_s",
                "peak_gib_torch",
                "wall_s",
            )
        },
    }
    if extra:
        payload.update(extra)
    obs = writer.append(
        "observe",
        "kernel",
        payload,
        epoch=epoch,
        night=night,
        cycle=cycle,
        candidate_id=candidate_id,
        surface=surface,
        bucket=bucket,
        provenance="pratyaksha",
    )
    return spend, obs
