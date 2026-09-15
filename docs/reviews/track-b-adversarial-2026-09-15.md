# Track B adversarial review — prabhāsa-saṃskṛtam → prabhāsa-nyāya foundational LM

Date: 2026-09-15 (BST). Reviewer: this session (review branch `claude/track-b-adversarial-review-a73cee`, never merge).
Method: 8 read-only finder lenses over the four AxisMeru repos, each followed by an independent refuter that re-opened
every cited file, then a completeness critic (17 agents, 555 tool calls; raw findings in
`docs/reviews/workflow-findings-2026-09-15.json`); an asset-inventory agent over prabhasa-samskrutam; a Codex
gpt-6-astra design spike (`docs/reviews/spikes/trackb-typed-ir/`). Only findings marked *confirmed* or *corrected* by a
refuter are used. Every number is from a file named here.

**Operator decisions taken on this review (AskUserQuestion, 2026-09-15 08:35 BST):**
1. Sanskrit is **not** a language requirement for prabhāsa-nyāya; prabhāsa-saṃskṛtam's PoC is done. The ask is an
   **English-focused, law-focused LLM** that leverages Sanskrit's lexical, grammatical and ontological machinery.
2. The model line **lives in the prabhasa-nyaya repo**, taking everything relevant from prabhasa-samskrutam.
3. RunPod: $200 loaded, ~10 days at ~$1/h, one pod only, L40S or cheaper. House rules: `docs/decisions/RUNPOD-HOUSE-RULES.md`
   (mirrored in `~/.claude/CLAUDE.md`). **No GPU work until the operator tells the lead to kick off.**
4. This session is a spike/research reviewer (like the Track A review session); `lead` and Track B execute.

Part I is the audit of what Track B did. Part II is the plan for the redefined product.

## Part I — findings

### 1. What the objectives were (and where they contradict each other)

| # | Objective | Source | Status against the 2026-09-15 decisions |
|---|---|---|---|
| O1 | Sanskrit-native foundational LM (D1) + En→graph→reason-in-Sanskrit→En bridge (D2) | `prabhasa-samskrutam/CHARTER.md:8-15`, `docs/prd.md:13-15` | **retired for the product** (decision 1); remains prabhāsa-saṃskṛtam's research record |
| O2 | Token-efficiency thesis primary; 1B scale opens at ≥5 tok/param (ADR-0006) | `docs/prd.md:50-55`, `docs/decisions/0006-*.md:35-40` | not applicable to a pretrained base; needs an ADR saying so |
| O3 | "No fine-tuning an existing model as the foundation" | `docs/non-goals.md:16-17` | **superseded by decision 1** for prabhāsa-nyāya; ADR required |
| O4 | Production pivot: train, benchmark, ship a chatbot; HF publish dropped | `docs/prd.md:85-87` | stands; the "HF dropped" rule is why the 1.13B has no off-box copy (R4) |
| O5 | Track A = vendor-agnostic verification harness; "no weights are trained on this track" | `prabhasa-nyaya/README.md:1-8`, `ADR-0001:47-52`, decision 6 lines 97-101 | **must be amended**: the repo now also hosts the model line. The harness stays vendor-agnostic; the prabhāsa model becomes one more vendor arm |
| O6 | "Track B — the foundational model. The Sanskrit-core model, 1.13B live, ~3B law-tuned as the MVP target" | `pravrudhi/docs/superpowers/specs/2026-09-09-prabhasa-nyaya-measurement-design.md:30` | the only written 3B target; never given a base, budget or gate |
| O7 | M7 plan with gates G0–G4 | `docs/plans/2026-09-12-m7-law-tuned-instruct.md` | its own §5: "None of G0–G4 are met yet"; now moot |
| O8 | VĀK-YANTRA north star (ADR-0011), H-ORD ladder (ADR-0010) | `docs/decisions/0010-*`, `0011-*` | research; must be parked by ADR so it stops drawing GPU time |

### 2. Achieved (model-measured unless labelled)

