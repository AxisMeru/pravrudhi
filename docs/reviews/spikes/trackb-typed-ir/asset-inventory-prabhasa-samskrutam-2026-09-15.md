# Migration inventory — prabhasa-samskrutam → prabhasa-nyaya

Repo: `/home/ss/projects/prabhasa-samskrutam`, branch `h-ord/phase1`. Everything below exists on `h-ord/phase1` working tree unless "branch" column says otherwise. `main` is a strict subset (it lacks `src/prabhasa/application/retrieval/*`, `src/prabhasa/application/hord/*`, and `scripts/{m7,sft,eval,hord,g0,b8,hf_export,law_apply,rsi_*,measure_*}`).

Portability legend: **P** = pure Python/stdlib, portable as-is to HF Qwen/Nemotron. **P-hf** = portable, uses HF transformers only. **T** = torch-only (portable, retarget model handle). **M** = Megatron-LM core import → must be rewritten. **B** = byte-level tokenizer coupling → must be rewritten for a BPE vocab. **V** = vidyut (Sanskrit morphology) dependency.

## (A) Domain graph schema / validators / renderer

| path | lines | dep | note |
|---|---|---|---|
| src/prabhasa/domain/graph/types.py | 195 | P | `PadarthaCategory`, `KarakaRole`, `SansaType`, `GraphNode/Edge/MeaningGraph` |
| src/prabhasa/domain/graph/validators.py | 101 | P | `validate_graph`, `is_valid` |
| src/prabhasa/domain/graph/renderer.py | 224 | P | `render_graph`/`parse_graph` lossless round-trip + IAST |
| src/prabhasa/domain/graph/__init__.py | 37 | P | |
| src/prabhasa/domain/contracts/closure.py | 243 | P | closure/gate record contract |
| src/prabhasa/domain/contracts/__init__.py | 25 | P | |
| src/prabhasa/application/eval/round_trip.py | 139 | P | round-trip fidelity metric |

## (B) Bridge: English ↔ graph

| path | lines | dep | note |
|---|---|---|---|
| src/prabhasa/application/bridge/templates.py | 223 | P | `parse_en`, `render_en`, `Frame`/`Slot`, `supported_frames` — this IS the English→graph parser |
| src/prabhasa/application/bridge/lexicon.py | 94 | P + V(optional) | `decline`, `conjugate` |
| src/prabhasa/application/bridge/realizer.py | 84 | P | `realize_sanskrit`, `realize_via_samsadhanii` (HTTP protocol, no torch) |
| src/prabhasa/application/bridge/__init__.py | 17 | P | |

## (C) Nyāya kernel + Z3

| path | lines | dep | note |
|---|---|---|---|
| src/prabhasa/application/reason/nyaya.py | 66 | P | `Avayava` pañcāvayava, `Syllogism`, `build_syllogism` |
| src/prabhasa/application/reason/verify.py | 86 | P | `Verdict`, `evaluate`, hetvābhāsa classification |
| src/prabhasa/application/reason/z3_verify.py | 82 | z3 only | `z3_available`, `z3_check_pervasion` |
| src/prabhasa/application/reason/self_consistency.py | 66 | P | verifier-gated vote |
| src/prabhasa/application/reason/__init__.py | 29 | P | |
| scripts/m4/m4_gate_frt_nyaya.py | 189* | T | FRT/nyāya gate; torch only, retargetable |
| scripts/m4/run_logic_benchmarks.py | 219 | P | |
| scripts/m4/assemble_m5_benchmarks.py | — | M | closure assembly, Megatron paths |

\*wc reported jointly; file is torch-import-only.

## (D) Instruction / synthetic data generators + outputs

| path | lines | dep |
|---|---|---|
| scripts/m6/build_instruct_v1.py | 670 | P |
| scripts/sft/build_law_v1.py | 337 | P |
| scripts/m7/build_sft_mix.py | 66 | P |
| scripts/m4/build_sft.py | — | P |
| src/prabhasa/application/corpus/synthetic.py | 150 | P |
| src/prabhasa/application/sft/targets.py | 70 | P |
| research/closure/m6_instruct_v1_stats.json | — | P (25 240 train / 515 val; kinds: nyaya_check 18 478, en2graph 1 506, graph2sa 1 510, en2sa 1 504, sa2en 1 493, reason_chain 749) |

