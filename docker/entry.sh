#!/usr/bin/env bash
# usage: <job> [args...]; default job is generate for backward compatibility
set -euo pipefail
job="${1:-generate}"
case "$job" in
  generate|sample|train_sft|train_grpo|anchor_nll|agent_code|agent_choice|score_code) shift; exec python "/opt/pravrudhi/jobs/${job}.py" "$@" ;;
  # An unknown job name used to fall through to generate.py, which ran the WRONG job and produced
  # plausible output for something nobody asked for. A typo is now a failure, not a silent substitution.
  *) echo "unknown job: ${job}" >&2; exit 64 ;;
esac
