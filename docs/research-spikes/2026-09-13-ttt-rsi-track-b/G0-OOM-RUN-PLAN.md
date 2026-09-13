# G0 OOM run plan: `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` (allocator-fragmentation test)

Prepared 2026-09-13 by the operator-interface session. **No GPU run has been executed for
this plan.** This is the run Track B pre-registered in `998d913` and has not yet fired.
Do not execute until the lead says "GPU is yours" (this task's own instruction) **and**
`nvidia-smi` shows the card idle at the time of launch — the plan does not authorize itself.

## 0. Host state observed while writing this plan (2026-09-13)

`nvidia-smi`: GPU idle, 18 MiB / 32607 MiB used — clear right now. `free -g`: **1 GiB free,
3 GiB available, swap 7/7 GiB full** — this is materially worse than the ~12 GiB-available
floor Track B's own docs set as the go/no-go gate, and matches the exact shape of the
2026-09-10 host-collapse precedent (stack a job onto an already-swap-saturated host). **Do
not launch until `free -g`'s `available` column clears at least ~8 GiB and swap has
headroom** — re-check both `nvidia-smi` and `free -g` immediately before `docker run`, not
from this snapshot.

## 1. Exact command

Track B's own precedent for this image/mount pattern is `scripts/hord/run_r2_5090.sh`
(`docker run --rm --gpus all --ipc=host -v "$REPO:$REPO" -w "$REPO" ... prabhasa/nemo-5090:26.02`).
Adapted to this task's isolation requirements (repo read-only, separate output mount,
memory-capped):

```bash
REPO=/home/ss/projects/prabhasa-samskrutam            # host path, mounted read-only at /trackB
FUSION=/home/ss/fusion-project                        # host path, mounted read-only at /fusion-project
OUT=/home/ss/projects/pravrudhi/.claude/worktrees/ttt-llm-research-0f1adf/prototypes/nyaya_ttt_rsi/runs/g0_expandable
mkdir -p "$OUT"

docker run --rm \
  --gpus all --ipc host \
  --memory 12g --memory-swap 12g \
  -v "$REPO:/trackB:ro" \
  -v "$FUSION:/fusion-project:ro" \
  -v "$OUT:/out:rw" \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  prabhasa/nemo-5090:26.02 \
  python3 /trackB/scripts/g0/sft_megatron_batched.py \
    --checkpoint /fusion-project/prabhasa-samskrutam/data/checkpoints/m4/final.pt \
    --repo-root /trackB \
    --config /trackB/configs/train/nemotron_h_1b.yaml \
    --sft-mix /trackB/data/sft/m7_mix_v1.jsonl \
    --n-steps 40 --batch-size 8 --seq-len 512 --lr 1e-5 --seed 42 \
    --warmup-steps-excluded 5 --vram-fraction 0.85 \
    --pad-to-fixed-len \
    --out /out/dry_run_1p13b_expandable.json
```

**No wrapper script needed.** `sft_megatron_batched.py`'s argparse already exposes `--out`
(the only path it writes — no checkpoint is saved by this dry run) as a free CLI flag, and
`--checkpoint`/`--repo-root`/`--config`/`--sft-mix` are all independently settable, so every
input can point into the two read-only mounts while the one write goes to `/out`. Pass
`--config` explicitly rather than relying on the loader's auto-resolution — see F6 in
`FIXES-FOR-MAIN-SESSIONS.md` for why that matters specifically under this dual-mount layout.
One variable changed from `ed3f327`'s run (`PYTORCH_CUDA_ALLOC_CONF`); every other flag value
is copied from that run's own invocation (`--pad-to-fixed-len`, batch 8, seq_len 512,
vram_fraction 0.85, same 12g/12g cap) per the pre-registration's own requirement.

## 2. Confirm/falsify criteria (verbatim from `998d913`, `docs/plans/2026-09-12-g0-fixed-len-prediction.md` §5)