| data file | lines |
|---|---|
| data/sft/instruct_v1.jsonl | 25 240 |
| data/sft/instruct_v1_val.jsonl | 515 |
| data/sft/law_v1.jsonl | 1 371 |
| data/sft/law_v2.jsonl | 1 914 |
| data/sft/law_v2_train.jsonl | 1 723 |
| data/sft/law_v3_train.jsonl | 6 206 |
| data/sft/law_v3_train_refit.jsonl | 6 206 |
| data/sft/m7_mix_v1.jsonl | 12 412 |
| data/sft/m7_mix_refit.jsonl | 12 412 |
| data/sft/sft.jsonl | 195 |

All `data/sft/*.jsonl` are text prompt/target pairs — tokenizer-agnostic, fully portable.

## (E) Law retrieval + eval harness

| path | lines | dep |
|---|---|---|
| src/prabhasa/application/retrieval/store.py | 475 | P (BM25, field weights, `build_grounded_prompt`, `parse_answer`, `grounded`, `retrieval_recall[_by_kind]`) |
| src/prabhasa/application/retrieval/scoring.py | 151 | P (`build_candidates`, `compose_answer`, `select_best`, `score_candidates`) |
| src/prabhasa/application/retrieval/calibration.py | 148 | P (abstention thresholds, balanced acc, save/load) |
| src/prabhasa/application/retrieval/sequence_nll.py | 68 | **T + M** — only torch/Megatron file in retrieval |
| src/prabhasa/application/retrieval/__init__.py | 29 | P |
| configs/retrieval/field_weights.yaml | 18 | P |
| scripts/eval/score_law_qa.py | 231 | P |
| scripts/measure_retrieval_recall.py | 59 | P |
| scripts/measure_citation_recall.py | 101 | M + B |
| scripts/measure_calibrated_citation_recall.py | 219 | M + B |
| scripts/measure_with_spike_calibration.py | 181 | M + B |
| scripts/rsi_round_1b.py | 296 | M + B |
| scripts/rsi_round_1b_recalibrate.py | 122 | M + B |
| scripts/rsi_round2_1b.py | 384 | M + B |
| scripts/m7/generate.py | 169 | T + B |
| scripts/m7/score_checkpoint.py | 56 | T |
| scripts/m7/train_full_sft.py | 282 | T |
| scripts/m7/sft_batched.py | 266 | T |
| scripts/m7/dry_run_sft.py | 207 | T + M |
| scripts/law_apply/items.py | 709 | P (33 hand-authored element-negation items) |
| scripts/law_apply/build_items.py | 91 | P |
| data/eval/law_qa_heldout_v1.jsonl | 191 | P |
| data/eval/law_qa_heldout_v3.jsonl | 690 | P |
| data/eval/law_qa_heldout_v3_refit.jsonl | 690 | P |
| data/eval/law_apply/items.jsonl | 33 | P |
| data/eval/law_apply/gold.jsonl | 33 | P |
| data/eval/law_apply/manifest.json | — | P |
| data/holdout/holdout_hashes.json | 1 (3.2 MB single-line) | P — decontamination hashes |
| research/m7/{run_report,before_report,after_report,dry_run_report,run_report_retry}.json, answers_{before,after}.jsonl | — | P |

## (F) RSI gate loop / LoRA machinery

| path | lines | dep | note |
|---|---|---|---|
| **loop.py** | — | — | **DOES NOT EXIST** in any branch |
| **g0/lora_megatron.py** | — | — | **DOES NOT EXIST**; only `scripts/g0/sft_megatron_batched.py` (full-FT) |
| scripts/g0/sft_megatron_batched.py | 393 | T + M | |
| scripts/g0/preflight_1p13b.py | 139 | T + M | |
| scripts/g0/measure_load_1p13b.py | 103 | T + M | |
| scripts/orchestrate/autopilot.py | — | M | RSI driver loop (de-facto `loop.py`) |
| scripts/orchestrate/_exp_config.py | — | P | |
| src/prabhasa/application/orchestrate/closure.py | 199 | M | |
| src/prabhasa/application/orchestrate/state.py | 258 | P | |
| src/prabhasa/application/orchestrate/policy.py | 53 | P | |
| src/prabhasa/application/efe/{agent,candidates,ledger,runner}.py | 234/88/136/92 | P | EFE experiment selection |
| scripts/m2/eval_gate.py | — | T + V | |
| scripts/m4/eval_gate_m4.py | — | T + M | |
| scripts/hf_export/export_megatron_to_hf.py | 365 | T + M + hf | Megatron→HF converter; obsolete if starting from HF base |
| scripts/hf_export/vendor/modeling_nemotron_h.py | — | T + hf | |
| scripts/hord/train_r1.py / train_r5cal.py / train_r2_1b.py | 483 / 313 / 199 | T / T / T+M | RSI rounds |
| scripts/hord/{run_battery,probe_readout,calibration_probe}.py | 224 / 217 / 185 | T + M | |
| scripts/hord/{tax_locus,assemble_report,build_materials,parse_holdout}.py | 133 / 96 / 106 / 116 | T / P / P / P | |
| src/prabhasa/application/hord/{perturb,stats,license}.py | 110 / 62 / 61 | P | |
| data/hord/{materials_all,materials_r1a,materials,karaka_cache_hord}.jsonl | 6 113 / 5 071 / 1 042 / 15 113 | P | |

