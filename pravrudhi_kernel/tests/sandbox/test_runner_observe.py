import json
import sys
from pathlib import Path

import pytest

from pravrudhi_kernel.ledger import LedgerWriter, verify
from pravrudhi_kernel.sandbox import (
    HashMismatch,
    JobSpec,
    admit_observation,
    ensure_kernel_state,
    kernel_hashes,
    run_job,
)
from pravrudhi_kernel.sandbox.observe import model_dir_hash, sha256_tree
from pravrudhi_kernel.sandbox.runner import docker_available

needs_docker = pytest.mark.skipif(not docker_available(), reason="docker not available")
windows_incompatible = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits / docker --memory-swap; see ADR-0052 Windows note"
)


@windows_incompatible
def test_kernel_state_dir_is_private(tmp_path: Path) -> None:
    st = ensure_kernel_state(tmp_path, docker_available=False)
    assert st.isolation == "process"
    assert (tmp_path / ".pravrudhi" / "kernel" / "secret").stat().st_mode & 0o777 == 0o600
    st2 = ensure_kernel_state(tmp_path, docker_available=True)
    assert st2.isolation == "container" and Path(st2.secret_path).read_bytes() == Path(st.secret_path).read_bytes()


@needs_docker
@windows_incompatible
def test_run_job_enforces_read_only_mount_and_no_network(tmp_path: Path) -> None:
    ro = tmp_path / "ro"
    ro.mkdir()
    (ro / "f.txt").write_text("hello")
    out = tmp_path / "out"
    spec = JobSpec(
        image="alpine:latest",
        command=[
            "sh",
            "-c",
            "cat /in/f.txt > /out/copy.txt; (echo x > /in/f.txt) 2>/dev/null && echo WROTE || echo RO; "
            "(wget -q -T 2 -O /dev/null http://example.com) 2>/dev/null && echo NET || echo NONET",
        ],
        mounts_ro={str(ro): "/in"},
        output_dir=str(out),
        gpu=False,
        network=False,
        timeout_s=60,
    )
    r = run_job(spec)
    assert r.exit_code == 0 and (out / "copy.txt").read_text() == "hello"
    assert "RO" in r.stdout_tail and "NONET" in r.stdout_tail and r.wall_s > 0 and not r.timed_out


@needs_docker
@windows_incompatible
def test_run_job_timeout(tmp_path: Path) -> None:
    r = run_job(JobSpec(image="alpine:latest", command=["sleep", "5"], output_dir=str(tmp_path / "o"), timeout_s=1))
    assert r.timed_out and r.exit_code == 124


def _setup(tmp_path: Path) -> tuple[LedgerWriter, dict, Path]:
    items = tmp_path / "items.jsonl"
    items.write_text('{"id":"a","question":"q"}\n')
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    scorer = tmp_path / "scorer.py"
    scorer.write_text("x")
    harness = tmp_path / "harness"
    model = tmp_path / "model"
    model.mkdir()
    (model / "model.safetensors").write_bytes(b"weights")
    (model / "config.json").write_text("{}")
    (model / "README.md").write_text("ignored")
    exp = kernel_hashes(items, manifest, scorer, harness, model)
    assert exp.harness == sha256_tree(harness) and exp.model == model_dir_hash(model)
    w = LedgerWriter.open(tmp_path / "ledger.jsonl", "0.1.0")
    w.append(
        "propose",
        "proposer",
        {"op": "baseline"},
        epoch=0,
        night=1,
        cycle=1,
        candidate_id="c-0000",
        surface="W3.adapter",
        bucket={"task_family": "t", "target_model": "m", "corpus": "c"},
        provenance="agama",
    )
    meta = {"items_sha256": exp.items, "model_sha256": exp.model, "tokens_generated": 10, "tok_s": 5.0}
    return w, {"expected": exp, "meta": meta}, tmp_path


def _admit(w: LedgerWriter, exp, meta) -> tuple:
    return admit_observation(
        w,
        expected=exp,
        job_meta=meta,
        per_item_scores={"a": 1, "b": 0},
        per_item_ref="x.jsonl",
        run_id="r1",
        candidate_id="c-0000",
        surface="W3.adapter",
        bucket={"task_family": "t", "target_model": "m", "corpus": "c"},
        epoch=0,
        night=1,
        cycle=1,
        seed=0,
        rotation_id="rot",
        value_ref=None,
        cost_gpu_h=0.01,
        wall_s=3.0,
        peak_gib=9.1,
        isolation="container",
        stage="smoke",
    )


def test_admit_observation_writes_spend_and_observe(tmp_path: Path) -> None:
    w, d, root = _setup(tmp_path)
    spend, obs = _admit(w, d["expected"], d["meta"])
    assert spend.kind == "spend" and obs.kind == "observe" and obs.actor == "kernel" and obs.provenance == "pratyaksha"
    assert obs.payload["observed"]["value"] == 0.5 and obs.payload["hashes"]["model"] == d["expected"].model
    assert verify(root / "ledger.jsonl").ok


def test_admit_observation_refuses_hash_mismatch_and_leaves_ledger_untouched(tmp_path: Path) -> None:
    w, d, root = _setup(tmp_path)
    before = (root / "ledger.jsonl").read_bytes()
    bad = dict(d["meta"], model_sha256="f" * 64)
    with pytest.raises(HashMismatch):
        _admit(w, d["expected"], bad)
    assert (root / "ledger.jsonl").read_bytes() == before
    bad2 = dict(d["meta"], items_sha256="0" * 64)
    with pytest.raises(HashMismatch):
        _admit(w, d["expected"], bad2)
    assert json.loads((root / "ledger.jsonl.head").read_text())["seq"] == 1
