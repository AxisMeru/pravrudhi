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


# --- #332: one row per result file; each candidate paired with its OWN (nearest earlier) base ---


def _evalplus(path: Path, passes: set[int], n: int = 10) -> Path:
    path.write_text(
        json.dumps(
            {
                "eval": {
                    f"HumanEval/{i}": [{"base_status": "pass", "plus_status": "pass" if i in passes else "fail"}]
                    for i in range(n)
                }
            }
        )
    )
    return path


def _ledger_with_two_nights(tmp_path):
    (tmp_path / "research").mkdir()
    ledger = tmp_path / "research" / "ledger.jsonl"
    LedgerWriter.open(ledger, "0.1.0")
    kw = dict(tool="evalplus", dataset="humaneval", track="H", model="h")
    a = _evalplus(tmp_path / "a.json", set(range(6)))  # night-1 base: 6/10
    b = _evalplus(tmp_path / "b.json", {0, 1})  # night-1 candidate: 2/10
    c = _evalplus(tmp_path / "c.json", set(range(7)))  # night-3 base: 7/10
    d = _evalplus(tmp_path / "d.json", set(range(9)))  # night-3 candidate: 9/10
    record_external(tmp_path, a, condition="base", night=1, **kw)
    record_external(tmp_path, b, condition="harness:c1", night=1, **kw)
    record_external(tmp_path, c, condition="base", night=3, **kw)
    record_external(tmp_path, d, condition="harness:combo", night=3, **kw)
    record_external(tmp_path, c, condition="base", night=3, **kw)  # the same file admitted again
    return ledger


def test_one_row_per_result_file(tmp_path):
    from pravrudhi.application.external import dedupe_external_rows, external_rows

    ledger = _ledger_with_two_nights(tmp_path)
    raw = external_rows(ledger)
    assert len(raw) == 5
    rows = dedupe_external_rows(raw)
    assert len(rows) == 4
    night3_base = next(r for r in rows if r["condition"] == "base" and r["night"] == 3)
    assert night3_base["seqs"] == [raw[2]["seq"], raw[4]["seq"]]
    text = render_external(ledger)
    assert f"| {raw[2]['seq']}/{raw[4]['seq']} | H | base |" in text
    assert sum(1 for line in text.splitlines() if line.startswith("|") and "| base |" in line) == 2  # not three


def test_per_item_vector_is_kept_from_whichever_duplicate_has_one():
    from pravrudhi.application.external import dedupe_external_rows

    base = {"seq": 1, "track": "H", "condition": "base", "model": "m", "sha256": "x" * 64}
    again = {**base, "seq": 5, "items": {"t": 1}}
    out = dedupe_external_rows([base, again])
    assert len(out) == 1 and out[0]["seqs"] == [1, 5] and out[0]["items"] == {"t": 1}
    # rows without a hash are never merged
    assert len(dedupe_external_rows([{**base, "sha256": None}, {**base, "sha256": None, "seq": 2}])) == 2


def test_each_candidate_is_paired_with_its_own_base_not_the_latest(tmp_path):
    ledger = _ledger_with_two_nights(tmp_path)
    text = render_external(ledger)
    pairs = [line for line in text.splitlines() if line.startswith("- H humaneval+")]
    c1 = next(line for line in pairs if "harness:c1" in line)
    combo = next(line for line in pairs if "harness:combo" in line)
    assert "harness:c1 − base = -0.4000" in c1 and "base 0.6000" in c1  # night-1 base 6/10, not the night-3 base
    assert "harness:combo − base = +0.2000" in combo and "base 0.7000" in combo  # night-3 base 7/10
    assert "paired: 2 harness:combo-only vs 0 base-only" in combo
    assert len(pairs) == 2


def test_a_candidate_before_any_base_is_not_paired(tmp_path):
    (tmp_path / "research").mkdir()
    ledger = tmp_path / "research" / "ledger.jsonl"
    LedgerWriter.open(ledger, "0.1.0")
    kw = dict(tool="evalplus", dataset="humaneval", track="H", model="h")
    record_external(tmp_path, _evalplus(tmp_path / "x.json", {0}), condition="harness:early", night=1, **kw)
    record_external(tmp_path, _evalplus(tmp_path / "y.json", {0, 1, 2}), condition="base", night=2, **kw)
    text = render_external(ledger)
    assert not any(line.startswith("- H humaneval+") and "harness:early" in line for line in text.splitlines())


