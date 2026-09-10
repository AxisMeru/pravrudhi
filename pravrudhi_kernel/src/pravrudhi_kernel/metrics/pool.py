"""Sealed held-out pool with HMAC rotation draws and an exposure cap (06-evaluation-and-statistics.md §2).

Layout (kernel-owned, mode 0700):
  <pool_dir>/manifest.json   {bench, pool_version, n_items, item_hashes, source, answer_kind}
  <pool_dir>/items/<id>.json {id, question, answer}          mode 0600 — the agent never lists this directory
  <pool_dir>/exposure.json   {item_id: [rotation_id, ...]}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from pravrudhi_kernel.ledger.jcs import canonicalize
from pravrudhi_kernel.schema.common import KernelModel

# How a pool's answers are read, and therefore which scorer may score it (ADR-0035). Sorted, because
# `metrics/__init__` asserts this tuple equals its scorer table: a kind with no scorer seals pools nothing can
# score, and a scorer with no kind is unreachable.
ANSWER_KINDS: tuple[str, ...] = ("choice", "numeric", "set", "text")
DEFAULT_ANSWER_KIND = "numeric"


class PoolExhausted(RuntimeError):
    pass


class Rotation(KernelModel):
    bench: str
    rotation_id: str
    item_ids: list[str]
    seed_commit: str
    night: int
    candidate_id: str


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def seal_pool(
    pool_dir: Path,
    bench: str,
    rows: Iterable[Mapping[str, Any]],
    source: Mapping[str, Any],
    *,
    answer_kind: str = DEFAULT_ANSWER_KIND,
) -> dict[str, Any]:
    """Write the pool once. Returns the manifest. Refuses to overwrite an existing manifest."""
    pool_dir = Path(pool_dir)
    if answer_kind not in ANSWER_KINDS:
        raise ValueError(f"unknown answer kind {answer_kind!r}; known kinds are {', '.join(ANSWER_KINDS)}")
    manifest_path = pool_dir / "manifest.json"
    if manifest_path.exists():
        raise FileExistsError(f"pool already sealed at {manifest_path}; refresh is an epoch-boundary act")
    items_dir = pool_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(pool_dir, 0o700)
    os.chmod(items_dir, 0o700)
    hashes: dict[str, str] = {}
    for i, row in enumerate(rows):
        item = {"id": f"{bench}-{i:05d}", "question": str(row["question"]), "answer": str(row["answer"])}
        blob = canonicalize(item).encode("utf-8")
        p = items_dir / f"{item['id']}.json"
        p.write_bytes(blob)
        os.chmod(p, 0o600)
        hashes[item["id"]] = _sha(blob)
    # `answer_kind` goes into the body before the version is computed, so the version commits to how the
    # items are read and not only to what they say: the same rows scored two ways are two different pools.
    body = {
        "bench": bench,
        "n_items": len(hashes),
        "item_hashes": hashes,
        "source": dict(source),
        "answer_kind": answer_kind,
    }
    body["pool_version"] = _sha(canonicalize(body).encode("utf-8"))
    manifest_path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")
    os.chmod(manifest_path, 0o600)
    (pool_dir / "exposure.json").write_text("{}\n")
    return body


def load_manifest(pool_dir: Path) -> dict[str, Any]:
    m: dict[str, Any] = json.loads((Path(pool_dir) / "manifest.json").read_text())
    return m


def answer_kind(pool_dir: Path) -> str:
    """The declared answer kind, defaulting to numeric when the key is absent.

    Absent means sealed before ADR-0035, when the engine had one scorer and it was numeric. The default is
    therefore the regression guarantee, not a guess: every pool this project has ever sealed resolves to the
    same scorer it was actually scored by.
    """
    return str(load_manifest(pool_dir).get("answer_kind") or DEFAULT_ANSWER_KIND)


def manifest_hash(pool_dir: Path) -> str:
    return _sha((Path(pool_dir) / "manifest.json").read_bytes())


def read_item(pool_dir: Path, item_id: str) -> dict[str, str]:
    p = Path(pool_dir) / "items" / f"{item_id}.json"
    blob = p.read_bytes()
    m = load_manifest(pool_dir)
    if _sha(blob) != m["item_hashes"][item_id]:
        raise ValueError(f"item {item_id} hash mismatch against manifest")
    d: dict[str, str] = json.loads(blob)
    return d


def stable_sample(eligible: list[str], k: int, seed: bytes) -> list[str]:
    """Deterministic k-subset: sort by HMAC(seed, id); no RNG state, reproducible by the kernel for audit."""
    keyed = sorted(eligible, key=lambda i: hmac.new(seed, i.encode(), hashlib.sha256).digest())
    return sorted(keyed[:k])


def draw_rotation(pool_dir: Path, night: int, candidate_id: str, secret: bytes, *, k: int, exposure_cap: int) -> Rotation:
    m = load_manifest(pool_dir)
    exposure: dict[str, list[str]] = json.loads((Path(pool_dir) / "exposure.json").read_text())
    seed = hmac.new(secret, f"{m['bench']}|{night}|{candidate_id}".encode(), hashlib.sha256).digest()
    eligible = [i for i in m["item_hashes"] if len(exposure.get(i, [])) < exposure_cap]
    if len(eligible) < k:
        raise PoolExhausted(f"{m['bench']}: {len(eligible)} eligible < draw {k}; refresh the pool at an epoch boundary")
    items = stable_sample(eligible, k, seed)
    rid = _sha("|".join(items).encode())[:16]
    return Rotation(
        bench=m["bench"],
        rotation_id=rid,
        item_ids=items,
        seed_commit=_sha(seed),
        night=night,
        candidate_id=candidate_id,
    )


def record_exposure(pool_dir: Path, rot: Rotation) -> None:
    p = Path(pool_dir) / "exposure.json"
    exposure: dict[str, list[str]] = json.loads(p.read_text())
    for i in rot.item_ids:
        exposure.setdefault(i, []).append(rot.rotation_id)
    p.write_text(json.dumps(exposure, sort_keys=True) + "\n")


def overlap(a: Rotation, b: Rotation) -> float:
    sa, sb = set(a.item_ids), set(b.item_ids)
    return len(sa & sb) / max(1, len(sa))