| Capability | Value | n / checkpoint | Evidence |
|---|---|---|---|
| M2 200M pretrain; structured channels significantly better on 4 domains | DCS bpb 1.8178 | 199.8M, 2026-07-09 | `research/closure/m2_gate.json` |
| M3 353M pretrain; structured-channel lever NULL (honest) | DCS 1.9048; kāraka probe reversed 0.9857→0.9736 | 353M | `m3_gate.json`, ADR-0004/0007 |
| M4 1.13B Nemotron-H (Mamba-2 hybrid) on the clean spine | 5.246B tokens; DCS 1.6337, Itihāsa 0.6113, Samayik 0.84, English 1.7089; 19.7k tok/s, 27.5 GiB peak | `m4/final.pt` 13.5 GB | `m4_gate.json`, `m4_train.json` |
| kāraka probe F1 / morphology case / UPOS | 0.994 / 0.8209 / 0.8522 | 8,000 / 5,076 / 9,585 | `m5_benchmarks.json` |
| Deterministic round-trip tiers | 1.0 / 1.0 / 1.0 | 100 / 3 / 3 (pipeline) | `m4_gate.json` |
| 25k instruction set, gold by construction | 25,240 train / 515 val; 18,478 `nyaya_check`, 1,506 `en2graph`, 749 `reason_chain` | `data/sft/instruct_v1.jsonl` | `m6_instruct_v1_stats.json` |
| Field-weighted BM25 retrieval over six statutes | recall@4 0.78→0.997 own-denominator | 690 held-out, CPU | `HANDOFF.md` item 1, `configs/retrieval/field_weights.yaml` |
| 1.13B + harness SFT + tuned retrieval + per-template calibration | citation recall 0.3656 (83/227), precision 0.61, abstention 9/9, lookup 0.9868 | 690 held-out | `HANDOFF.md` item 2 |
| One gated RSI LoRA round on the 1.13B (64 modules, attention + Mamba mixer) | 0.3656→**0.4934** (+12.8 pp), abstention 9/9, cite-to-title −2.2 pp | `rsi_round1_1b/` 30 MB | `HANDOFF.md` item 3 |
| RSI round 2 correctly gate-rejected | probe regression 31.5% vs 15% threshold, no dev gain | — | commit `ee2f179` |
| Gateway + Cloudflare Worker + Vercel UI (M6 prong 2) | deployed, Playwright-verified | — | commits `58d37e4`, `43dd72a` |
| Nyāya kernel: pañcāvayava, vyāpti, five hetvābhāsa classes | 600/600 exact verdict on derived gold, five mutation checks killed | — | `prabhasa-nyaya/ADR-0001` table |
| 33 hand-authored `law_apply` element-negation items across six acts | — | `data/eval/law_apply/` | commit `8618901` |

### 3. Regressed

| # | Regression | Evidence |
|---|---|---|
| R1 | Semantic round-trip fidelity **0.13 pass @0.70** (CI 0.078–0.210), untouched since 2026-07-13 | `m5_benchmarks.json` |
| R2 | Closed-book law knowledge ≈ 0 (1/227); the F11 fix proved it is knowledge, not termination (byte-identical before/after) | `HANDOFF.md` item 4 |
| R3 | `main` (164c66e) vs `h-ord/phase1` (8618901): **87 commits apart**; RSI/LoRA/F11 work on `assistant/trackB/item4-sft-refit`, not on `main` | `git log main..h-ord/phase1`, `HANDOFF.md` |
| R4 | 223 GB of checkpoints (four distinct 1.13B) in one copy at `/home/ss/fusion-project/prabhasa-samskrutam/data/checkpoints/`, 33/35 `.pt` root-owned; HF holds only m2/m3 | `docs/decisions/reports/2026-09-12-checkpoint-inventory.md` |
| R5 | No Track B model can be evaluated by lm-eval or served by transformers (byte-vocab-256 Megatron blob, no HF export) — every public law number in the ledger is Qwen's | M7 plan §4 |
| R6 | torch.compile broken in the 5090 image (InductorError) | `m4_train.json` compiled=false |
| R7 | `feat/m6-instruct-and-app` **is** merged (memory note wrong); the app's live status is undocumented | refuter `git merge-base` |
| R8 | **Hidden dependency on an unmerged branch**: Track B's `scripts/rsi_round_1b.py:49-52` imports `loop`, `evaluate`, `retrieval_tuning` from `prototypes.nyaya_ttt_rsi`, which exists only on pravrudhi branch `claude/ttt-llm-research-0f1adf`; `loop.py` and `g0/lora_megatron.py` are in **no** prabhasa-samskrutam branch. The 0.4934 result is reproducible only while that research worktree exists | `git ls-tree claude/ttt-llm-research-0f1adf prototypes/nyaya_ttt_rsi`, inventory (F) |

