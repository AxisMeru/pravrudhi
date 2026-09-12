"""External scorer results enter the ledger by hash and render deterministically."""
import json
from pathlib import Path

from pravrudhi.application.external import parse_evalplus, parse_lm_eval, record_external, render_external
from pravrudhi_kernel.ledger import LedgerWriter


def _lm_eval(path: Path, acc: float) -> Path:
    path.write_text(json.dumps({
        "results": {"gsm8k": {"alias": "gsm8k", "exact_match,strict-match": acc, "exact_match_stderr,strict-match": 0.01}},
        "n-samples": {"gsm8k": {"original": 1319, "effective": 1319}}, "n-shot": {"gsm8k": 5},
        "lm_eval_version": "0.4.9", "transformers_version": "4.57", "config": {"model_args": "pretrained=x"},
    }))
    return path


def test_parse_lm_eval_trust_remote_code(tmp_path):
    """model_args carries trust_remote_code as a raw substring; the parsed row must surface
    it as its own explicit, typed field so a reader doesn't have to grep model_args to tell
    whether a run loaded custom code (scripts/ext_eval.sh's opt-in, off by default)."""
    on = tmp_path / "trc_true.json"
    on.write_text(json.dumps({
        "results": {"mmlu_pro_law": {"exact_match,custom-extract": 0.18}},
        "n-samples": {"mmlu_pro_law": {"original": 1101, "effective": 1101}}, "n-shot": {"mmlu_pro_law": 0},
        "lm_eval_version": "0.4.9", "transformers_version": "4.57",
        "config": {"model_args": "pretrained=x,dtype=bfloat16,trust_remote_code=True"},
    }))
    off = tmp_path / "trc_false.json"
    off.write_text(json.dumps({
        "results": {"mmlu_pro_law": {"exact_match,custom-extract": 0.18}},
        "n-samples": {"mmlu_pro_law": {"original": 1101, "effective": 1101}}, "n-shot": {"mmlu_pro_law": 0},
        "lm_eval_version": "0.4.9", "transformers_version": "4.57",
        "config": {"model_args": "pretrained=x,dtype=bfloat16,trust_remote_code=False"},
    }))
    assert parse_lm_eval(on)["trust_remote_code"] is True
    assert parse_lm_eval(off)["trust_remote_code"] is False


def test_parse_and_record(tmp_path):
    (tmp_path / "research").mkdir()
    LedgerWriter.open(tmp_path / "research" / "ledger.jsonl", "0.1.0")
    base = _lm_eval(tmp_path / "base.json", 0.40)
    after = _lm_eval(tmp_path / "after.json", 0.48)
    ep = tmp_path / "he.json"
    ep.write_text(json.dumps({"eval": {
        "HumanEval/0": [{"base_status": "pass", "plus_status": "pass"}],
        "HumanEval/1": [{"base_status": "pass", "plus_status": "fail"}],
        "HumanEval/2": [{"base_status": "fail", "plus_status": "fail"}],
    }}))
    assert parse_lm_eval(base)["metrics"]["gsm8k"]["exact_match,strict-match"] == 0.40
    p = parse_evalplus(ep, "humaneval")
    assert p["metrics"]["humaneval"] == {"pass@1_base": 2 / 3, "pass@1_plus": 1 / 3}
    r1 = record_external(tmp_path, base, tool="lm-eval", track="M", condition="base", model="m", night=4)
    r2 = record_external(tmp_path, after, tool="lm-eval", track="M", condition="adapter:c-1", model="m", night=4)
    record_external(tmp_path, ep, tool="evalplus", dataset="humaneval", track="H", condition="base", model="h", night=1)
    assert r1["tier"] == "external" and len(r1["sha256"]) == 64 and r2["seq"] == r1["seq"] + 1
    text = render_external(tmp_path / "research" / "ledger.jsonl")
    assert "adapter:c-1 − base = +0.0800" in text
    assert "humaneval+ pass@1 | 0.3333" in text
    assert text == render_external(tmp_path / "research" / "ledger.jsonl")


def test_render_external_shows_trust_remote_code(tmp_path):
    """A reader of the evidence document must see trust_remote_code without opening the
    ledger: rows that ran with it (custom model code, e.g. NemotronH) render 'yes', rows
    that explicitly ran without it render 'no', and rows from tools where the concept
    doesn't apply (evalplus has no such field at all) render '-' rather than a fabricated
    'no' - Sakshi: no claim the row's own payload doesn't actually carry."""
    (tmp_path / "research").mkdir()
    LedgerWriter.open(tmp_path / "research" / "ledger.jsonl", "0.1.0")
    on = tmp_path / "on.json"
    on.write_text(json.dumps({
        "results": {"mmlu_pro_law": {"exact_match,custom-extract": 0.18}},
        "n-samples": {"mmlu_pro_law": {"original": 1101, "effective": 1101}}, "n-shot": {"mmlu_pro_law": 0},
        "lm_eval_version": "0.4.9", "transformers_version": "4.57",
        "config": {"model_args": "pretrained=x,trust_remote_code=True"},
    }))
    off = _lm_eval(tmp_path / "off.json", 0.40)  # model_args "pretrained=x" -> explicit False
    ep = tmp_path / "he.json"
    ep.write_text(json.dumps({"eval": {
        "HumanEval/0": [{"base_status": "pass", "plus_status": "pass"}],
    }}))
    record_external(tmp_path, on, tool="lm-eval", track="B", condition="base", model="m", night=1)
    record_external(tmp_path, off, tool="lm-eval", track="B", condition="base", model="m", night=1)
    record_external(tmp_path, ep, tool="evalplus", dataset="humaneval", track="B", condition="base", model="m", night=1)
    text = render_external(tmp_path / "research" / "ledger.jsonl")
    assert "trust_remote_code" in text.splitlines()[4]  # header row
    rows = [line for line in text.splitlines() if line.startswith("|") and "---" not in line][1:]
    on_row = next(r for r in rows if "mmlu_pro_law" in r)
    off_row = next(r for r in rows if "gsm8k" in r)
    ep_row = next(r for r in rows if "humaneval+" in r)
    assert " | yes | " in on_row
    assert " | no | " in off_row
    assert " | - | " in ep_row