def test_paper_paired_table_uses_the_own_base(tmp_path):
    from pravrudhi.application import paper_data

    ledger = _ledger_with_two_nights(tmp_path)
    table = paper_data._paired_table(ledger)
    assert "harness:combo & 2 & 0 &" in table  # wins/losses against the night-3 base, from per-item vectors
    assert "harness:c1 & 0 & 4 &" in table  # night-1 candidate against the night-1 base: 4 base-only
    assert paper_data._external_table(ledger).count("harness:combo") == 1


def test_bases_are_keyed_by_track_model_and_metric(tmp_path):
    """R2's probe: base of model A (seq 1), base of model B (seq 2), candidate of model A (seq 3).
    The candidate pairs with ITS model's base."""
    (tmp_path / "research").mkdir()
    ledger = tmp_path / "research" / "ledger.jsonl"
    LedgerWriter.open(ledger, "0.1.0")
    kw = dict(tool="evalplus", dataset="humaneval", track="H")
    record_external(tmp_path, _evalplus(tmp_path / "a0.json", {0, 1}), condition="base", model="A", night=1, **kw)
    record_external(tmp_path, _evalplus(tmp_path / "b0.json", set(range(9))), condition="base", model="B", night=1, **kw)
    record_external(tmp_path, _evalplus(tmp_path / "a1.json", set(range(4))), condition="harness:x", model="A", night=1, **kw)
    text = render_external(ledger)
    line = next(line for line in text.splitlines() if line.startswith("- H humaneval+") and "harness:x" in line)
    assert "harness:x − base = +0.2000" in line and "base 0.2000" in line  # model A's base 2/10, not model B's 9/10
    assert "paired: 2 harness:x-only vs 0 base-only" in line


def test_a_tiny_exact_p_is_never_printed_as_zero(tmp_path):
    from pravrudhi.application import paper_data
    from pravrudhi.application.external import format_p

    assert format_p(0.00001) == "< 0.001" and format_p(0.0009) == "< 0.001"
    assert format_p(0.001) == "= 0.001" and format_p(0.1338) == "= 0.134"
    (tmp_path / "research").mkdir()
    ledger = tmp_path / "research" / "ledger.jsonl"
    LedgerWriter.open(ledger, "0.1.0")
    kw = dict(tool="evalplus", dataset="humaneval", track="H", model="m", night=1)
    record_external(tmp_path, _evalplus(tmp_path / "b.json", set(), n=40), condition="base", **kw)
    record_external(tmp_path, _evalplus(tmp_path / "c.json", set(range(40)), n=40), condition="harness:y", **kw)
    assert "exact McNemar p < 0.001" in render_external(ledger)
    assert "$<$0.0001" in paper_data._paired_table(ledger) and "0.0000" not in paper_data._paired_table(ledger)


def test_non_binary_per_item_scores_do_not_break_the_render(tmp_path):
    from pravrudhi.application.external import paired_with_own_base  # noqa: F401  (the render path under test uses it)

    (tmp_path / "research").mkdir()
    ledger = tmp_path / "research" / "ledger.jsonl"
    LedgerWriter.open(ledger, "0.1.0")
    kw = dict(tool="evalplus", dataset="humaneval", track="H", model="m", night=1)
    record_external(tmp_path, _evalplus(tmp_path / "p.json", {0}), condition="base", **kw)
    record_external(tmp_path, _evalplus(tmp_path / "q.json", {0, 1}), condition="harness:z", **kw)
    import pravrudhi.application.external as ext

    orig = ext.external_rows

    def with_bad_items(path):
        rows = orig(path)
        for r in rows:
            r["items"] = {k: 0.5 for k in (r.get("items") or {})}  # non-binary scores
        return rows

    ext.external_rows = with_bad_items
    try:
        text = render_external(ledger)
    finally:
        ext.external_rows = orig
    line = next(line for line in text.splitlines() if "harness:z − base" in line)
    assert "paired:" not in line
