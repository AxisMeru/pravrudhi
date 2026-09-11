"""pratyabhijñā re-cognizes a candidate's sealed testimony (āgama) by its kernel-executed observation
(pratyakṣa): does the revealed prediction match what was hash-committed before execution, and does its claimed
direction agree with what the kernel measured? The verdict is written as its own pramāṇa-tagged ledger row, and
a defeated testimony is sublated (bādha) exactly like any other superseded claim -- CHARTER.md §1."""

import hashlib
import json
from pathlib import Path

import pytest

from pravrudhi.application.pratyabhijna import ZERO_BAND, recognize, recognize_testimony
from pravrudhi_kernel.ledger import LedgerWriter
from pravrudhi_kernel.ledger.verify import iter_events

BUCKET = {"task_family": "gsm8k", "target_model": "m", "corpus": "gsm8k-train"}


def _sealed(cid: str, delta_in: float, conf: float, salt: str = "abc123") -> dict:
    h = hashlib.sha256(f"{cid}|{delta_in}|{conf}|{salt}".encode()).hexdigest()
    return {"candidate_id": cid, "delta_in": delta_in, "conf": conf, "salt": salt, "hash": h, "night": 1}


class TestRecognizeTestimonyPure:
    """The comparison itself, with no ledger involved."""

    def test_agreeing_sign_and_verified_commitment_is_recognized(self):
        sealed = _sealed("c-0001", 0.05, 0.7)
        verdict = recognize_testimony(sealed, sealed["hash"], observed_delta_in=0.02)
        assert verdict.recognized
        assert verdict.commitment_verified
        assert verdict.sign_agrees
        assert verdict.hetvabhasa is None

    def test_opposing_sign_is_defeated_badhita(self):
        sealed = _sealed("c-0002", 0.05, 0.7)
        verdict = recognize_testimony(sealed, sealed["hash"], observed_delta_in=-0.02)
        assert not verdict.recognized
        assert verdict.commitment_verified
        assert not verdict.sign_agrees
        assert verdict.hetvabhasa == "badhita"

    def test_tampered_sealed_record_is_asiddha_even_if_sign_would_have_agreed(self):
        sealed = _sealed("c-0003", 0.05, 0.7)
        tampered = {**sealed, "delta_in": 0.20}  # revealed value no longer matches what was committed
        verdict = recognize_testimony(tampered, sealed["hash"], observed_delta_in=0.02)
        assert not verdict.recognized
        assert not verdict.commitment_verified
        assert verdict.hetvabhasa == "asiddha"

    def test_flat_testimony_against_a_real_direction_is_asiddha_not_badhita(self):
        """A prediction inside the zero band asserted no direction; a directional execution cannot defeat a claim
        that was never made, so this is unestablished rather than contradicted."""
        sealed = _sealed("c-0004", ZERO_BAND / 2, 0.5)
        verdict = recognize_testimony(sealed, sealed["hash"], observed_delta_in=-0.03)
        assert not verdict.recognized
        assert verdict.commitment_verified
        assert verdict.hetvabhasa == "asiddha"

    def test_both_flat_is_recognized(self):
        sealed = _sealed("c-0005", ZERO_BAND / 2, 0.5)
        verdict = recognize_testimony(sealed, sealed["hash"], observed_delta_in=ZERO_BAND / 3)
        assert verdict.recognized
        assert verdict.hetvabhasa is None


def _seed_ledger(root: Path, *, cid: str, sealed_hash: str, observed_delta_in: float) -> tuple[LedgerWriter, int]:
    (root / "research").mkdir()
    w = LedgerWriter.open(root / "research" / "ledger.jsonl", "0.1.0")
    w.append(
        "propose", "proposer", {"op": "adapter", "recipe": {}}, epoch=0, night=1, candidate_id=cid,
        surface="W3.adapter", bucket=BUCKET, provenance="agama",
    )
    w.append(
        "predict", "proposer", {"predictor": "v1", "hash": sealed_hash}, epoch=0, night=1, candidate_id=cid,
        surface="W3.adapter", bucket=BUCKET, provenance="agama",
    )
    obs = w.append(
        "observe", "kernel",
        {
            "arm": "candidate", "stage": "screen",
            "observed": {
                "metric": "pass_rate", "value": 0.5, "n_items": 100, "seeds": [0],
                "delta_in": observed_delta_in, "value_ref": 0.5,
            },
            "hashes": {},
        },
        epoch=0, night=1, candidate_id=cid, surface="W3.adapter", bucket=BUCKET, provenance="pratyaksha",
    )
    return w, obs.seq


def _write_sealed_file(sealed_dir: Path, record: dict) -> None:
    sealed_dir.mkdir(parents=True)
    (sealed_dir / "night_1.jsonl").write_text(json.dumps(record) + "\n")


class TestRecognizeLedgerIntegration:
    def test_recognized_testimony_writes_a_pratyaksha_tagged_reflect_row_only(self, tmp_path):
        sealed = _sealed("c-0010", 0.05, 0.7)
        w, obs_seq = _seed_ledger(tmp_path, cid="c-0010", sealed_hash=sealed["hash"], observed_delta_in=0.03)
        _write_sealed_file(tmp_path / "sealed", sealed)
        ledger = tmp_path / "research" / "ledger.jsonl"

        ev = recognize(ledger, tmp_path / "sealed", w, candidate_id="c-0010", observe_seq=obs_seq, epoch=0, night=1)

        assert ev.kind == "reflect" and ev.provenance == "pratyaksha" and ev.candidate_id == "c-0010"
        assert ev.payload["kind"] == "pratyabhijna"
        assert ev.payload["recognized"] is True
        assert ev.payload["hetvabhasa"] is None
        rows = list(iter_events(ledger))
        assert [r.kind for r in rows] == ["audit", "propose", "predict", "observe", "reflect"]

    def test_defeated_testimony_also_sublates_the_predict_row(self, tmp_path):
        sealed = _sealed("c-0011", 0.05, 0.7)
        w, obs_seq = _seed_ledger(tmp_path, cid="c-0011", sealed_hash=sealed["hash"], observed_delta_in=-0.05)
        _write_sealed_file(tmp_path / "sealed", sealed)
        ledger = tmp_path / "research" / "ledger.jsonl"
        predict_seq = next(r.seq for r in iter_events(ledger) if r.kind == "predict")

        ev = recognize(ledger, tmp_path / "sealed", w, candidate_id="c-0011", observe_seq=obs_seq, epoch=0, night=1)

        assert ev.payload["recognized"] is False and ev.payload["hetvabhasa"] == "badhita"
        rows = list(iter_events(ledger))
        sublations = [r for r in rows if r.kind == "sublate"]
        assert len(sublations) == 1
        assert sublations[0].payload["kind"] == "testimony_defeated"
        assert sublations[0].payload["target_seq"] == predict_seq
        assert sublations[0].provenance == "pratyaksha"

    def test_missing_sealed_prediction_is_refused_not_silently_skipped(self, tmp_path):
        w, obs_seq = _seed_ledger(tmp_path, cid="c-0012", sealed_hash="0" * 64, observed_delta_in=0.03)
        ledger = tmp_path / "research" / "ledger.jsonl"
        with pytest.raises(LookupError):
            recognize(ledger, tmp_path / "sealed", w, candidate_id="c-0012", observe_seq=obs_seq, epoch=0, night=1)