### 4. Digressed (effort that did not move the mission)

| # | Digression | Evidence | Salvageable |
|---|---|---|---|
| D1 | H-ORD order-tax program R0–R3 (18+ commits, 35.8 GB of checkpoints), never evaluated on any law task, no adopt/reject decision (ADR-0012 requires one) | `hord_r1/r2/r3`, `research/findings/r0-order-tax.md` | a paper finding (P1 tax −60% at 1.13B); nothing for the product |
| D2 | VĀK-YANTRA five-organ design, 307 lines, zero code | `docs/vak-yantra-architecture.md` | park by ADR |
| D3 | Templated `law_citation_retrieval`: header weighting 6× lifts recall@4 to 0.98 *because the questions are templated*; the 0.49 headline measures harness + selector, not knowledge | `HANDOFF.md` attribution paragraph | internal gate only |
| D4 | Two RSI LoRA rounds on a model with ≈0 closed-book knowledge | `HANDOFF.md` item 3 | the gate discipline transfers |
| D5 | kāraka probe at 0.994 still a panel headline | spec §2.1 | regression tripwire only |
| D6 | The mission-critical inputs nobody built: law corpus in the repo < 1 MB; no English legal instruction data beyond templates; the Sanskrit-generation slices (`sa2en`/`en2sa`/`graph2sa`, 4,507 examples = 18% of instruct_v1) trained a language the product will not speak | inventory (D), `m6_instruct_v1_stats.json` | drop those slices |

### 5. What blocked the *old* 3B plan (all confirmed) — and what the redefinition does to each

| Blocker | Under "scale the byte-level model" | Under decision 1–2 |
|---|---|---|
| No 3B, no path from 1.13B (re-init + re-pretrain; corpus 5.25B bytes, 11× short of Chinchilla) | fatal | gone — base is pretrained |
| Byte vocab 256 vs 131k–151k BPE: logit distillation impossible; `scripts/m5/distill.py` never run | fatal | gone — sequence-level, verifier-filtered distillation only |
| No HF export → no lm-eval, no transformers serving | fatal | gone — HF-native base |
| 48 GB L40S: full FT of 3–4B with fp32 Adam ≈ 36–48 GB before activations | tight | LoRA r≤64 on a bf16 4B base ≈ 8 GB weights + activations; fits (estimate; preflight required) |
| Loom: only `sft` executable; no RunPod transport; Track B never used Loom | blocker | still a blocker — but `sft_binding()` already accepts exactly what this plan needs (HF snapshot + LoraRecipe) |
| Governance: non-goals §16, ADR-0006, ADR-0001 "no weights here" | blocker | three ADRs (Part II §9) |
| Container sm_120 image, arm64 lineage | blocker | gone — public CUDA 12.8 PyTorch image |

## Part II — the leap: Prabhāsa-Nyāya as an English law LLM with a Sanskrit-typed core

### 6. TRIZ framing (engine-backed)

Physical contradiction: the model must be **structurally Sanskrit** (kāraka roles, śābdabodha graph, pañcāvayava trace,
hetvābhāsa taxonomy, paribhāṣā meta-rules) *and* **natively English and broadly knowledgeable in law**. Separation by
**system level** (0.85) and **by condition** (0.85) rank first. Matrix cells: 35↔26 → principles 3, 35, 15; 39↔28 → 1, 10,
34, 28; 27↔20 → 36, 23.

