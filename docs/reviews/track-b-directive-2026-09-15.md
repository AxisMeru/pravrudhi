# Directive to `lead` — Track B redirect: Prabhāsa-Nyāya as the English law LLM (2026-09-15)

From: the Track B adversarial-review session (desktop, `track-b-adversarial-review-a73cee-f8`), on the operator's
instruction of 2026-09-15 09:30 BST ("trigger it with the lead … you have precedence … your role is to work through
them and through codex astra for your own parallel actions/research").

Authority: the operator asked for this directive and gave the reviewer precedence on Track B direction, gates and
prereg. You keep execution authority and your own judgement; where you disagree, say so and record it.
Full evidence: `/home/ss/projects/pravrudhi/.claude/worktrees/track-b-adversarial-review-a73cee/docs/reviews/`
(`track-b-adversarial-2026-09-15.md` Part I = audit, Part II = plan; `spikes/trackb-typed-ir/` = Codex typed-IR
spike + asset inventory; `runpod-house-rules-2026-09-15.md`). Read-only for you; copy what you need under your own
commits. The branch is never merged.

## 0. Channel

- Reviewer → you: `SendMessage` to `lead`. You → reviewer: `SendMessage` to `track-b-adversarial-review-a73cee-f8`
  (desktop-to-desktop delivery works; if a send is held, write to `docs/reviews/liaison-log-track-b.md` on your side and
  say so in the next message that gets through).
- First message wanted from you: acknowledgement that you have read this file and Part II §6–10, plus your reading of
  §2 below (which items you dispatch first and to whom).
- Cadence: one status per merged range and at least every 2 hours while Track B is active: what merged (range,
  files), numbers with n and file path, what is blocked, what is next. Findings are provisional until the reviewer
  clears them (same rule as Track A).

## 1. Operator decisions now in force (2026-09-15, 08:35 and 08:50 BST)

1. **Prabhāsa-Nyāya is an English-focused, law-focused LLM** (3–4B, pretrained base). Sanskrit is **not** a target
   language; prabhāsa-saṃskṛtam's PoC is done. Sanskrit's lexical/grammatical/ontological machinery (kāraka roles,
   śābdabodha typed graph, pañcāvayava trace, hetvābhāsa taxonomy, paribhāṣā) is the **typed intermediate
   representation** the model emits and Track A checks.
2. **The model line lives in `prabhasa-nyaya`**, taking every relevant asset from `prabhasa-samskrutam`.
3. **RunPod house rules are binding** (`~/.claude/CLAUDE.md` summary; full text `docs/decisions/RUNPOD-HOUSE-RULES.md`
   in the pravrudhi checkout): $200 total, **one pod account-wide**, L40S or cheaper 48 GB (A40 $0.49/h, RTX A6000
   $0.53/h are the current cheapest), pause at $100, nothing new above $180; checkpoint every ≤30 min/200 steps,
   rsync to the network volume **and** the 5090 (`/home/ss/fusion-project/prabhasa-nyaya/checkpoints/<run>/`);
   milestones to a **public** HF repo under AxisMeru; a pod is never the last copy.
4. **Kick-off is now given** for the *scaffolding*. GPU/pod work still needs your written per-job go (card, image,
   volume, hours, $, kill condition) and a P0 preflight file first (house rule 10). Loops are stood down for this spike;
   the reviewer may use the 5090 for its own spikes subject to memory (`free -h` available ≥ 8 GiB; one GPU job).

## 2. What to dispatch, in order (owner in brackets)

| # | Deliverable | Done when |
|---|---|---|
| T1 | **ADR-0005 in prabhasa-nyaya**: Prabhāsa-Nyāya = English law LLM with Sanskrit-typed IR; amends ADR-0001 decisions 1 and 6 (weights *are* trained here; the prabhāsa model is one vendor arm of the same harness); supersedes prabhasa-samskrutam `non-goals.md §16` and ADR-0006's token gate *for this line*; parks H-ORD / Vāk-Yantra as research; adopts the RunPod house rules into TEAM-RULES [lead; reviewer drafts if you want it — say so] | ADR merged on `main`, TEAM-RULES updated |
| T2 | **Checkpoint backup** of the four 1.13B and the RSI LoRA (`/home/ss/fusion-project/prabhasa-samskrutam/data/checkpoints/{m4,hord_r2,hord_r3,g0_sft_round1,rsi_round1_1b}`) to public HF repos under AxisMeru with honest cards, or at minimum sha256-verified second copies [Track B] | sha256 list committed |
| T3 | **Rescue R8**: `prototypes/nyaya_ttt_rsi/{loop,evaluate,retrieval_tuning}.py` and `g0/lora_megatron.py` from pravrudhi branch `claude/ttt-llm-research-0f1adf` copied into `prabhasa-nyaya` under its own commits, with the 76 host tests; prabhasa-samskrutam's `scripts/rsi_round_1b.py:49-52` import path noted as dead [Track B] | tests green in prabhasa-nyaya |
| T4 | **Migrate inventory A–E + tests** (asset inventory in `spikes/trackb-typed-ir/asset-inventory-…md`): graph domain (types/validators/renderer), bridge `templates.py`, reason (nyaya/verify/z3/self_consistency), instruct/law data builders and the JSONL data, retrieval store/scoring/calibration, `law_apply`, held-out 690, scorer; **reimplement `sequence_nll.py` against `AutoModelForCausalLM`**; do not migrate tokenizer/, Megatron scripts, `data/packed`, lexicon/realizer [Track B] | `uv run pytest` green in prabhasa-nyaya; module map committed |
| T5 | **Fix the four IR schema defects** before generating any SFT data (spike §5): labels forbid spaces → store spans separately; roles global → event-scoped bindings; hard-coded one-KARTR → configurable frames; checkers ignore `sapaksa` (`verify.py:62`) → check it. Then the schema in spike §2.1 and line grammar §2.2, with a deterministic parser and round-trip tests [Track B, reviewer signs the schema] | schema + parser + tests merged; 20 golden records |
| T6 | **Studio**: RunPod transport in `pravrudhi/src/pravrudhi/hosts/transports.py` (pod lifecycle, volume, rsync sync to the 5090, `get-billing` ledger, one-pod guard from `list-pods`); Loom bindings `evaluate` (ext_eval.sh / lm-eval on an HF snapshot), `distill` (teacher sampling → Track A checker filter → JSONL), `promote` (ledger admission policy). `sft_binding()` already fits the plan [studio] | bindings marked executable in `STAGE_EXECUTABILITY` with tests; dry-run manifest for a P2 run |
| T7 | **Prereg for P0/P1** (bake-off): arms `Qwen/Qwen3-4B-Base` and `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16`; measures: peak VRAM, tok/s, $/1k steps; closed-book `law_apply` 33 and a LegalBench rule-application subset; decision rule written before the run [Track B, reviewer approves] | prereg sha sent to reviewer |
| T8 | Only after T1–T7: P2 structure-SFT run under a per-job go [lead go; Track B runs] | P0 file + gate JSONs |

## 3. Rules the reviewer will hold you to

- No number without a file; pipeline-measured labelled so; the templated citation harness is an **internal** gate,
  never the public number. Public number = three-arm (c)−(b) on independent gold (LegalBench rule-application, IL-TUR
  LSI, own statute set), scored per Track A's protocol.
- Track A's harness stays vendor-agnostic; the prabhāsa model enters it as one more `vendor` id.
- One GPU job at a time on the box; no pod without the written go; house rules 15–18 on every run.
- Merge ranges, not tips; verify a peer's claim before asserting it.

## 4. Reviewer's own parallel work (so you do not duplicate it)

- Drafts of ADR-0005 text and the P0/P1 prereg (sent to you for adoption).
- Codex gpt-6-astra spikes: (a) IR parser/serializer prototype against the fixed schema; (b) CaseHOLD/LegalBench →
  IR rendering feasibility; (c) sequence-NLL scoring on an HF 4B model on the 5090 (memory-gated).
- Reviewer does not touch RunPod and does not commit to `prabhasa-nyaya` main trees.
