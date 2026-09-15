# ADR-0005 — Prabhāsa-Nyāya is the English law LLM; Sanskrit is its type system (DRAFT for prabhasa-nyaya)

Status: DRAFT by the Track B reviewer, 2026-09-15, for the lead to adopt, amend or reject. Becomes a decision only
when merged on `prabhasa-nyaya` `main` by the lead.
Amends: ADR-0001 decisions 1 and 6. Supersedes, for this line only: `prabhasa-samskrutam/docs/non-goals.md` §16
("no fine-tuning an existing model as the foundation") and the token gate of `prabhasa-samskrutam` ADR-0006.
Operator instructions: 2026-09-15 08:35 and 08:50 BST (AskUserQuestion, recorded in the review), `r-30e9cc88`.

## Context

Track B (prabhasa-samskrutam) built a Sanskrit-native byte-level Nemotron-H line to 1.13B (5.25B tokens). Its best
law result is harness-bound: citation recall 0.4934 on a templated 690-item set with closed-book knowledge 0.004
(review Part I §2–3). There is no path from 1.13B to 3B (corpus 5.25B bytes, byte vocab 256, no HF export), and the
operator has closed the Sanskrit-language PoC. Track A (this repo) was defined as a harness that trains no weights.

## Decision

1. **Prabhāsa-Nyāya is a model line, and it lives here.** An English-focused, law-focused LLM of 3–4B parameters,
   fine-tuned from a pretrained open base (arms: `Qwen/Qwen3-4B-Base`, Apache-2.0; `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16`,
   Nemotron Open Model License; chosen by pre-registered bake-off). ADR-0001 decision 1's sentence "No weights are
   trained on this track" is replaced by: *weights are trained here, under `research/prereg/` gates, and the
   resulting model enters the harness as one vendor id like any other.* The harness stays vendor-agnostic.
2. **Sanskrit is the type system, not the surface language.** The model emits a typed intermediate representation
   (`nyaya-law-v1`: kāraka-role bindings over English spans, padārtha-sorted nodes, Lean `Claim` operators, a
   pañcāvayava trace naming a trusted `rule_id`, a hetvābhāsa verdict, cited sections, a typed abstain reason) that the
   Lean/Z3 layer checks. Sanskrit generation, declension, realization and the byte-level tokenizer are not migrated.
3. **What migrates from prabhasa-samskrutam** (asset inventory, review spikes): graph domain (types, validators,
   renderer), bridge `templates.py`, reason (nyaya, verify, z3_verify, self_consistency), the instruct/law data
   builders and their JSONL (English-side slices only), retrieval store/scoring/calibration, `law_apply`, the 690
   held-out set, the scorer, probe gold, and the RSI gate loop + LoRA code from pravrudhi branch
   `claude/ttt-llm-research-0f1adf`. `sequence_nll.py` is reimplemented against `AutoModelForCausalLM`.
4. **prabhasa-samskrutam is frozen as the research record.** `h-ord/phase1` and `main` are not reconciled; H-ORD and
   Vāk-Yantra are parked (no GPU time) until an ADR reopens them. Its four 1.13B checkpoints and the RSI LoRA are
   backed up (public HF under AxisMeru, honest cards) before anything else runs.
5. **Numbers.** The templated citation harness is an internal gate. The public number is the three-arm
   (c)−(b) lift on independent gold (LegalBench rule-application, IL-TUR LSI, own statute set) under the Track A
   protocol, with abstention reported beside it. Every claim carries its tier and n.
6. **Compute.** RunPod under `docs/decisions/RUNPOD-HOUSE-RULES.md` (one pod, ≤ L40S class, $200, checkpoint ≤ 30 min,
   rsync to the 5090, milestones to public HF). The 5090 hosts local models and reviewer spikes only.
7. **Loom.** Every training or evaluation job is a Loom program once the `evaluate`/`distill`/`promote` bindings
   exist; until then, a committed manifest per job.

## Consequences

- ADR-0001 decision 6 ("only the legal content and the contracts belong here") now reads: the legal content, the
  contracts, the IR schema, the training data builders, the prereg/gate records and the model cards belong here;
  vendor comparison UI stays in the pravrudhi product.
- `prabhasa-samskrutam` CHARTER/PRD/non-goals are not rewritten; this ADR is cross-referenced from its HANDOFF.
- Gates: G-IR (schema + parser + 20 goldens + four defects fixed), G-P0 (preflight file), G-P1 (bake-off decision),
  G-P2 (structure SFT: parse rate ≥ 0.98, Lean `grounded` rate vs base, abstention, English MMLU-law within −1 pp),
  G-P3 (verifier-filtered distillation: (c)−(b) lift), all pre-registered before the run.

## Rejected alternatives

- Scaling the byte-level 1.13B to 3B (no tokens, no path, no export).
- Keeping the model in prabhasa-samskrutam (operator decision 2).
- A research-only base (`Nemotron-H-4B-Base-8K`) — license blocks product use.