- **System level**: the whole (Prabhāsa-Nyāya) is an English pretrained 3–4B; the parts that carry Sanskrit are a
  *typed intermediate representation* the model must emit and a checker that validates it. Sanskrit is the type system,
  not the surface language. This is exactly what the operator asked for and what the Codex spike found feasible:
  the graph schema has "no vibhakti, sandhi, or Devanagari requirement in the graph schema itself" (spike §5).
- **Condition**: the model asserts only what the trace licenses; otherwise it abstains with a typed reason
  (`missing_fact | missing_source | conflicting_authority | unsupported_rule`, spike §2.1).
- **Prior action (10) + replace the mechanical system (28)**: an HF-native base deletes R5, the exporter, the sm_120
  container and the tokenizer problem in one move.
- **Discard and recover (34)**: discard the byte-level lineage as foundation; recover ~20,733 of 25,240 instruct
  examples (`nyaya_check`, `en2graph`, `reason_chain`), the 690 held-out set, `law_apply`, the retrieval store, the
  scorer, the probe gold, the pure-Python graph/reason/bridge modules (inventory A–E: ~4,000 lines, all portable).
- **Feedback (23)**: Track A's Lean/Z3 checker filters distillation targets — only `grounded valid` traces are learned.
  Pratyabhijñā in the loop: the student learns only inferences the witness recognised.
- **Phase transition (36)**: the public number becomes the three-arm (c)−(b) lift on independent gold (LegalBench
  rule-application, IL-TUR LSI, own statute set), per the Track A protocol; the templated harness is an internal gate.

### 7. Base model and data (all on the box or on the Hub today)