## (G) Probes + gold

| path | lines | dep |
|---|---|---|
| scripts/m3/probe_karaka.py | 214 | T |
| scripts/m2/probe_morph.py | 245 | T |
| scripts/m4/probe_karaka_m4.py | 211 | T + M |
| scripts/m4/probe_morph_m4.py | 250 | T + M |
| scripts/m2/build_holdouts.py | — | P (gold builder) |
| scripts/m3/build_holdouts.py | — | P (gold builder) |
| src/prabhasa/application/corpus/vidyut_morph.py | 177 | V |
| data/packed/m3/karaka_cache.jsonl, data/packed/m4/karaka_cache.jsonl | — | P |
| research/closure/m3_probe_{karaka,morph}.json, m3_aux_probe_*, m4_final_eval/probe_*.json | — | P |

Probe *gold* lives inside `data/packed/*` (22 GB total, mostly Megatron `.bin`/`.idx` — take only `karaka_cache.jsonl` + `meta.json`).

## (H) Corpus manifest / Sanskrit structure sources

| path | lines | dep |
|---|---|---|
| configs/corpus/manifest.yaml | 227 | P |
| configs/corpus/synthetic_buckets.yaml | 28 | P |
| src/prabhasa/application/corpus/manifest.py | 196 | P |
| src/prabhasa/application/corpus/structure.py | 222 | P |
| src/prabhasa/application/corpus/paribhasha_natural.py | 246 | P |
| src/prabhasa/application/corpus/growth.py | 114 | P |
| src/prabhasa/application/corpus/prepare.py | 356 | **M** (writes Megatron packed format) |
| data/corpus/paribhasha_natural/paribhasha_natural_v1.jsonl | 24 096 | P |
| scripts/m3/gen_paribhasha.py, prewarm_silver.py | — | P |
| scripts/corpus/growth/{fetch_external,ocr_pipeline}.py | — | P / P-hf |
| scripts/corpus/audited_tokens_report.py | — | P |

## (I) Tests covering A–G

| path | lines | covers | dep |
|---|---|---|---|
| tests/unit/test_graph_types.py | 86 | A | P |
| tests/unit/test_graph_validators.py | 126 | A | P |
| tests/unit/test_graph_renderer.py | 105 | A | P |
| tests/unit/test_graph_golden.py | 79 | A | P |
| tests/unit/test_round_trip.py | 53 | A | P |
| tests/unit/test_bridge.py | 82 | B | P |
| tests/unit/test_realizer.py | 59 | B | P |
| tests/unit/test_reason.py | 83 | C | P |
| tests/unit/test_self_consistency.py | 47 | C | P |
| tests/unit/test_frt_tier3.py | 46 | C | P |
| tests/unit/test_instruct_v1.py | 213 | D | P |
| tests/unit/test_build_law_v1.py | 221 | D | P |
| tests/unit/test_build_law_v1_split.py | 117 | D | P |
| tests/unit/test_build_sft_mix.py | 72 | D | P |
| tests/unit/test_synthetic.py | 78 | D | P |
| tests/unit/test_retrieval.py | 388 | E | P |
| tests/unit/test_scoring.py | 137 | E | P |
| tests/unit/test_score_law_qa.py | 197 | E | P |
| tests/test_law_apply.py | 78 | E | P |
| tests/test_eval_adapter.py | 274 | E/F | M |
| tests/unit/test_generate.py | 131 | E | T+B |
| tests/unit/test_sft_batched_collate.py | 109 | E/F | T |
| tests/unit/test_train_full_sft_preflight.py | 73 | E/F | T |
| tests/unit/test_g0_sft_megatron_batched_collate.py | 88 | F | T+M |
| tests/unit/test_g0_sft_megatron_batched_preflight.py | 121 | F | T+M |
| tests/unit/test_export_megatron_to_hf.py | 50 | F | M |
| tests/unit/test_b8_compare.py | 117 | F | T+M |
| tests/unit/test_orchestrate.py | 216 | F | P |
| tests/unit/test_closure.py | 127 | A/F | P |
| tests/unit/test_autopilot.py | 119 | F | P |
| tests/unit/test_efe.py | 128 | F | P |
| tests/test_hord_pure.py | 122 | F | P |
| tests/unit/test_vidyut_morph.py | 108 | G | V |
| tests/unit/test_m3_holdouts.py | 50 | G | P |
| tests/unit/test_corpus_manifest.py | 152 | H | P |
| tests/unit/test_corpus_prepare.py | 174 | H | M |
| tests/unit/test_structure.py | 135 | H | P |
| tests/unit/test_paribhasha_natural.py | 111 | H | P |
| tests/unit/test_growth.py | 83 | H | P |
| tests/unit/test_tokenizer.py | 64 | — | B (do not migrate) |
| tests/unit/test_decoding.py | 81 | — | P |
| tests/unit/test_cli.py | 74 | — | B |
| tests/strategies.py, tests/conftest.py | 63 / 1 | shared | P |

