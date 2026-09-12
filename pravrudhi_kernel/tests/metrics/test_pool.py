import json
import os
import sys
from pathlib import Path

import pytest

from pravrudhi_kernel.metrics import PoolExhausted, draw_rotation, record_exposure, seal_pool
from pravrudhi_kernel.metrics.pool import load_manifest, manifest_hash, overlap, read_item, stable_sample

ROWS = [{"question": f"q{i}", "answer": f"steps\n#### {i}"} for i in range(40)]
SECRET = b"s" * 32


@pytest.fixture
def pool(tmp_path: Path) -> Path:
    p = tmp_path / "pool"
    seal_pool(p, "gsm8k-test", ROWS, {"file": "x", "sha256": "y"})
    return p


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits; see ADR-0052 Windows note")
def test_seal_writes_hashed_items_with_private_modes(pool: Path) -> None:
    m = load_manifest(pool)
    assert m["n_items"] == 40 and len(m["item_hashes"]) == 40 and len(m["pool_version"]) == 64
    assert oct(os.stat(pool).st_mode & 0o777) == "0o700"
    item = pool / "items" / "gsm8k-test-00003.json"
    assert oct(os.stat(item).st_mode & 0o777) == "0o600"
    assert read_item(pool, "gsm8k-test-00003")["answer"].endswith("#### 3")
    with pytest.raises(FileExistsError):
        seal_pool(pool, "gsm8k-test", ROWS, {})


def test_item_tamper_detected(pool: Path) -> None:
    item = pool / "items" / "gsm8k-test-00003.json"
    d = json.loads(item.read_text())
    d["answer"] = "#### 99"
    item.write_text(json.dumps(d))
    with pytest.raises(ValueError):
        read_item(pool, "gsm8k-test-00003")


def test_rotation_is_deterministic_secret_dependent_and_capped(pool: Path) -> None:
    a = draw_rotation(pool, 1, "c-0001", SECRET, k=10, exposure_cap=2)
    b = draw_rotation(pool, 1, "c-0001", SECRET, k=10, exposure_cap=2)
    c = draw_rotation(pool, 1, "c-0001", b"t" * 32, k=10, exposure_cap=2)
    d = draw_rotation(pool, 2, "c-0001", SECRET, k=10, exposure_cap=2)
    assert a == b and a.item_ids != c.item_ids and a.item_ids != d.item_ids
    assert len(a.item_ids) == 10 and a.item_ids == sorted(a.item_ids) and len(a.rotation_id) == 16
    record_exposure(pool, a)
    record_exposure(pool, a)
    e = draw_rotation(pool, 3, "c-0002", SECRET, k=10, exposure_cap=2)
    assert not set(e.item_ids) & set(a.item_ids)  # capped items are ineligible
    exhausted_at = None
    for n in range(4, 30):
        try:
            r = draw_rotation(pool, n, "c-0009", SECRET, k=10, exposure_cap=2)
        except PoolExhausted:
            exhausted_at = n
            break
        record_exposure(pool, r)
        record_exposure(pool, r)
    assert exhausted_at is not None  # 40 items x cap 2 = 80 slots; 20 slots per night -> exhausted by night ~7


def test_stable_sample_and_overlap(pool: Path) -> None:
    ids = [f"i{k}" for k in range(100)]
    s1 = stable_sample(ids, 10, b"a")
    assert s1 == stable_sample(ids, 10, b"a") and len(set(s1)) == 10 and s1 != stable_sample(ids, 10, b"b")
    a = draw_rotation(pool, 1, "c-0001", SECRET, k=20, exposure_cap=5)
    b = draw_rotation(pool, 1, "c-0002", SECRET, k=20, exposure_cap=5)
    assert 0.0 <= overlap(a, b) <= 1.0 and overlap(a, a) == 1.0
    assert len(manifest_hash(pool)) == 64