| Base | Arch | License | Fit |
|---|---|---|---|
| `Qwen/Qwen3-4B-Base` | dense, 36 L, 3.6B non-embed | Apache-2.0 | strongest English/legal prior; transformers + PEFT + lm-eval native; Loom `sft_binding()` runs it today |
| `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | Mamba-2 hybrid, 42 L, `NemotronHForCausalLM` | Nemotron Open Model (commercial) | operator's Mamba lineage; English-only pretraining is now a plus; `use_mamba_kernels` needs `mamba_ssm` on the pod |
| `nvidia/Nemotron-H-4B-Base-8K` | Mamba-2 hybrid, 52 L | research-only | excluded for product use |

Recommendation: **Qwen3-4B-Base as arm 1, Nemotron-3-Nano-4B as arm 2**, decided by the ≤ 30-minute preflight the
house rules require plus a 2-hour closed-book `law_apply` + LegalBench-subset bake-off (≈ $3–5 per arm on an A40/A6000).

Data already available (no acquisition step):

| Source | Where | Size | Use |
|---|---|---|---|
| Six Indian statutes (Constitution, IPC, BNS, BNSS, Evidence, Contract) | `pravrudhi/research/nyaya/corpus/*.json` | 2.8 MB | retrieval passages, span grounding |
| CaseHOLD train/val/test | `.pravrudhi/ext_cache/casehold-*.csv` | 106 MB | holding-selection SFT + eval |
| IL-TUR LSI test + statutes | `.pravrudhi/ext_cache/iltur-lsi-*` | 118 MB | tier-1 eval (CC-BY-NC-SA: eval only) |
| LegalBench | `.pravrudhi/ext_cache/legalbench/`, `hf://datasets/nguha/legalbench` | — | rule-application eval + SFT of non-test splits |
| RegLab legal hallucinations | `.pravrudhi/ext_cache/reglab_legal_hallucinations.csv` | 424 MB | abstention training/eval |
| LawInstruct | `hf://datasets/lawinstruct/lawinstruct` | — | general legal instruction mix (license per sub-source) |
| instruct_v1 (English-side slices) | `prabhasa-samskrutam/data/sft/instruct_v1.jsonl` | 20,733 ex | graph + Nyāya trace format |
| law_v3 / held-out 690 / law_apply 33 | `data/sft/law_v3_train.jsonl`, `data/eval/*` | 6,206 / 690 / 33 | citation SFT + internal gates |
| Track A's per-rule Lean contracts (hearsay, personal_jurisdiction, …) and A1.2 round-trip graphs (230) | `prabhasa-nyaya/data/contracts`, P2.5 seed | — | checker for distillation filtering |

### 8. The typed IR (from the Codex spike, `docs/reviews/spikes/trackb-typed-ir/`)

Keep the existing vocabulary (`PadarthaCategory` DRAVYA/GUNA/KRIYA/SAMANYA/VISESA/SAMAVAYA/ABHAVA; `KarakaRole`
KARTR/KARMAN/KARANA/SAMPRADANA/APADANA/ADHIKARANA; Lean `Sorta` provision/authority/holding/court/party/act/element/
fact/remedy; Lean `Claim` constructors provides/cites/establishes/applies/satisfies/binds/entitles/absent) and extend the
graph with **English span grounding** (code-point offsets into immutable prompt text, tokenizer-independent),
**event-scoped role bindings** (one party can be KARTR in one event, SAMPRADANA in another), **explicit propositions**,
a **pañcāvayava trace** whose `udaharana` names a trusted versioned `rule_id` (the model never supplies rules), a
hetvābhāsa verdict, `cited_sections`, and an `abstain` flag with a typed reason. Emission is a fixed line grammar
(`NODE`, `ROLE`, `ELEMENT`, `CLAIM`, `TRACE`, `CITE`, `ABSTAIN`) a 4B model can produce reliably; a deterministic
parser expands it; Lean's existing refusal order (`refuted → refusedUngrounded → refusedIncomplete → grounded`) checks it.

Four schema defects the spike found that must be fixed before any SFT data is generated: labels forbid spaces so English
spans cannot live in node labels (`types.py:19`); roles are global node attributes, not per event; Pāṇinian
cardinalities are hard-coded (one KARTR); both Python checkers ignore `sapaksa`, so a fabricated udāharaṇa passes
(`verify.py:62`). And one Lean gap: entailment does not derive `applies` from `satisfies` — rule application needs the
typed-rule extension P2.5 already plans.

### 9. Plan, budget, gates

Stages — each a Loom program (`sft` executes today; `evaluate`/`distill`/`promote` bindings are the Studio work in §10):

| Stage | Job | Data | Gate (pre-registered before the run) | Est. cost on A40/A6000 ($0.5/h) |
|---|---|---|---|---|
| P0 preflight | 30-min run per arm: peak VRAM, tok/s, $/1k steps | 1k records | file written to `research/preflight/` | $1 |
| P1 bake-off | closed-book `law_apply` + LegalBench rule-application subset, both arms, base weights | — | choose arm; record both | $5 |
| P2 structure SFT (LoRA r=64, seq 2048) | typed-IR emission | instruct_v1 English slices (20.7k) + law_v3 (6.2k) + CaseHOLD/LegalBench train splits rendered into the IR by the deterministic pipeline (§8) | parse rate ≥ 0.98; Lean `grounded` rate on held-out 690 vs base; abstention on the 9 absent-gold + RegLab slice; English MMLU-law within −1 pp of base | $10–20 (2–4 h × 3–5 runs) |
| P3 verifier-filtered distillation | teachers = claude-cli / codex / qwen-dashscope through Track A's three-arm harness; keep only `grounded valid` traces; student = P2 model | ≥ 5k filtered traces | (c)−(b) lift on LegalBench rule-application and IL-TUR; abstention rate; `law_apply` 33/33 as tripwire | $10–20 |
| P4 RSI round | Track B's gate loop retargeted to PEFT (self-labelled pool, probe regression ≤ 15%) | fresh statute questions | same as P3 | $5–10 |
| P5 evaluate + promote | lm-eval on the HF model + Track A arms; ledger admission via `parse_prabhasa_panel` | — | ADR-recorded | $2 |

Total ≈ **$35–60 of the $200**, leaving headroom for reruns and the second arm. FLOP arithmetic, not measurement: the
house rules require every full run's hours and dollars to come from the P0 file.

### 9a. Amendment after the data-feasibility spike (2026-09-15 10:05 BST)

The Codex spike (`spikes/trackb-typed-ir/codex-gpt6-astra-data-feasibility-2026-09-15.md`) found that **no source on
the box converts deterministically into a full `nyaya-law-v1` record**: current verified yield is 0 of the 25k
target; at most 36 LegalBench diversity rows could be adapted with a human-authored rule contract (0.14%). What the
data does give: 915 statute lookups (IPC 557, BNS 358), 6,206 `law_v3` citation rows (provision-level overlap with
the 690 held-out set unaudited), CaseHOLD 5,314 holding selections (reserve as validation), LegalBench 75 train /
3,397 test rows across 15 tasks, IL-TUR 13,019 eval rows (4,699 truncated, entities redacted). The `nyaya_check` and
`reason_chain` slices of instruct_v1 are finite-world Sanskrit-pipeline artefacts and do not convert to legal proofs.

Consequences for §9 (the lead is told the same):

1. **P2 splits in two.** P2a = *supervision authoring*: a trusted rule registry (typed premises, exceptions, source
   spans — the spike lists the entries needed for IPC 405/416/182 and BNS 85/69/47), controlled fact generators, and
   a reviewed contract per rule family; P2b = the structure SFT as before, but its data is (i) mechanical auxiliaries
   (lookup, citation, holding selection, abstain-on-missing-source) as a lower tier and (ii) checker-accepted
   teacher-converted records. Teacher conversion therefore moves *ahead* of SFT: the verifier-filtered distillation of
   P3 is how P2b's data is made, not a later stage.
2. **Yield is a gate, not an assumption.** Before any GPU run, a prereg states the record count per tier with the
   checker's acceptance rate measured on ≥ 200 teacher proposals; "25k" is retired as a target until that number
   exists.
3. **Leakage rules** (spike §5) are binding: all 33 `law_apply`, all LegalBench test, all IL-TUR test, all CaseHOLD
   validation excluded from SFT with group-level dedup; `law_v3` joined against the 690 by provision id before use.
4. The line grammar needs two additions the spike found missing: a rule-free abstention spelling and native task
   kinds for holding selection and multi-label statute identification.

### 10. Scaffolding the team must build before kick-off (Studio RSI work, not hand-work)

| Item | Where | Owner |
|---|---|---|
| RunPod transport: pod lifecycle, volume mount, checkpoint sync, `get-billing` ledger, one-pod guard | `pravrudhi/src/pravrudhi/hosts/transports.py` | Studio |
| Loom bindings `evaluate` (ext_eval.sh/lm-eval), `distill` (teacher sampling + Track A filter), `promote` (ledger policy); `continue_pretrain` deferred | `loom_pipeline.py` STAGE_EXECUTABILITY | Studio |
| Migrate inventory A–E + tests (pure Python, ~4,000 lines + 25 test files) into `prabhasa-nyaya/src/prabhasa_nyaya/` ; reimplement `sequence_nll.py` (68 lines) against `AutoModelForCausalLM` | prabhasa-nyaya | Track B |
| Fix the four schema defects (§8) and add span/event/rule fields; regenerate SFT data through the fixed pipeline | prabhasa-nyaya | Track B |
| Rescue R8: copy `prototypes/nyaya_ttt_rsi/{loop,evaluate,retrieval_tuning}.py` from the research branch into prabhasa-nyaya under its own commits | prabhasa-nyaya | Track B |
| Checkpoint backup of the four 1.13B + LoRA (HF private under AxisMeru or a RunPod volume) | — | lead |
| Freeze prabhasa-samskrutam: `h-ord/phase1` = research record; ADR parks H-ORD/Vāk-Yantra | prabhasa-samskrutam | lead |
| ADRs: (a) prabhāsa-nyāya = English law LLM with Sanskrit-typed IR, home repo prabhasa-nyaya, amends ADR-0001 decision 1/6; (b) supersedes non-goals §16 + ADR-0006 gate for this line; (c) public number = independent gold only; (d) RunPod house rules adopted into TEAM-RULES | both repos | lead, drafts from this session |

### 11. What this review does not claim

No throughput, VRAM or dollar figure above is measured; Devanagari tokenizer efficiency is now irrelevant and was
not measured; the IR schema in §8 is a design, not code; the operator's redefinition is recorded here and in memory,
not yet in any ADR. The lead has not been sent anything: the operator will say when.
