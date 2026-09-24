"""T2 harness-level false-prove check, phase 2: assembly + Lean check (Lead-2, 2026-09-24).

CONSTRUCTED, regression-only, an additional read of the already-frozen eval set (same convention as phase
1). No live model calls -- reads phase 1's already-sealed, R2-signed raw p-values (sha256 f9be2b3e...) and
runs them through the SAME assembler/Lean-checker code path the existing config A/B/C re-measure used
(`run_ws_b_dual_threshold.judgments_at_threshold`/`classify`, `element_specs_for_contract`,
`load_and_verify_eval_set`, pinned score binary sha256 29f6eaed...), under CONFIG A's exact threshold rule
(4B-alone @0.74 -- T2 only compares the 4B/product path, config B/C are not needed here).

Two "configs" run through the SAME code: `free_arm` (my own live free_text p, phase 1) and `typed_arm` (my
own live typed p, phase 1) -- both labelled "pre-fix, F_narrative duplicated" per Lead-2's fix diagnosis
(build_house_prompt double-counted the narrative; both arms hit the identical bug, so free-vs-typed stays a
valid comparison even though neither is the corrected product-path number yet).

Cross-repo dependency (a scratch measurement script outside src/, not product code -- see RUN-METADATA):
`run_ws_b_dual_threshold` from the graph-fix WS-B worktree (sha256 0aff19a1..., commit ab7151fa, see
PROVENANCE-NOTE.md for why this substituted copy is accepted), `nyaya_ir` and `prabhasa_nyaya.*` from the
same worktree / the prabhasa-nyaya checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from _t2_run_metadata import RunMetadata  # noqa: E402

from pravrudhi.application.nyaya_judges import p_established_from_top_logprobs  # noqa: E402
from pravrudhi.application.typed.decoder import score_decision  # noqa: E402
from pravrudhi.application.typed.house_judge import _STATUS_FIELD  # noqa: E402
from pravrudhi.models.openai_compat import CompletionResult  # noqa: E402


def _nvidia_smi() -> str:
    return subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader"],
        capture_output=True, text=True,
    ).stdout.strip()

HARNESS_RAW_SHA256 = "f9be2b3e32dac264bedec48fcf33ce47e71c45c745d20e4ea111e172536d7d16"
T0 = 0.74  # config A's threshold, the only one T2 needs (4B/product path)


def main() -> int:
    results_dir_env = os.environ.get("PRAVRUDHI_T2_RESULTS_DIR")
    if not results_dir_env:
        print("REFUSING: PRAVRUDHI_T2_RESULTS_DIR is not set (results never write inside the repo)", file=sys.stderr)
        return 2
    results_dir = Path(results_dir_env)

    graph_fix_env = os.environ.get("PRAVRUDHI_T2_GRAPH_FIX_WT")
    score_bin_env = os.environ.get("PRABHASA_NYAYA_SCORE_BIN")
    eval_items_env = os.environ.get("PRAVRUDHI_T2_EVAL_ITEMS")
    for name, val in [
        ("PRAVRUDHI_T2_GRAPH_FIX_WT", graph_fix_env),
        ("PRABHASA_NYAYA_SCORE_BIN", score_bin_env), ("PRAVRUDHI_T2_EVAL_ITEMS", eval_items_env),
    ]:
        if not val:
            print(f"REFUSING: {name} is not set (no host-path default)", file=sys.stderr)
            return 2

    wt = Path(graph_fix_env)  # type: ignore[arg-type]
    # wt/src already contains prabhasa_nyaya and nyaya_ir -- the graph-fix worktree carries its own copy,
    # confirmed by direct inspection; no separate prabhasa-nyaya checkout path is needed for imports.
    sys.path.insert(0, str(wt / "src"))
    sys.path.insert(0, str(wt / "scripts"))
    sys.path.insert(0, str(wt / "research/preflight/2026-09-22-ws-b-element-first"))

    # run_ws_b_dual_threshold.py imports torch/transformers at module level, used ONLY inside load_judge()/
    # run_partition() (confirmed by grep: neither name appears outside a function body) -- functions this
    # script never calls (it uses judgments_at_threshold/classify directly on already-computed p-values, no
    # model loading). A syntactic stub satisfies the import without installing a multi-GB ML stack for two
    # pure-Python functions. Written outside the repo, never committed.
    stub_dir = results_dir.parent / "_t2_torch_stubs"
    stub_dir.mkdir(parents=True, exist_ok=True)
    (stub_dir / "torch.py").write_text(
        "# stub -- run_ws_b_dual_threshold.py's torch usage is confined to functions this script never calls\n"
        "bfloat16 = None\n\n\nclass cuda:\n    @staticmethod\n    def empty_cache() -> None: ...\n"
        "    @staticmethod\n    def memory_allocated() -> int: return 0\n"
    )
    (stub_dir / "transformers.py").write_text(
        "# stub -- see torch.py stub in this directory for why\n"
        "class AutoModelForCausalLM:\n    pass\n\n\nclass AutoTokenizer:\n    pass\n"
    )
    sys.path.insert(0, str(stub_dir))

    import run_ws_b_dual_threshold as D  # type: ignore[import-not-found]
    from nyaya_ir import from_dict  # type: ignore[import-not-found]
    from nyaya_ir.codec import ParseError  # type: ignore[import-not-found]
    from nyaya_ir.validators import ValidationError  # type: ignore[import-not-found]
    from prabhasa_nyaya.element_first_harness import element_specs_for_contract  # type: ignore[import-not-found]
    from prabhasa_nyaya.p2b_preflight import load_and_verify_eval_set  # type: ignore[import-not-found]
    from prabhasa_nyaya.p2b_registry import build_p2b_scoring_registry  # type: ignore[import-not-found]

    score_bin = Path(score_bin_env)  # type: ignore[arg-type]
    sha = hashlib.sha256(score_bin.read_bytes()).hexdigest()
    if sha != "29f6eaed3ef5c548d6c8a1cdf884c9a73895937132779ea4d4cffb8e66cb80ae":
        print(f"REFUSING: score binary sha256 {sha} != pinned 29f6eaed...", file=sys.stderr)
        return 2
    D.SCORE_BIN = score_bin
    D.PINNED_SCORE_BINARY_SHA256 = sha

    meta = RunMetadata(
        script_path=Path(__file__), base_url="N/A (no live model calls in phase 2)", container_name=None,
        concurrency_description="N/A (CPU assembly + Lean-binary subprocess calls only, no backend served)",
        delay_s=0.0,
    )
    meta.data["start_utc"] = datetime.now(UTC).isoformat()
    meta.data["score_bin_path"] = str(score_bin)
    meta.data["score_bin_sha256"] = sha
    meta.data["ws_b_worktree"] = str(wt)
    meta.data["prabhasa_nyaya_path"] = f"{wt / 'src'} (graph-fix worktree's own copy, no separate checkout used)"
    meta.data["harness_raw_input_sha256"] = HARNESS_RAW_SHA256
    meta.data["nvidia_smi_before"] = _nvidia_smi()

    # -- load phase 1's sealed, R2-signed raw p-values ---------------------------------------------------
    harness_raw_path = results_dir / "t2_harness_raw_outputs.jsonl"
    raw_bytes = harness_raw_path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != HARNESS_RAW_SHA256:
        print(f"REFUSING: {harness_raw_path} sha256 {digest} != R2-signed {HARNESS_RAW_SHA256}", file=sys.stderr)
        return 2
    raw_rows = [json.loads(line) for line in raw_bytes.decode().splitlines() if line.strip()]

    p_by_key: dict[tuple[str, str], tuple[float | None, float | None]] = {}
    for r in raw_rows:
        free_top = r["free_text"]["top_logprobs"]
        p_free = p_established_from_top_logprobs(free_top[0]) if free_top else None
        typed_top = r["typed"]["top_logprobs"]
        p_typed = None
        if typed_top:
            res = CompletionResult(text=r["typed"]["text"], model="x", top_logprobs=typed_top, wall_s=0.0)
            p_typed = score_decision(res, _STATUS_FIELD)["true"]
        p_by_key[(r["item_id"], r["element_id"])] = (p_free, p_typed)

    items_path = Path(eval_items_env)  # type: ignore[arg-type]
    items = [json.loads(line) for line in items_path.read_text().splitlines() if line.strip()]
    print(f"phase 1 raw sha256 confirmed. {len(raw_rows)} element scores loaded, {len(items)} eval items.")

    ev = load_and_verify_eval_set(wt)
    reg = build_p2b_scoring_registry()

    def fn_for(arm: str) -> Any:
        def fn(item_id: str, element_id: str) -> float:
            p_free, p_typed = p_by_key.get((item_id, element_id), (None, None))
            p = p_free if arm == "free_arm" else p_typed
            if p is None:
                raise KeyError(f"missing {arm} score for {(item_id, element_id)}")
            return 1.0 if p >= T0 else 0.0

        return fn

    used_keys: set[tuple[str, str]] = set()
    for arm in ("free_arm", "typed_arm"):
        fn = fn_for(arm)
        rows_out: list[dict[str, Any]] = []
        for part, gt, data in (
            ("full_ir_gold", "assert_expected", ev.full_ir_gold), ("negatives", "should_refuse", ev.negatives),
        ):
            for row in data:
                rec = from_dict(row["record"])
                cid = rec.audit.contract_id
                specs = tuple(s for s in element_specs_for_contract(cid, score_bin=D.SCORE_BIN) if not s.is_denial)
                pmin = {}
                for s in specs:
                    used_keys.add((rec.id, s.element_id))
                    pmin[s.element_id] = fn(rec.id, s.element_id)
                js = D.judgments_at_threshold(specs, pmin, rec, 0.5)
                try:
                    v, cp, fp, why = D.classify(rec, cid, specs, js, gt, reg)
                except (ParseError, ValidationError) as e:
                    v, cp, fp, why = f"PARSE_FAIL({type(e).__name__})", (False if gt == "assert_expected" else None), False, None
                rows_out.append(
                    {
                        "arm": arm, "partition": part, "item_id": rec.id, "verdict": v,
                        "checker_pass": cp, "false_prove": fp, "fail_reason": why,
                    }
                )
        out_path = results_dir / f"t2_harness_{arm}_verdicts.jsonl"
        with out_path.open("w") as f:
            for r in rows_out:
                f.write(json.dumps(r) + "\n")
        print(f"{arm}: wrote {len(rows_out)} verdict rows to {out_path}")

    missing = set(p_by_key) - used_keys
    if missing:
        print(f"WARNING: {len(missing)} phase-1 scores never used by the assembler: {list(missing)[:5]}...", file=sys.stderr)

    meta.data["end_utc"] = datetime.now(UTC).isoformat()
    meta.data["nvidia_smi_after"] = _nvidia_smi()
    meta_path = results_dir / "t2_harness_assemble_RUN-METADATA.json"
    meta.write(meta_path)
    print(f"RUN-METADATA written: {meta_path}")

    all_verdict_bytes = b""
    for arm in ("free_arm", "typed_arm"):
        all_verdict_bytes += (results_dir / f"t2_harness_{arm}_verdicts.jsonl").read_bytes()
    combined_sha = hashlib.sha256(all_verdict_bytes).hexdigest()
    print()
    print(f"VERDICT ROWS SEALED. Combined (free_arm + typed_arm bytes, in that order) sha256: {combined_sha}")
    print("This sha must reach R2 before any scoring pass, per the sealed prereg.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
