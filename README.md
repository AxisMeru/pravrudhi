# Pravrudhi — प्रवृद्धि

Pravrudhi is a recursive self-improvement engine for language-model systems that you run on your own hardware, over your own model and your own harness. You install one package, initialise it inside a project, point the configuration at a local model and a target, and run a budgeted night. The night proposes changes, selects among them with an expected-free-energy controller over a posterior that lives outside the language model, executes each selected candidate in a disposable sandbox, scores it with an evaluator kernel the mutated process cannot reach, and disposes of it by a sequential test. In the morning the inbox lists what the night wants promoted; you accept or reject; `pravrudhi export` hands back the improved artefact.

Nothing on this page states a measurement. Measurements live in the paper under `paper/` and in evidence documents rendered from the ledger, and only once a gate has produced them.

## Quickstart (one GPU, Linux, Docker with the NVIDIA runtime)

```bash
git clone https://github.com/AxisMeru/pravrudhi.git && cd pravrudhi
uv sync
uv run pravrudhi init --root .                       # kernel state dir, config, pre-registrations, prompts, genesis ledger
make exec-image                                       # execution image (public NVIDIA PyTorch base; override with BASE_IMAGE=)
uvx --from huggingface_hub hf download Qwen/Qwen3-4B  # trainee
uvx --from huggingface_hub hf download Qwen/Qwen3-30B-A3B-GGUF --include "Qwen3-30B-A3B-Q4_K_M.gguf"   # proposer
docker pull ghcr.io/ggml-org/llama.cpp:server-cuda
uv run pravrudhi pool seal-gsm8k path/to/gsm8k-test.parquet --root .   # seal the held-out pool (kernel-owned)
uv run pravrudhi preflight --root .                   # measure VRAM and throughput on your card
uv run pravrudhi study noise-floor --root .           # the noise floor of your unmodified model (required before a night)
uv run pravrudhi night --night 1 --root .             # one budgeted night
uv run pravrudhi inbox --root .                       # what the night wants promoted
uv run pravrudhi export ./adapter --root .            # the green adapter, with provenance

# optional: more machines and coding agents. Neither is required; one machine and no agents is the default.
uv run pravrudhi hosts list --root .                  # what this machine can do, measured not declared
uv run pravrudhi hosts add mac-mini --address 10.0.0.5 --user you   # enrol another machine over ssh
uv run pravrudhi hosts place train --root .           # which machine takes a training job, and why not the others
uv run pravrudhi agents --root .                      # which coding agents can run here
uv run pravrudhi serve --root .                       # HTTP under /api: candidates, observations, inbox, evidence, objectives, chat...

# optional: state what you want, track it, and let the engine plan for it
uv run pravrudhi objective new my-goal --from math-reasoning    # or --intent/--track/--metric written by hand
uv run pravrudhi objective progress my-goal --root .            # baseline vs current, straight from the ledger
uv run pravrudhi objective loom my-goal --root .                # the plan as Loom source: readable, editable, not yet run
uv run pravrudhi objective subagents my-goal --run --root .     # fan the plan out to coding agents; output lands in proposals/, never as evidence
uv run pravrudhi tools --root .                                 # tools and connectors this engine can draw on, and what's installed here
uv run pravrudhi recipes --root .                               # published training/eval recipes, and what's installed here
uv run pravrudhi routing --root .                               # which agent and model a difficulty tier would use now, and why

# harness track (fixed model, mutable scaffold, MBPP+ hidden tests in the sandbox; HumanEval+ as external proof)
uv run pravrudhi pool seal-mbppplus --root .
uv run pravrudhi study harness-noise-floor --root .
uv run pravrudhi harness-night --night 1 --root .
scripts/ext_humaneval.sh Qwen/Qwen3-1.7B harness/agent/harness.json ./research/ext/humaneval-after

# external proof tier (third-party scorers; results enter the ledger by hash and render to docs/evidence/P1_external.md)
scripts/ext_eval.sh Qwen/Qwen3-0.6B gsm8k ./research/ext/base            # lm-evaluation-harness, offline
scripts/ext_eval.sh Qwen/Qwen3-0.6B gsm8k ./research/ext/after ./adapter
uv run pravrudhi ext-record ./research/ext/after/results.json --tool lm-eval --track M --condition adapter:c-0045 --model Qwen/Qwen3-0.6B --root .
uv run pravrudhi evidence external --root .
```

`pravrudhi replay --verify` rebuilds the state view from the ledger and verifies the hash chain; `make reproduce` regenerates every evidence document from the ledger and fails on any difference.

`pravrudhi app` (see `app/README.md`) serves the same engine with a browser interface, plus a few surfaces the CLI does not have a command for. `/api/chat` is a conversational front door over the same replay functions as the routes above: a reply may only state a number a tool call actually returned, citing the ledger rows behind it, and whatever it cannot verify is stripped out and reported as a refusal rather than invented. Its model endpoint is `PRAVRUDHI_CHAT_ENDPOINT` (falling back to the proposer's own local llama.cpp). `/api/memory` holds durable notes kept apart from ledger evidence; they follow the caller, not the workspace. Identity is optional: set `PRAVRUDHI_AUTH=required` with a Supabase project configured (see `supabase/`) to get per-user accounts at `/api/me` and separate per-user workspaces at `/api/workspaces`; leave it unset and every route answers to the local operator, no account required.

## What runs where

`pravrudhi_kernel/` is the evaluator kernel: schema, hash-chained ledger, vendored statistics, controller mathematics, sealed pools, scorers, and the sandbox runner. It has no model client and no network access; it is the only writer of evidence. `src/pravrudhi/` is the engine: targets, model backends, the proposer, the night orchestrator, CLI, API, and the Claude Code plugin under `plugin/`. The kernel is a dependency of the engine, never the reverse.

## Claude Code plugin

`plugin/` carries skills `pravrudhi-night`, `pravrudhi-inbox`, `pravrudhi-status`, `pravrudhi-export`. Install it as a local plugin to drive a project from an agent session; sign-off stays a human act (the API refuses agent identities).

## Licence

Apache-2.0.
