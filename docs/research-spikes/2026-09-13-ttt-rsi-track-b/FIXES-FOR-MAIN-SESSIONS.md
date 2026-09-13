# Fixes and findings for the main sessions (from the isolated `ttt-lab` work, 2026-09-13)

Everything below was done in an isolated container (`ttt-lab`, started from the same
`rtx5090-train:latest` image with an 8 GiB RAM cap, my worktree mounted at `/lab`,
`/fusion-project` and `~/projects/prabhasa-samskrutam` mounted **read-only**). Nothing here
touched the running `rtx5090-train`, the two engine containers, or any repo other than this
branch. Each item says what is broken, what fixed it, and what the owning session should do.

## F1. `peft` is unusable in the `rtx5090-train:latest` image (`torchao` version pairing)

- **Symptom:** `from peft import get_peft_model; get_peft_model(model, LoraConfig(...))` raises
  `ImportError: Found an incompatible version of torchao. Found version 0.11.0+git, but only
  versions above 0.16.0 are supported` (peft 0.19.1 → `peft/import_utils.py:143`). Any LoRA
  recipe using peft in that container is dead on arrival; only hand-rolled adapters work.
- **What does not work:** `pip install -U torchao` (pulls the newest torchao, which imports
  `torch.nn.functional.ScalingType` and needs torch ≥ 2.11; the image ships torch
  `2.8.0a0+...nv25.06`). Import of torchao itself then fails.
- **What works (verified in `ttt-lab`):** `pip install 'torchao==0.16.0'`. torchao logs
  "Skipping import of cpp extensions due to incompatible torch version" (its CUDA kernels are
  off, which peft's plain LoRA path never needs) and `get_peft_model` succeeds.
- **Owner action (Track B / whoever maintains the image):** add `torchao==0.16.0` to the
  image's Dockerfile (`/home/ss/rtx5090setup/docker/Dockerfile`) or run the pin once inside
  `rtx5090-train`. Check first that nothing in that container depends on torchao 0.11's cpp
  kernels (quantized inference would; plain SFT/LoRA does not).

## F2. Track B's model cannot load in `rtx5090-train:latest`; only `prabhasa/nemo-5090:26.02` has the stack

- **Symptom:** `import mamba_ssm` and `import megatron.core` both fail in `rtx5090-train:latest`
  (the container that has been running for two days with the HF cache and `/fusion-project`
  mounted). NemotronH (both the 370M custom-loop line and the 1.13B Megatron line) needs
  `mamba_ssm`; the 1.13B loader also needs Megatron-Core.
- **What works:** `prabhasa/nemo-5090:26.02` (torch `2.10.0a0+...nv25.11`, `mamba_ssm 2.3.1`,
  `megatron.core`, `transformers 4.57.6`, `peft 0.13.2` + `torchao 0.14.0` — and in *this* image
  peft imports cleanly, so F1 is specific to the `rtx5090-train` image).
- **Owner action:** any Track B doc that tells a reader to "use the training container" should name
  the image; `rtx5090-train` is a general-purpose box, not the Track B stack. The isolated
  `ttt-lab` container used for this prototype is `prabhasa/nemo-5090:26.02` with the worktree at
  `/lab`, Track B repo at `/trackB` (ro), `~/.local/share/prabhasa-samskrutam` at `/trackB-local`
  (ro), `--memory 8g --memory-swap 8g` (measured 370M/1.13B RSS is ≈3–5 GiB per Track B's own G0
  notes, so 8 GiB is a cap that fails fast instead of thrashing the host).
