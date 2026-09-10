#!/usr/bin/env bash
# External proof-tier scoring with lm-evaluation-harness inside pravrudhi/ext-scorers, offline, datasets pre-cached.
# usage: scripts/ext_eval.sh <hf-repo-id> <tasks,comma> <out-dir> [adapter-dir] [limit] [task-dir]
# task-dir mounts custom lm-eval task YAMLs (--include_path). Defaults to scripts/ext_tasks when present,
# so the nyaya citation tasks are available without an image rebuild.
# Produces <out-dir>/results.json (lm-eval's own output) and prints the accuracy lines. Never touches the ledger.
set -euo pipefail
MODEL="$1"; TASKS="$2"; OUT="$3"; ADAPTER="${4:-}"; LIMIT="${5:-}"; TASKDIR="${6:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HFH="${HF_HOME:-$HOME/.cache/huggingface}"
SNAP="$(ls -d "$HFH/hub/models--${MODEL//\//--}/snapshots/"*/ | head -1)"
REL="/models/${SNAP#$HFH/}"
mkdir -p "$OUT"
ARGS="pretrained=$REL,dtype=bfloat16,trust_remote_code=False"
MOUNTS=(-v "$HFH:/models:ro" -v "$ROOT/.pravrudhi/ext_cache:/cache:rw" -v "$OUT:/out:rw")
if [[ -n "$ADAPTER" ]]; then MOUNTS+=(-v "$ADAPTER:/adapter:ro"); ARGS="$ARGS,peft=/adapter"; fi
LIM=(); [[ -n "$LIMIT" ]] && LIM=(--limit "$LIMIT")
[[ -z "$TASKDIR" && -d "$ROOT/scripts/ext_tasks" ]] && TASKDIR="$ROOT/scripts/ext_tasks"
INC=(); if [[ -n "$TASKDIR" ]]; then MOUNTS+=(-v "$TASKDIR:/tasks:ro"); INC=(--include_path /tasks); fi
# network is allowed here only so lm-eval can fetch its task datasets into the persistent cache (the model is local);
# the kernel's own scoring jobs never have network.
docker run --rm --gpus all --user "$(id -u):$(id -g)" "${MOUNTS[@]}" \
  -e HF_HOME=/cache -e HF_DATASETS_CACHE=/cache/datasets -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=1 \
  pravrudhi/ext-scorers:latest lm_eval --model hf --model_args "$ARGS" --tasks "$TASKS" --batch_size 32 \
  --output_path /out --log_samples "${INC[@]}" "${LIM[@]}" 2>&1 | grep -vE "Warning|warn" | tail -25
f="$(find "$OUT" -name 'results_*.json' | sort | tail -1)"; [[ -n "$f" ]] && cp "$f" "$OUT/results.json" && python3 - "$OUT/results.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))["results"]
for t, m in r.items():
    vals = {k: round(v, 4) for k, v in m.items() if isinstance(v, float) and "stderr" not in k}
    errs = {k: round(v, 4) for k, v in m.items() if isinstance(v, float) and "stderr" in k}
    print("EXT", t, vals, errs)
PY
