#!/usr/bin/env bash
# External proof for the harness track: run the agent harness on HumanEval+ (never in the loop) with a fixed model,
# then score with EvalPlus's own evaluator. usage: scripts/ext_humaneval.sh <hf-repo-id> <harness.json> <out-dir> [seed]
set -euo pipefail
MODEL="$1"; HARNESS="$2"; OUT="$3"; SEED="${4:-0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# ADR-0037 moved the promoted recipe to one file per bench, so `harness/agent/<bench>/harness.json` does not
# exist until that bench has promoted something. Without this the run died on `cp: cannot stat`, which says
# nothing about why. It refuses rather than falling back to the old global `harness/agent/harness.json`: that
# file still holds the night-3 code incumbent and nothing writes it any more, so a fallback would quietly
# score the external proof against a stale recipe and report the number as current.
if [[ ! -f "$HARNESS" ]]; then
  echo "REFUSED: no harness recipe at $HARNESS" >&2
  echo "  A promoted recipe is written per bench (ADR-0037) and appears only after that bench promotes." >&2
  echo "  Run a harness night on the code bench first, or pass a recipe explicitly." >&2
  if [[ -f "$ROOT/harness/agent/harness.json" ]]; then
    echo "  Do NOT pass harness/agent/harness.json: it is the pre-ADR-0037 global file, holds the night-3" >&2
    echo "  code incumbent, and nothing has written it since. Proof run against it would not be current." >&2
  fi
  exit 2
fi
HFH="${HF_HOME:-$HOME/.cache/huggingface}"
SNAP="$(ls -d "$HFH/hub/models--${MODEL//\//--}/snapshots/"*/ | head -1)"; REL="/models/${SNAP#$HFH/}"
mkdir -p "$OUT/in" "$OUT/out"
# EvalPlus reuses *_eval_results.json when present: a stale file silently reports a previous harness's score for
# freshly generated samples. Remove any prior result and sample files so every run measures what it just produced.
rm -f "$OUT/out/"*_eval_results.json "$OUT/out/samples.jsonl" "$OUT/out/evalplus_samples.jsonl" "$OUT/out/job_meta.json"
python3 - "$ROOT/.pravrudhi/ext_cache/humanevalplus.jsonl" "$OUT/in/items.jsonl" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
open(sys.argv[2], "w").write("".join(json.dumps({"id": r["task_id"], "question": r["prompt"]}) + "\n" for r in rows))
print("items", len(rows))
PY
cp "$HARNESS" "$OUT/in/harness.json"
docker run --rm --gpus all --network none --user "$(id -u):$(id -g)" -v "$OUT/in:/in:ro" -v "$HFH:/models:ro" -v "$OUT/out:/out:rw" \
  -e HF_HOME=/models -e HF_HUB_OFFLINE=1 pravrudhi/exec-5090:latest agent_code --model-dir "$REL" --seed "$SEED" --batch-size 16 2>&1 | grep -vE "Warning|warn" | tail -2
python3 - "$OUT/out/samples.jsonl" "$OUT/out/evalplus_samples.jsonl" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
open(sys.argv[2], "w").write("".join(json.dumps({"task_id": r["id"], "solution": r["solution"]}) + "\n" for r in rows))
PY
docker run --rm --network none --user "$(id -u):$(id -g)" -v "$OUT/out:/work:rw" -v "$ROOT/.pravrudhi/ext_cache:/cache:rw" -e HOME=/cache -e HF_HOME=/cache \
  pravrudhi/ext-scorers:latest bash -c "cd /work && evalplus.evaluate --dataset humaneval --samples evalplus_samples.jsonl --parallel 8 2>&1 | grep -vE 'Warning|warn' | tail -6"
RES="$(ls "$OUT/out"/*eval_results.json 2>/dev/null | head -1)"
# the results must be newer than the samples they claim to score
if [[ -n "$RES" ]] && [[ "$OUT/out/evalplus_samples.jsonl" -nt "$RES" ]]; then
  echo "REFUSED: $RES is older than the samples it reports on" >&2; exit 3
fi
echo "$RES"