**Confirmed**: "`step_memory_allocated_mib` stays flat step over step (each value within
ordinary noise of the first, not trending upward) for meaningfully more than the single step
`ed3f327` managed — ideally reaching a run of several steps, ultimately all 40, without OOM."
→ Follow-up: expandable_segments becomes a standing flag for future Megatron-line runs on
this checkpoint family; proceed to the real 3-epoch SFT run planning (§5 of the base G0
plan), reusing this container/mount pattern.

**Still falsified**: "`step_memory_allocated_mib` keeps climbing step over step at a
comparable rate to `ed3f327`... meaning the allocator's reserved-but-unallocated pool was not
the operative problem." → Follow-up per the doc: "the per-step series should be reported and
this line of testing should stop again rather than guessing a third knob" — i.e. do not try
a fourth allocator flag on spec; escalate to isolating the accumulating buffer itself
(profiler-level inspection of the Megatron/Transformer-Engine/Mamba2 call path), a different
class of investigation than a run-flag test.

**Middle outcome** (further than step 1, still OOMs before step 40): "not a clean
confirmation or falsification... should be reported as exactly that (how many steps, the
per-step series, where it OOM'd) rather than forced into either bucket." → Follow-up: report
the partial series, then treat as informative-but-inconclusive input to the batch-4 /
grad-accum-2 test in §3 below, not as grounds to declare either hypothesis resolved.

## 3. Static memory estimate vs. 32 GiB, and the batch-4/grad-accum-2 fallback

Params + grad + 2 Adam fp32 moments, all fp32 (the script's dtype for the optimizer state;
`n_params=1,129,634,752` measured): `1,129,634,752 × 4 × 4 bytes ≈ 16.83 GiB`. Measured
preflight peak at batch 8 × seq 512 was **21.6 GiB** (`research/g0/dry_run_1p13b.json`), so
activations account for ≈4.8 GiB of that — consistent with the fixed-length run's own step-0
number (17.30 GiB *allocated*, growing to 27.42 GiB total process usage by the step-1 OOM).
Against the 32 GiB card (26.65 GiB usable at 0.85 fraction): the static 16.83 GiB floor
leaves only ~9.8 GiB for activations + allocator overhead + fragmentation before hitting the
fraction cap — tight, and exactly the margin the fragmentation hypothesis is testing.

**Batch 4 / grad-accum 2 is a legitimate second test** if expandable_segments alone doesn't
fix it: it holds the effective batch size (and thus the loss/gradient statistics) constant
while halving the worst-case activation memory per forward pass, isolating whether the
climb is activation-driven (would shrink) or from a per-step-invariant leak (would not).
`sft_megatron_batched.py` as written has **no `--grad-accum` flag** — only `--batch-size`.
Implementing grad accumulation would require either a small patch to `run_batched_dry_run`
(accumulate `loss` across `grad_accum_steps` micro-batches before `opt.step()`, matching
`scripts/m7/sft_batched.py`'s own pattern per the base G0 plan §3) or, for a same-day probe
with zero script changes, simply rerunning with `--batch-size 4` alone (not equivalent to
grad-accum-2 for the loss statistics, but isolates the activation-memory variable just as
well for a pure OOM/no-OOM read). Record which variant was actually run — they are not
interchangeable for anything beyond this OOM question.

## 4. Bug found

Recorded as **F6** in `FIXES-FOR-MAIN-SESSIONS.md` in this same directory: `eval_adapter.
load_megatron_blob`'s default config resolution (`_find_repo_config`) walks up from the
`--checkpoint` path, not from `--repo-root` — under this task's two-separate-mount layout
(`/fusion-project` for the checkpoint, `/trackB` for the repo/scripts), an unset `--config`
would silently resolve against `/fusion-project`'s own mirrored `configs/` tree instead of
`/trackB`'s (currently identical byte-for-byte, verified by `diff`, but nothing enforces that
they stay identical). The command in §1 above avoids this by passing `--config` explicitly.

## Files

- Plan: `/home/ss/projects/pravrudhi/.claude/worktrees/ttt-llm-research-0f1adf/docs/research-spikes/2026-09-13-ttt-rsi-track-b/G0-OOM-RUN-PLAN.md`
- Bug record: `/home/ss/projects/pravrudhi/.claude/worktrees/ttt-llm-research-0f1adf/docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md` (F6)
