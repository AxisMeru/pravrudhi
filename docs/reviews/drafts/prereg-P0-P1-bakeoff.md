# Pre-registration — P0 preflight and P1 base-model bake-off (DRAFT, reviewer, 2026-09-15)

Status: DRAFT for the lead's T7. Frozen (sha recorded in `research/prereg/`) before any GPU minute is spent.
House rules apply (`docs/decisions/RUNPOD-HOUSE-RULES.md`): one pod, ≤ L40S class, preflight file before any full run.

## Arms

| id | model | revision (pin before run) | license |
|---|---|---|---|
| Q | `Qwen/Qwen3-4B-Base` | sha to pin | Apache-2.0 |
| N | `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` | sha to pin | NVIDIA Nemotron Open Model License |

Both loaded with `AutoModelForCausalLM`, bf16, `trust_remote_code` only for N at the pinned revision; N needs
`mamba_ssm` + `causal_conv1d` for `use_mamba_kernels=true`, otherwise the slow path (record which).

## P0 — preflight (per arm, ≤ 30 min, on the cheapest 48 GB card in stock: A40 / RTX A6000 / L40 / L40S)

Measured and written to `research/preflight/<date>-<arm>-<card>.json`:

1. load wall-clock, resident VRAM after load (bf16);
2. inference: sequence-NLL of 64 held-out prompts at seq 2048, tokens/s;
3. LoRA r=64, α=128, dropout 0.05, targets = all linear projections (Q: q/k/v/o/gate/up/down; N: in_proj/out_proj/
   qkv/proj + MLP), gradient checkpointing on, seq 2048, micro-batch 2, grad-accum 8, 50 optimizer steps of AdamW
   (lr 1e-4, bf16 params, fp32 master for LoRA weights only): peak VRAM, steps/s, $/1k steps at the card's list price;
4. resumable checkpoint written at step 25 and restored at step 26 (loss continuity ± 1e-3) — house rule 15;
5. rsync of that checkpoint to the network volume and to the 5090, sha256 identical — house rules 16/18.

**Decision rule P0:** an arm passes if peak VRAM ≤ 40 GiB on the 48 GB card and steps 4/5 succeed. (Lead's ruling 2026-09-15: the 48 GB class is the operator-set floor and is not relaxed here; the 5090 spike's 17.85 GiB peak on Qwen3-4B, `spikes/trackb-typed-ir/preflight-5090/preflight_5090_qwen3-4b_run2.json`, is recorded as headroom evidence only.) A failing arm is
retried once at micro-batch 1; a second failure drops the arm (recorded, not silently).

## P1 — bake-off (per arm, base weights, no fine-tuning; ≈ 2 h)

Measures, all with the same prompt template and greedy decoding, scored by the existing deterministic scorers:

| measure | set | n | metric |
|---|---|---|---|
| closed-book element application | `law_apply` items + controls | 33 (+3) | exact verdict accuracy, control pass rate |
| retrieval-grounded citation (harness, internal) | held-out 690, BM25 top-4 shown | 690 | citation recall/precision, abstention on the 9 absent-gold items, `grounded` |
| rule application, independent gold | LegalBench rule-application tasks Track A already transcribed (hearsay, personal_jurisdiction, …) | per task n | accuracy, abstention |
| English/legal prior | `mmlu_professional_law` via `ext_eval.sh` (lm-eval, admitted `--tool lm-eval`) | 1,534 | acc |
| IR emission (zero-shot, 5-shot from the goldens) | 20 golden records | 20 | parse rate of the line grammar |

**Decision rule P1 (pre-registered):** choose the arm with the higher mean rank across the five rows; ties broken by
`law_apply` accuracy, then by P0 $/1k steps. Both arms' numbers are recorded and reported regardless. Neither arm's
P1 number is a product claim: this is base-model selection, labelled model-measured at n given.

## What is not decided by this prereg

Nothing about P2+ (structure SFT, distillation) — separate preregs, each after the previous gate's file exists.

## Cost cap for P0+P1

≤ $15 total on an A40/A6000 (≈ $0.5/h × ≤ 2.5 h × 2 arms + volume); abort at $20.
