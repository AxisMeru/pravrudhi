# Track B adversarial review — prabhāsa-saṃskṛtam → prabhāsa-nyāya foundational LM

Date: 2026-09-15 (BST). Reviewer: this session (review branch `claude/track-b-adversarial-review-a73cee`, never merge).
Method: 8 read-only finder lenses over the four AxisMeru repos, each followed by an independent refuter that re-opened
every cited file, then a completeness critic; 17 agents, 555 tool calls. Only findings marked *confirmed* or
*corrected* by the refuter are used below. Every number is from a file named here; nothing is from memory.
Operator position taken as input (2026-09-15, this conversation, and request `r-30e9cc88` "for the prabhasa-nyaya
app do the full foundational llm building"): prabhāsa-nyāya is the foundational law LM built on
prabhāsa-saṃskṛtam, not only Track A's verifier; move to ~3B via Qwen/Nemotron teacher-student; keep fine-tuning on
Pāṇini/Nyāya/pratyabhijñā; use Loom end to end; train on RunPod L40S, spare the 5090.

## Part I — findings

### 1. What the objectives actually are (and where they contradict each other)

| # | Objective | Source | Status vs the operator's 2026-09-15 position |
|---|---|---|---|
| O1 | Sanskrit-native foundational LM (D1) + En→graph→reason-in-Sanskrit→En bridge (D2) | `prabhasa-samskrutam/CHARTER.md:8-15`, `docs/prd.md:13-15` | still the mission; the 3B plan must keep D1/D2 measurable |
| O2 | Token-efficiency thesis is the *primary* metric; 1B scale opens at ≥5 tok/param (ADR-0006) | `docs/prd.md:50-55`, `docs/decisions/0006-*.md:35-40` | **conflicts**: a 3B trained on the same 5.25B tokens is 1.75 tok/param, below the ADR's own gate. Needs a superseding ADR, not silence |
| O3 | "No fine-tuning an existing model as the foundation" | `docs/non-goals.md:16-17` | **conflicts** with Qwen/Nemotron as the 3B base. Refuter's reading: distillation *from* Qwen into an own student is allowed; *starting from* Qwen weights is not. The operator's ask is the latter → ADR required |
| O4 | Production pivot: train, benchmark, ship a chatbot; HF publish dropped | `docs/prd.md:85-87` | pivot stands; "HF dropped" is now the reason 1.13B weights have no off-box copy (see R4) |
| O5 | Track A = vendor-agnostic verification harness, "no weights are trained on this track" | `prabhasa-nyaya/README.md:1-8`, `ADR-0001:47-52` | **the assumption the operator is correcting**: no repo, ADR, or team rule anywhere records prabhāsa-nyāya as a *model*. Nothing wires a prabhāsa model into Track A's arms (`pravrudhi/src/pravrudhi/application/nyaya.py:58` DEFAULT_VENDORS = claude-cli, codex-cli, qwen-dashscope, glm-local) |
| O6 | Track B = "the Sanskrit-core model, 1.13B live, ~3B law-tuned as the MVP target" | `pravrudhi/docs/superpowers/specs/2026-09-09-prabhasa-nyaya-measurement-design.md:30` | the only place the 3B target is written; no plan ever set a base, budget, or gate for it |
| O7 | M7 plan for the 3B law-tune, with gates G0–G4 | `docs/plans/2026-09-12-m7-law-tuned-instruct.md` | its own §5: "None of G0–G4 are met yet … does not recommend starting a training run today" |
| O8 | VĀK-YANTRA north star (ADR-0011) and H-ORD R0–R4 research ladder (ADR-0010) | `docs/decisions/0010-*`, `0011-*`, `docs/vak-yantra-architecture.md` | design-only (zero code commits for Vāk-Yantra); no decision on whether it pauses for the MVP |

### 2. Achieved (model-measured unless labelled)

| Capability | Value | n / checkpoint | Evidence |
|---|---|---|---|
| M2 200M pretrain, structured channels significantly better on 4 domains | DCS bpb 1.8178 | 199.8M params, 2026-07-09 | `research/closure/m2_gate.json` |
| M3 353M pretrain, structured-channel lever NULL (honest) | DCS 1.9048; kāraka probe reversed 0.9857→0.9736 | 353M | `m3_gate.json`, ADR-0004/0007 |
| M4 1.13B Nemotron-H (Mamba-2 hybrid) pretrain on the clean spine | 5.246B tokens; DCS 1.6337, Itihāsa 0.6113, Samayik 0.84, English 1.7089; 19.7k tok/s, 27.5 GiB peak on the 5090 | `m4/final.pt` 13.5 GB | `m4_gate.json`, `m4_train.json` |
| kāraka probe F1 / morphology case / UPOS | 0.994 / 0.8209 / 0.8522 | 8,000 / 5,076 / 9,585 | `m5_benchmarks.json` |
| Deterministic round-trip tiers | 1.0 / 1.0 / 1.0 | 100 / 3 / 3 (pipeline-measured) | `m4_gate.json` |
| 25k instruction set, gold by construction | 25,240 train / 515 val; 18,478 `nyaya_check` | `data/sft/instruct_v1.jsonl` | `m6_instruct_v1_stats.json` |
| Field-weighted BM25 retrieval over the statute corpus | recall@4 0.78→0.997 own-denominator | 690 held-out, CPU only | `HANDOFF.md` item 1, `configs/retrieval/field_weights.yaml` |
| 1.13B + harness-built SFT + tuned retrieval + per-template calibration | citation recall 0.3656 (83/227), precision 0.61, abstention 9/9, lookup 0.9868 | 690 held-out | `HANDOFF.md` item 2 |
| One gated RSI LoRA round on the 1.13B (attention + Mamba mixer, 64 modules) | 0.3656→**0.4934** (+12.8 pp), abstention 9/9, cite-to-title −2.2 pp | `rsi_round1_1b/` 30 MB LoRA | `HANDOFF.md` item 3, journal 2026-09-13 |
| RSI round 2 correctly gate-rejected | probe regression 31.5% vs 15% threshold, no dev gain | — | commit `ee2f179` |
| OpenAI-compatible gateway + Cloudflare Worker + Vercel UI (M6 prong 2) | deployed, Playwright-verified | — | commits `58d37e4`, `43dd72a` |
| HF exporter for the custom-loop checkpoints | shape-verified on M3; Megatron 1.13B export never attempted (needs GPU) | — | `scripts/hf_export/export_megatron_to_hf.py` docstring |

### 3. Regressed

| # | Regression | Evidence |
|---|---|---|
| R1 | Semantic round-trip fidelity: **0.13 pass @0.70** (mean sim 0.6353, CI 0.078–0.210), the one D2 number with headroom, untouched since 2026-07-13 | `m5_benchmarks.json` semantic_fidelity |
| R2 | Closed-book law knowledge is ~0 (citation recall 1/227 = 0.004) and the F11 fix proved it is knowledge, not termination (byte-identical before/after) | `HANDOFF.md` item 4 |
| R3 | Two unreconciled histories: `main` (164c66e) vs `h-ord/phase1` (8618901), **87 commits apart**; the RSI/LoRA/F11 work sits on `assistant/trackB/item4-sft-refit` off `h-ord/phase1`, not on `main` | `git log main..h-ord/phase1`, `HANDOFF.md` "Two lines" |
| R4 | 223 GB of checkpoints (4 distinct 1.13B: m4, hord_r2 ×2, hord_r3) exist only at `/home/ss/fusion-project/prabhasa-samskrutam/data/checkpoints/`, 33 of 35 `.pt` root-owned; HF holds only m2/m3. Single copy, no backup | `docs/decisions/reports/2026-09-12-checkpoint-inventory.md`, `hf://models/qbz506` |
| R5 | No Track B model can be evaluated by `ext_eval.sh`/lm-eval or served by transformers: byte-vocab-256 Megatron blob, no HF export → every public-benchmark number (MMLU-law, LegalBench) is still Qwen's, never Track B's | M7 plan §4 "the real gate here" |
| R6 | torch.compile broken in the 5090 image (InductorError); throughput measured uncompiled | `m4_train.json` compiled=false, `megatron_run.sh:98` |
| R7 | `feat/m6-instruct-and-app` is merged (memory note "never merged" is wrong) but the app's live status is undocumented | refuter check `git merge-base --is-ancestor` |

### 4. Digressed (effort that did not move the mission)

| # | Digression | Cost / evidence | Salvageable asset |
|---|---|---|---|
| D1 | H-ORD order-tax program R0–R3 on 353M and 1.13B (18+ commits, 35.8 GB of checkpoints), never evaluated on any law task, no adopt/reject decision recorded (ADR-0012 requires one) | `hord_r1/r2/r3`, `research/findings/r0-order-tax.md` | the quotient objective result (P1 tax −60% at 1.13B) is a real finding for the paper; not for the MVP |
| D2 | VĀK-YANTRA five-organ architecture: 307-line design, zero code | `docs/vak-yantra-architecture.md` | a research north star; must be explicitly parked or it keeps pulling GPU time |
| D3 | Templated `law_citation_retrieval` harness: header weighting 6× drives recall@4 to 0.98 because the questions are templated; the headline 0.49 measures the harness + a selector, not legal knowledge (spike §12 warned exactly this) | `retrieval_tuning`, `HANDOFF.md` attribution paragraph | keep as an internal gate; never as the public number |
| D4 | Two RSI LoRA rounds on a model with ~0 closed-book knowledge: correct discipline, wrong lever — the ceiling was knowledge | `HANDOFF.md` item 3/round 2 | the gate machinery (`loop.py`, `lora_megatron.py`) transfers to the 3B |
| D5 | kāraka probe at 0.994 (no headroom) still reported as a panel headline | measurement-design spec §2.1 | drop from the panel; keep as a regression tripwire |
| D6 | Nothing built the mission-critical inputs: a Sanskrit *law* corpus (law-domain data <1 MB; prabhasa-nyaya `data/` 296 KB), Sanskrit-native Nyāya inference data for law, Sanskrit-language instructions (instruct_v1 prompts are all English) | `du -sh`, `m6-instruct-and-app.md` | — this is the real gap |

### 5. What blocks the 3B plan today (all confirmed)

1. **No 3B exists, no path from 1.13B to 3B**: width/depth growth of a trained byte-level Nemotron-H means re-initialising and re-pretraining; the corpus is 847M words ≈ 5.25B bytes (`configs/corpus/manifest.yaml`), 11× short of Chinchilla for 3B and below ADR-0006's own gate.
2. **Tokenizer**: byte vocab 256 vs Qwen/Nemotron 131k–151k BPE → logit distillation impossible; `scripts/m5/distill.py` is token-level KD only, never run.
3. **Memory on L40S (48 GB)**: full fine-tune of 3–4B with fp32 Adam ≈ 12 B/param ≈ 36–48 GB before activations → does not fit; LoRA or 8-bit optimiser does (estimate; must be preflighted, no file measures it).
4. **Loom**: only `sft` is executable (`loom_pipeline.py:65-72`); `pretrain`, `continue_pretrain`, `distill`, `evaluate`, `promote` pending; Track B contains no reference to Loom at all; no RunPod transport (`hosts/transports.py:12-95` = local/ssh/orca).
5. **Container**: the training image is sm_120-built (`prabhasa/nemo-5090:26.02`, 25.7 GB); L40S is Ada sm_89, x86_64 — kernels rebuild; unmeasured.
6. **Governance**: non-goals §16 and ADR-0006 forbid the plan as stated; ADR-0001 defines prabhāsa-nyāya as a harness; no charter for prabhāsa-nyāya-as-model exists.

## Part II — course correction and the leap

### 6. TRIZ framing

Physical contradiction: the model must be **Sanskrit-native by structure** (Pāṇini, kāraka, Nyāya trace, from a 5B-token corpus) *and* **broadly knowledgeable in law and English** (which only trillions of pretraining tokens give). Separation principles (engine result): **by system level** (0.85) and **by condition** (0.85) beat time/space. Matrix cells consulted: adaptability↔quantity of substance → principles 3 (local quality), 35 (parameter change), 15 (dynamics); productivity↔measurement accuracy → 1 (segmentation), 10 (prior action), 34 (discard and recover), 28 (replace the mechanical system); reliability↔energy → 36 (phase transition), 23 (feedback).

Applied:

- **System level**: the *whole* (Prabhāsa-Nyāya) inherits world and legal knowledge from a pretrained 3–4B base; the *parts* that carry the Sanskrit thesis are adapters and losses trained on Track B's own audited corpus and gold-by-construction structure data. The from-scratch byte-level line stops being the foundation and becomes the **Sanskrit structure teacher** (M4 1.13B) and the source of probes.
- **Condition**: reasoning happens in the Nyāya graph only when the verifier can check it; otherwise the model answers grounded and abstains. Track A stays the gate (ADR-0001 untouched).
- **Prior action (10) / replace the mechanical system (28)**: choosing an HF-native base *deletes* the exporter problem (R5), makes `ext_eval.sh` and lm-eval work on day one, and gives Loom's `sft` binding (HF snapshot + LoraRecipe) a model it can already execute.
- **Discard and recover (34)**: discard "1.13B → 3B by retraining"; recover the corpus (847M words), instruct_v1 (25k), the retrieval store, the RSI gate machinery, the probes, the M4 teacher.
- **Feedback (23)**: Track A's checker becomes the filter for distillation targets: only teacher answers whose citations are `licensed` and whose Nyāya trace passes become student targets ("verifier-filtered distillation"). This is pratyabhijñā in the training loop: the student learns only recognised, witnessed inferences.
- **Phase transition (36)**: the number that matters changes from harness-measured template recall to **closed-book vs retrieval-grounded gap on independent gold** (spec §4.4; LegalBench rule-application, IL-TUR, own statute set).

### 7. The plan: Prabhāsa-Nyāya 3B, three Loom stages on RunPod

Base candidates (Hub-verified 2026-09-15):

| Base | Arch | License | Notes |
|---|---|---|---|
| `Qwen/Qwen3-4B-Base` | dense transformer, 36 L, 3.6B non-embed | Apache-2.0 | 36T tokens, 119 languages; transformers-native; Devanagari token efficiency to be measured |
| `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | Mamba-2 hybrid (`NemotronHForCausalLM`, 42 L, 4 attention layers) | Nemotron Open Model License (commercial) | same architecture family as Track B's 1.13B, so `lora_megatron.py` regexes transfer; stated English-only; "improved using Qwen" |
| `nvidia/Nemotron-H-4B-Base-8K` | Mamba-2 hybrid, 52 L | research-only license | closest cousin to M4; license blocks product use |
| `nvidia/Nemotron-4-Mini-Hindi-4B-Base` | dense | NVIDIA Open Model | proves Devanagari continued-pretraining at 4B works (400B Hindi/English tokens); tokenizer evidence |

Recommended: **two arms, Qwen3-4B-Base and Nemotron-3-Nano-4B**, decided by a 1-day preflight bake-off (tokens per Devanagari character on DCS; closed-book `law_apply`; kāraka probe after 200M tokens of Sanskrit continued pretraining). Mamba-hybrid is the operator's stated preference and keeps the M4 lineage; Qwen is the safer license and the stronger multilingual prior.

Stages (each one Loom stage, each gated, each a RunPod job):

1. **`continue_pretrain`** — Sanskrit core: 847M-word audited corpus + paribhāṣā synthetic gold + Pāṇini/kāraka channel text; LoRA r=64 (or full FT with 8-bit Adam if preflight shows it fits) at seq 2048; gate = kāraka/morphology probes ≥ M4, Sanskrit bpb trend, English MMLU-law not below base − 1 pp.
2. **`sft`** — instruct_v1 (25k, gold by construction) + law_v3 (6,206/690) + Sanskrit-language instruction variants (to be generated, gap D6) + Nyāya trace format; gate = Z3-validity through the model, F_rt through the model, closed-book `law_apply`.
3. **`distill`** — sequence-level, verifier-filtered: teachers = frontier vendors already wired in Track A (claude-cli, codex, qwen) + M4 for Sanskrit renderings; keep only `licensed` + Lean-passing traces; student = the stage-2 model; gate = (c)−(b) lift on LegalBench rule-application and IL-TUR, measured per Track A's three-arm protocol.
4. **`evaluate` + `promote`** — lm-eval on the HF model (works because the base is HF-native), Track A checker arms, ledger admission via `parse_prabhasa_panel`.

Budget (estimates, labelled; every figure to be replaced by a measured preflight): L40S community $0.79/h, secure $1.09/h; stock LOW in all six datacentres today. Stage 1 at 1B tokens on one L40S ≈ 6·4e9·1e9 = 2.4e19 FLOPs; at ~35% of 362 TFLOPS ≈ 53 h ≈ $45–60. Full corpus (5.25B tokens) ≈ $240–320. Stages 2–3 are hours. Network volume 200 GB ≈ $14/month. A first-phase cap of **$300** covers the bake-off plus stage 1 on one arm.

### 8. Scaffolding Studio must build (this is the team's RSI work, not hand-work)

| Item | Where | Why first |
|---|---|---|
| RunPod transport (pod lifecycle, volume mount, checkpoint sync, cost ledger) | `pravrudhi/src/pravrudhi/hosts/transports.py` | nothing runs off-box without it |
| Loom bindings: `evaluate` (ext_eval.sh), `continue_pretrain`, `distill` (teacher sampling + checker filter), `promote` (ledger policy) | `loom_pipeline.py` STAGE_EXECUTABILITY | the operator's end-to-end claim is false until these exist |
| Track B adopts Loom programs as the only way to launch a run | `prabhasa-samskrutam/configs/loom/*.loom` | Track B has zero Loom usage today |
| Checkpoint backup: HF private repos (AxisMeru org) or RunPod volume for the 4 × 1.13B + LoRA | — | R4 single-copy risk |
| Branch reconciliation: one lineage branch for the model line; `h-ord/phase1` frozen as research | prabhasa-samskrutam | R3 |
| ADRs: (a) Prabhāsa-Nyāya = foundational law LM, home repo, charter; (b) supersede non-goals §16 and ADR-0006's gate for this line; (c) H-ORD/Vāk-Yantra parked; (d) public number = independent gold only | both repos | governance blockers §5.6 |

### 9. What this review does not claim

No 3B number exists anywhere; no L40S throughput has been measured; Devanagari tokenizer efficiency for either base is unmeasured; the dollar figures are FLOP arithmetic. The operator's redefinition of prabhāsa-nyāya is recorded here from this conversation and `r-30e9cc88`; it is not yet in any ADR.
