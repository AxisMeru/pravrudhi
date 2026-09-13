# Spike: TTT + RSI-gate mechanism demo (2026-09-13)

Throwaway, toy-scale. Full writeup and interpretation: see
[`../../ttt-rsi-track-b-research-and-implementation.md`](../../ttt-rsi-track-b-research-and-implementation.md)
(§6 "Spike").

- `run_ttt_rsi_prototype.py` — the script (Qwen2.5-1.5B-Instruct, hand-rolled rank-4 LoRA, n=10
  synthetic legal-graph examples, no `peft`/no Track B code/checkpoints touched).
- `report.json` — the exact run this document's numbers come from (wall clock 15.4s, RTX 5090,
  `rtx5090-train` container, GPU idle before/after — checked via `nvidia-smi`/`docker ps`).

Reproduce: `docker exec rtx5090-train python3 /workspace/<copy-this-script-here>/run_ttt_rsi_prototype.py`
(or any CUDA box with `torch`+`transformers`; ~15s on a 5090 with a 1.5B model).
