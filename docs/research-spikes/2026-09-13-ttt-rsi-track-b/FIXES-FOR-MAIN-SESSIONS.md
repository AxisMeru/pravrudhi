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

## F5. Loading `m7_retry_checkpoint.pt` with `map_location="cpu"` OOM-kills in an 8 GiB container

- **Symptom:** `torch.load(ckpt_path, map_location="cpu", weights_only=False)` on the 4.2 GB
  `m7_retry_checkpoint.pt` blob — the exact pattern used by both
  `prabhasa.infrastructure.ml.inference.NemotronHRunner` (which loads to `self.device`, so it
  is only at risk when that device is `"cpu"`) and `scripts/m7/dry_run_sft.py::load_source_model`
  (which always hardcodes `map_location="cpu"`) — gets killed (exit 137) inside the `ttt-lab`
  container (`--memory 8g --memory-swap 8g`). The unpickle stages the full state dict as host
  RAM tensors before any device transfer; building a second, CPU-resident `NemotronH` (353M
  params) alongside that staged copy pushes past the cap even before `.to(device)` runs.
- **What works (verified in `ttt-lab`, used in `prototypes/nyaya_ttt_rsi/model_io.py::load_model`):**
  `torch.load(ckpt_path, map_location="cuda:0", weights_only=False, mmap=True)` — loading
  straight to the CUDA device skips the CPU staging buffer for tensor storages, and `mmap=True`
  (torch ≥ 2.1; confirmed present in `prabhasa/nemo-5090:26.02`'s torch 2.10) maps the file's
  storages instead of reading them fully into memory up front. Verified end-to-end: loads,
  generates the full 690-item `law_qa_heldout_v3.jsonl` set, and reproduces Track B's own
  published M7 "after" numbers (citation recall/precision 1/227, abstention 5/9,
  `law_lookup.prefix_similarity_mean` 0.0587 vs the recorded 0.0584) inside the 8 GiB cap
  (peak VRAM 5.46 GiB, host RAM never spiked).
- **Owner action (Track B):** `scripts/m7/dry_run_sft.py::load_source_model` always loads to
  `"cpu"` regardless of the caller's target device — anyone re-running it (or copying its
  pattern) inside a RAM-capped container should switch that call to
  `map_location=<target_device>, mmap=True` when the target is CUDA, or otherwise ensure the
  host has enough free RAM to stage the full checkpoint (~4.2 GB) plus a CPU-resident model
  copy (~1.4 GB fp32) simultaneously.

## F6. `load_megatron_blob`'s default config resolution can silently pick the wrong tree under a two-mount container layout

- **Symptom (found while writing the G0 allocator-fragmentation run plan, not yet hit in a
  real run):** `scripts/m4/eval_adapter.py::load_megatron_blob`'s `config_path=None` default
  calls `_find_repo_config(blob_path)`, which walks up from the **checkpoint's own path**
  looking for `configs/train/nemotron_h_1b.yaml` — it never looks relative to `--repo-root`.
  `scripts/g0/sft_megatron_batched.py` passes `args.config` straight through, so if a caller
  omits `--config`, resolution depends entirely on where `--checkpoint` happens to sit.
- **Why this matters for a container run specifically:** the G0 OOM test plan
  (`G0-OOM-RUN-PLAN.md`, this directory) mounts the checkpoint's real location
  (`/home/ss/fusion-project`, a full separate mirror of the repo, confirmed by `find`/`ls` to
  contain its own `configs/train/nemotron_h_1b.yaml`) read-only at `/fusion-project`, and the
  actual Track B git checkout (branch `h-ord/phase1`) read-only at `/trackB`. An unset
  `--config` would resolve against `/fusion-project`'s mirrored config, not `/trackB`'s
  checked-out one — silently, no error, no log line naming which tree was used. Verified by
  `diff` that the two `nemotron_h_1b.yaml` files are byte-identical right now, so this has not
  caused a wrong-config load yet, but nothing enforces that they stay in sync (the mirror is
  a separate, unversioned copy), and a future edit to `/trackB`'s config on `h-ord/phase1`
  would silently not apply to any run that omits `--config`.
- **What works:** always pass `--config` explicitly (e.g.
  `--config /trackB/configs/train/nemotron_h_1b.yaml`) in any container invocation that
  mounts the checkpoint's real (fusion-project) location separately from the repo checkout.
- **Owner action (Track B):** either make `sft_megatron_batched.py` require `--config`
  (drop the `default=None` convenience) when `--repo-root` and `--checkpoint` resolve to
  different filesystem trees, or have `load_megatron_blob` prefer a `repo_root`-relative
  config path when one is available instead of always deriving it from the checkpoint path.
