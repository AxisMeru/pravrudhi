# RunPod house rules — binding for every agent in every session (operator, 2026-09-15)

These rules come from the operator's answers of 2026-09-15 08:35 BST and the RunPod facts read from the
account that morning (0 pods, 0 network volumes, $0 spend in the prior 14 days). They apply to every Claude,
Codex or CLI agent on this box and to every repo under AxisMeru. Break one and the pod is killed and the run is
not evidence.

## 1. Money

1. **Balance: $200 loaded, meant to last ~10 days at 24×7 on one ~$1/h GPU.** There is no top-up to assume.
2. **One pod at a time, account-wide.** Before `create-pod`, run `list-pods`; if anything is listed (running
   *or* stopped), do not create another. Stopped pods still bill disk.
3. **GPU class: L40S (48 GB) or cheaper, and — operator amendment 2026-09-15 10:50 BST — 24 GB cards are
   allowed when in stock and the job fits.** Principle: *do well and do more with the least spend.* Pick the
   cheapest card whose measured peak VRAM (preflight file) is ≤ 80% of the card and whose CUDA version fits;
   24 GB candidates seen on 2026-09-15: RTX 4090 community $0.34/h / secure $0.74/h, L4 secure $0.49/h,
   RTX 3090 secure $0.50/h; 48 GB: A40 secure $0.49/h, RTX A6000 secure $0.53/h, L40 $0.82/h, L40S $0.79–1.09/h.
   Community cards are reclaimable, so rule 15's ≤30-minute checkpoints are what make them acceptable.
   Never an 80 GB or Hopper/Blackwell card for this line.
3a. **Daily cap = one L40S running 24 h ≈ $24/day (≈ $1.01/h).** Spending faster than that on any day, on any
   mix of cards, needs the lead's written go and the operator's knowledge.
4. **Spend ledger.** Every pod creation, stop and delete is recorded (who, why, gpuTypeId, $/h, start, stop,
   measured cost from `get-billing`) in `docs/decisions/runpod-ledger.md` of the repo that owns the run.
   Report cumulative spend against the $200 in every status message that mentions a pod.
5. **Hard stops:** pause everything and tell the lead at **$100 spent**; nothing new above **$180**; the last
   $20 is reserved for pulling checkpoints off the volume.

## 2. Who may start GPU work

6. **Nobody starts training or a pod until the operator says "kick off" to the lead.** Reviewer/spike sessions
   (this review, the Track A review) write plans, scripts and preflight *manifests*; they do not create pods.
7. The lead gives the go per job, in writing (message id), naming: gpuTypeId, image, volume, expected hours,
   expected $, the Loom program or script, the kill condition. No go, no pod.
8. The RTX 5090 is not a fallback for this line. It serves local models and Track A arms only.

## 3. How a run is shaped

9. **Checkpoints never live only on a pod.** A network volume (≤ 200 GB, same datacentre as the pod) holds
   inputs and outputs; every run syncs its final checkpoint to the volume *and* to an off-RunPod location
   (HF private repo under AxisMeru, or this box) before the pod is deleted.
10. **Preflight before commit:** a ≤ 30-minute run that measures peak VRAM, tok/s and $/1k steps on the chosen
    card, written to `research/preflight/<date>-<job>.json`. The full run's hours and dollars are stated from
    that file, never from arithmetic.
11. **Kill conditions are pre-registered** (loss NaN, probe regression, wall-clock, $) and enforced by the job
    itself, not by a human watching.
12. **Images:** HF-transformers-native jobs use a public CUDA 12.8 PyTorch image; no Megatron/NeMo container is
    pushed to RunPod for this line. `trust_remote_code` only for the pinned base model revision.
13. Every job is a Loom program (`*.loom`) or, until the bindings exist, a single script under `scripts/runpod/`
    whose manifest (image, command, mounts, env, expected cost) is committed before the run.

## 3a. Checkpoint durability (operator, 2026-09-15 08:50 BST)

15. **Checkpoint often.** Every training job saves a resumable checkpoint (weights + optimizer + step + data cursor)
    at least every 30 minutes of wall-clock or every 200 steps, whichever is sooner, and at every gate boundary.
    A RunPod pod can break, disconnect, be stopped or be paused at any time; a run that cannot resume from its last
    checkpoint within one pod restart is a house-rule violation, not bad luck.
16. **Ship each checkpoint off the pod as it is written.** Target order: (1) the attached network volume (same
    datacentre), then (2) **the RTX 5090 box** via `rsync` over SSH into `/home/ss/fusion-project/prabhasa-nyaya/checkpoints/<run>/`
    (the 5090 is the durable local store; training there is still forbidden by rule 8). Keep the last 3 resumable
    checkpoints on the volume and on the 5090; prune older ones on the pod first.
17. **Milestones go to Hugging Face, public.** At every pre-registered gate pass and at run completion, the
    checkpoint (LoRA adapter or merged weights, plus the preflight/gate JSONs and a model card stating tier and n)
    is pushed to a **public** repo under `AxisMeru` (HF private storage is near its limit and is not to be used).
    Publish only what a public model card can honestly describe; interim/rejected checkpoints stay on the volume and
    the 5090, never on HF. The lead decides the milestone list per run; when unspecified, the reviewer decides.
18. **Before `delete-pod`:** confirm the final checkpoint's sha256 matches on the volume, on the 5090 and (for
    milestones) on HF. Then delete. A pod is never the last copy of anything.

## 4. Discovery

14. This file is the canonical statement. It is mirrored in `~/.claude/CLAUDE.md` (loaded by every session on
    this box) as a five-line summary pointing here, and the lead appends the same summary to
    `docs/decisions/TEAM-RULES.md`. A session that cannot find this file must ask the lead before touching RunPod.
