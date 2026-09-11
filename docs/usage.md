# Using Pravrudhi

## What a night does

A night is a budgeted batch of experiments on your own model or harness. In the deliberation window a local proposer model (served by llama.cpp) reads the ledger's evidence and emits candidate recipes as JSON inside a fixed grammar; a predictor emits a predicted effect and a confidence for each, which are hash-committed before anything runs. The controller rebuilds its posterior from the ledger, scores each live candidate by expected free energy (expected information gain, expected log-preference, cost), refuses to proceed if the scores do not condition on the action, and fills the budget by a Thompson-like knapsack with an epistemic floor. In the execution windows each selected candidate is trained in a disposable container, evaluated against the incumbent on the same held-out rotation with the same sampling seed, scored by the kernel, and disposed by an always-valid sequential test; a candidate that crosses the efficacy boundary must also pass the pre-registered canaries before it becomes the incumbent. The night closes with audit rows for the strategy-switch rate and any rethink checkpoints.

## What you can and cannot change

You may edit anything under `harness/` (prompts, templates), `research/prereg/*.yaml` (budgets, grammar bounds, thresholds; each change is a pre-registration change and should carry an ADR in your own project), and `.pravrudhi/config.yaml`. You may not edit `research/ledger.jsonl`, `research/state.json`, or anything under `.pravrudhi/kernel/`; the ledger writer refuses a file whose hash chain does not verify, and `pravrudhi replay --verify` names the first broken line.

## Reading the evidence

`pravrudhi status` summarises the ledger. `pravrudhi inbox` lists promotion packs with badges derived by replay: grey has no observation, amber is under test, green is promoted and not since pruned, red is pruned or audited. `pravrudhi evidence night1` renders a per-candidate account of a LoRA night and `pravrudhi evidence hnight1` of a harness night, both from the ledger alone; `make reproduce` fails if a rendered document differs from the committed one. `pravrudhi serve` exposes the same views over HTTP. Numbers you quote elsewhere should come from these outputs; `make headline-check` flags any measurement-looking number in the README, paper or evidence documents that does not trace to a gate or pre-registration file.

## The external tier

The loop's own instrument selects candidates; it does not by itself prove an improvement on a public benchmark. Two wrappers run third-party scorers with the same invocation before and after a loop: `scripts/ext_eval.sh` runs lm-evaluation-harness inside the scorer image (base model, or base plus an exported adapter), and `scripts/ext_humaneval.sh` runs your agent harness on HumanEval+ and scores it with EvalPlus. Their result files enter the ledger by hash with `pravrudhi ext-record` (tier `external`, never pratyakṣa in the kernel sense), and `pravrudhi evidence external` renders the table and the paired differences from those rows alone. Quote external numbers from that document, with the standard errors the scorers report.

## More machines, and coding agents

Neither is required: one machine and no coding agents is the default, and a fresh install has no fleet file at all.

`pravrudhi hosts list` probes every enrolled machine and reports what it can actually do, measured on the machine
rather than declared in a config. `pravrudhi hosts add <name> --address <host> --user <you>` enrols another over
SSH and refuses to record one that does not answer the probe. `pravrudhi hosts place train` says which machine
would take a job and why each other machine would not. A machine with a CUDA GPU and a container runtime can
train; an Apple Silicon machine can serve open-weight models through Metal but cannot train, because the kernel
admits only container-isolated runs.

The first thing a second machine buys you is `--proposer-endpoint`. The proposer is the largest model in the loop
and on a single GPU it competes for the memory the trainee needs; pointed at another host serving an
OpenAI-compatible endpoint, the training accelerator never has to share.

`pravrudhi agents` lists the coding agents that can run here and the specific reason for any that cannot. Agents
work only inside their own git worktree, a task declares the paths it may touch, and a diff that strays outside
that declaration or touches the kernel, the ledger, sealed state or the pre-registration files is rejected whole.
Hosted assistants improve code only; weight-level distillation teachers stay open-weight models.

## Checking an installation

`pravrudhi doctor` reports whether this installation is ready to run: initialised, ledger chain verifying, docker
present, at least one sealed pool, pre-registration files in place. It exits non-zero if any check failed, so it
can gate a script.

## Messages and plugins

The engine can talk to you on Telegram and can draw on tools, connectors and plugins installed on the machine.
Both are ordinary commands, not internal modules.

* `pravrudhi telegram-poll` reads the messages a paired Telegram bot has received and answers them from the
  engine's own state (`application/telegram_inbox.py`); the network cannot ask the engine to do anything it does
  not already do. The first message you send the bot pairs it and records the chat, after which the engine can
  start a conversation rather than only answer one. Studio's bot is the operator's; a product install
  configures its own bot in settings, where the token is stored like a provider key (`application/messaging.py`),
  and the chat id and the on/off switch are ordinary settings.
* `pravrudhi doctor` includes a `telegram` check: paired, or no token configured here (so nothing to pair), or
  a token without a paired chat with the step that pairs it.
* `pravrudhi tools [--category ...]` lists the tools, connectors and plugins this engine can draw on and which
  of them are installed here (`application/tools.py`).

## Sign-off and export

Promotion to incumbent inside a night is automatic and reversible by replay; merging an adapter into base weights, or shipping it, is a human act. Sign a pack through the API with the `X-Pravrudhi-Operator` header (agent identities are refused) or a gate with `pravrudhi gate sign`, then `pravrudhi export <dir>` copies the green adapter with a provenance manifest naming the candidate, the ledger head and the adapter hash.

## Targets

The first target is adapter-only self-improvement of a local model on GSM8K with a deterministic checker. The `Target` protocol in `src/pravrudhi/targets/base.py` is the extension point: a target declares its surfaces and edit families, materialises a candidate into a work directory, and returns a container job whose per-item outputs the kernel scores. Script and agent-harness targets follow the same protocol.