## Do-not-migrate (dead weight under the new product)

| path | lines | reason |
|---|---|---|
| src/prabhasa/application/tokenizer/{bytelevel,hybrid,__init__}.py | 31 / 97 / 13 | byte-level + vidyut-hybrid vocab; Qwen/Nemotron ship their own BPE |
| configs/train/nemotron_h_*.yaml (10 files) | — | Megatron-LM pretrain configs for 200M–1.13B scratch models |
| data/packed/** | 22 GB | Megatron `.bin`/`.idx` bound to the byte vocab |
| scripts/m1/*, scripts/m2/train_130m.py, scripts/m4/megatron_pretrain.py, scripts/m5/distill.py | — | from-scratch pretrain path |
| src/prabhasa/cli/main.py | 398 | wired to byte tokenizer |

## Three things that will NOT transfer

| # | item | why |
|---|---|---|
| 1 | Every checkpoint, packed dataset (`data/packed/**`, 22 GB), and all BPB/probe numbers in `research/closure/**` | All tied to the custom byte-level+vidyut hybrid tokenizer and to Megatron-LM's `GPTDatasetBuilder` layout. A Qwen3-4B/Nemotron-4B base has a different vocab and a different embedding table, so bits-per-byte, karaka/morph probe accuracies, and every trained weight are non-comparable and non-loadable. The probe *gold* and the `karaka_cache.jsonl` survive; the measurements do not. |
| 2 | The whole Megatron training/eval spine — `scripts/g0/sft_megatron_batched.py` (393), `scripts/m4/{megatron_pretrain,sft_megatron,eval_gate_m4,probe_*_m4}.py`, `scripts/orchestrate/autopilot.py`, `src/prabhasa/application/{corpus/prepare,orchestrate/closure,retrieval/sequence_nll}.py`, `scripts/hf_export/**` | These import `megatron.core` and assume tensor/pipeline-parallel model handles and `.distcp` checkpoints. Under HF + PEFT LoRA + TRL the correct move is a rewrite against `AutoModelForCausalLM`, not a port. `sequence_nll.py` (68 lines) is the one that matters — it is the scoring primitive under the entire retrieval calibration stack and must be reimplemented (~30 lines) before (E) runs at all. Note there is **no `loop.py` and no `g0/lora_megatron.py` anywhere in the repo or any branch** — the RSI loop is `scripts/orchestrate/autopilot.py` plus the `rsi_round*_1b.py` scripts, and LoRA was never implemented. |
| 3 | The Sanskrit-generation half of the product: `realizer.py` (84), `lexicon.py` (94), the `sa2en`/`en2sa`/`graph2sa` slices of `instruct_v1.jsonl` (≈4 507 of 25 240 examples), and `vidyut_morph.py` (177) | The new model is not required to emit Sanskrit. Surface realization, declension/conjugation tables, and the Sāmsādhanī round-trip become dead paths. Retain `en2graph` (1 506), `nyaya_check` (18 478), and `reason_chain` (749) — 20 733 of 25 240 examples, ~82% — and drop the rest, or the SFT mix will spend a fifth of its budget teaching a language the product no longer speaks. Correspondingly `src/prabhasa/application/corpus/prepare.py`'s vidyut path and `tests/unit/test_vidyut_morph.py` lose their purpose. |