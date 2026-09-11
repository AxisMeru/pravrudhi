#!/usr/bin/env bash
# External proof for the IL-TUR harness track: run a harness recipe against IL-TUR's held-out `test` split.
# Modelled on ext_casehold.sh, using ext_iltur_generate.py (with retry loop, not lm-eval).
# usage: scripts/ext_iltur.sh <hf-repo-id> <recipe.json> <out-dir> [limit]
#
# What is external here, precisely:
#   * the SPLIT -- IL-TUR `test`, never in the loop (the pool is `val`, model training is unseen);
#   * the RUNNER -- ext_iltur_generate.py applies template and retries like the harness does;
#   * the SCORER -- independent parser of section names, not importing the kernel.

set -euo pipefail
MODEL="$1"; HARNESS="$2"; LIMIT="${4:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$3"; OUT="$(cd "$3" && pwd)"
HFH="${HF_HOME:-$HOME/.cache/huggingface}"

if [[ ! -f "$HARNESS" ]]; then
  echo "REFUSED: no harness recipe at $HARNESS" >&2
  echo "  A promoted recipe is written per bench as harness/agent/<bench>/harness.json" >&2
  echo "  and appears only after that bench promotes." >&2
  exit 2
fi

SNAP="$(ls -d "$HFH/hub/models--${MODEL//\//--}/snapshots/"*/ | head -1)"
REL="/models/${SNAP#$HFH/}"
CACHE="$ROOT/.pravrudhi/ext_cache"
JSONL="$CACHE/iltur-lsi-test.jsonl"
mkdir -p "$OUT" "$CACHE"

# Fetch IL-TUR test split once
if [[ ! -s "$JSONL" ]]; then
  echo "Fetching IL-TUR test split (once)..."
  python3 "$ROOT/scripts/ext_iltur_fetch.py" \
    --output-dir "$CACHE" ${LIMIT:+--limit "$LIMIT"}
  echo ""
fi

if [[ ! -f "$JSONL" ]]; then
  echo "ERROR: Failed to fetch IL-TUR test split" >&2
  exit 1
fi

echo "iltur-lsi-test.jsonl sha256 $(sha256sum "$JSONL" | cut -d' ' -f1)"
echo ""

# Run generation with retry loop inside docker
rm -f "$OUT/results.json"
docker run --rm --gpus all --user "$(id -u):$(id -g)" \
  -v "$HFH:/models:ro" -v "$CACHE:/cache:ro" -v "$OUT:/out:rw" -v "$ROOT/scripts:/scripts:ro" \
  -v "$(cd "$(dirname "$HARNESS")" && pwd):/recipe:ro" \
  -e HF_HOME=/cache -e HF_HUB_OFFLINE=1 -e TRANSFORMERS_OFFLINE=1 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  pravrudhi/ext-scorers:latest python /scripts/ext_iltur_generate.py \
    --recipe "/recipe/$(basename "$HARNESS")" --jsonl /cache/iltur-lsi-test.jsonl \
    --model-dir "$REL" --output /out/results.json --batch-size "${BATCH:-4}" \
    ${LIMIT:+--limit "$LIMIT"} 2>&1 | grep -vE "Warning|warn" | tail -15

python3 - "$OUT/results.json" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
print("EXT-ILTUR", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in r["meta"].items()})
print("Retries applied as the recipe states; scored by the independent external parser; the same harness the loop selected.")
PY
