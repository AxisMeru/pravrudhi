#!/usr/bin/env bash
# External proof for the LAW harness track: run a harness recipe against CaseHOLD's held-out `test` split with
# lm-evaluation-harness, which is third-party and is not the scorer the ledger uses.
# usage: scripts/ext_casehold.sh <hf-repo-id> <harness.json> <out-dir> [limit]
#
# Why this exists. `ext_eval.sh` takes a model and an optional adapter and no recipe, so it certifies the MODEL
# and cannot see the harness around it. `ext_humaneval.sh` does apply a harness and is code-only. So the law
# harness track's screen-tier result -- A/A mean 0.3322 at the 512-token baseline against 0.5000 at the adopted
# c-0238 baseline -- had no external path to proof, while its pre-registration claimed `tool: lm-eval` as
# though it did. Under the operator's delegation the internal pool is the SELECTION instrument and external
# tooling is the PROOF, so that gap meant the track could select a harness and never certify one.
#
# What is external here, precisely:
#   * the SPLIT -- CaseHOLD `test`, never in the loop (the pool is `val`, the model track trains on `train`);
#   * the RUNNER -- lm-eval generates and aggregates, not the kernel;
#   * the SCORER -- scripts/ext_tasks/casehold_utils.py, an independent implementation of the same
#     specification, importing neither the harness policy nor the kernel's authoritative scorer.
#
# What it cannot carry, and why the number is therefore a LOWER BOUND: `retries`. The harness re-asks when a
# reply commits to no letter; lm-eval has no equivalent, so an unparsed reply is scored wrong here. Conservative
# is the safe direction for a claim. `n_samples > 1` is REFUSED rather than approximated.
set -euo pipefail
MODEL="$1"; HARNESS="$2"; LIMIT="${4:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Absolute, because every -v below is a docker bind mount and docker reads a relative path as a NAMED
# VOLUME, not a directory: both arms of the first real run died with "includes invalid characters for a
# local volume name".
mkdir -p "$3"; OUT="$(cd "$3" && pwd)"
HFH="${HF_HOME:-$HOME/.cache/huggingface}"

if [[ ! -f "$HARNESS" ]]; then
  echo "REFUSED: no harness recipe at $HARNESS" >&2
  echo "  A promoted recipe is written per bench (ADR-0037) as harness/agent/<bench>/harness.json and appears" >&2
  echo "  only after that bench promotes. It lacks strategy/execution_family, which this script does not need." >&2
  exit 2
fi
SNAP="$(ls -d "$HFH/hub/models--${MODEL//\//--}/snapshots/"*/ | head -1)"
REL="/models/${SNAP#$HFH/}"
CACHE="$ROOT/.pravrudhi/ext_cache"
CSV="$CACHE/casehold-test.csv"
mkdir -p "$OUT" "$CACHE"

# The held-out split is fetched ONCE and kept, so a proof does not depend on what the network returned today.
if [[ ! -s "$CSV" ]]; then
  echo "fetching CaseHOLD test split (once) ..."
  curl -fsSL "https://huggingface.co/datasets/casehold/casehold/resolve/main/data/all/test.csv" -o "$CSV.part"
  mv "$CSV.part" "$CSV"
fi
echo "casehold-test.csv sha256 $(sha256sum "$CSV" | cut -d' ' -f1)"

TASKS="$OUT/tasks"; mkdir -p "$TASKS"
cp "$ROOT/scripts/ext_tasks/casehold_utils.py" "$TASKS/"
LIM_ARG=(); [[ -n "$LIMIT" ]] && LIM_ARG=(--limit "$LIMIT")

# Build inside the image: the prompt must go through the model's own chat template, exactly as
# docker/jobs/agent_choice.py builds it. Rendering it any other way would certify a prompt that never ran.
docker run --rm --network none --user "$(id -u):$(id -g)" \
  -v "$HFH:/models:ro" -v "$CACHE:/cache:rw" -v "$TASKS:/tasks:rw" -v "$ROOT/scripts:/scripts:ro" \
  -v "$(cd "$(dirname "$HARNESS")" && pwd):/recipe:ro" \
  -e HF_HOME=/cache -e HF_HUB_OFFLINE=1 pravrudhi/ext-scorers:latest \
  python /scripts/build_casehold_ext.py --harness "/recipe/$(basename "$HARNESS")" \
    --csv /cache/casehold-test.csv --model-dir "$REL" --cache /cache --tasks /tasks ${LIMIT:+--limit "$LIMIT"}

# lm-eval reuses cached results; remove any prior run so the numbers describe what was just generated.
rm -f "$OUT"/results_*.json "$OUT/results.json"
docker run --rm --gpus all --user "$(id -u):$(id -g)" \
  -v "$HFH:/models:ro" -v "$CACHE:/cache:rw" -v "$TASKS:/tasks:ro" -v "$OUT:/out:rw" \
  -e HF_HOME=/cache -e HF_DATASETS_CACHE=/cache/datasets -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  pravrudhi/ext-scorers:latest lm_eval --model hf \
    --model_args "pretrained=$REL,dtype=bfloat16,trust_remote_code=False" \
    --tasks casehold_harness --batch_size "${BATCH:-4}" --output_path /out --log_samples \
    --include_path /tasks "${LIM_ARG[@]}" 2>&1 | grep -vE "Warning|warn" | tail -25

f="$(find "$OUT" -name 'results_*.json' | sort | tail -1)"
[[ -n "$f" ]] && cp "$f" "$OUT/results.json" && python3 - "$OUT/results.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))["results"]
for t, m in r.items():
    vals = {k: round(v, 4) for k, v in m.items() if isinstance(v, float) and "stderr" not in k}
    print("EXT", t, vals)
print("read `unparsed` before `exact_match`: a reply that never states a letter scores 0 exactly as a wrong "
      "one does, and `retries` is not carried here, so this is a LOWER BOUND on the harness.")
PY
