# RunPod house rules — binding for every agent in every session (operator, 2026-09-15)

These rules come from the operator's answers of 2026-09-15 08:35 BST and the RunPod facts read from the
account that morning (0 pods, 0 network volumes, $0 spend in the prior 14 days). They apply to every Claude,
Codex or CLI agent on this box and to every repo under AxisMeru. Break one and the pod is killed and the run is
not evidence.

## 1. Money

1. **Balance: $200 loaded, meant to last ~10 days at 24×7 on one ~$1/h GPU.** There is no top-up to assume.
2. **One pod at a time, account-wide.** Before `create-pod`, run `list-pods`; if anything is listed (running
   *or* stopped), do not create another. Stopped pods still bill disk.
3. **GPU class: L40S (48 GB) or cheaper.** Cheaper 48 GB cards seen on 2026-09-15 that satisfy the same job
   shape (LoRA / SFT of a 3–4B model): A40 secure $0.49/h, RTX A6000 secure $0.53/h, L40 secure $0.82/h,
   RTX 6000 Ada secure $0.84/h, L40S community $0.79/h / secure $1.09/h. Pick the cheapest card whose VRAM and
   CUDA version fit the job; never an 80 GB or Hopper/Blackwell card for this line.
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

## 4. Discovery

14. This file is the canonical statement. It is mirrored in `~/.claude/CLAUDE.md` (loaded by every session on
    this box) as a five-line summary pointing here, and the lead appends the same summary to
    `docs/decisions/TEAM-RULES.md`. A session that cannot find this file must ask the lead before touching RunPod.
