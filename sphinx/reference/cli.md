# Command line

Generated from the Typer command tree by `sphinx/gen_reference.py`; do not edit by hand. Run any command with
`--help` for the same text. Every command takes `--root` where it works on a project folder.

## `pravrudhi agents`

Which coding agents can run right now, and the reason for any that cannot.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --json | option | False | machine-readable output |

## `pravrudhi app`

The Pravrudhi app: the engine's API and the web interface on one local port.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --port | option | 8008 |  |
| --host | option | 127.0.0.1 | bind address; keep it local unless you know why |
| --no-browser | option | False |  |

## `pravrudhi appetite`

What the engine wants right now, and why, from measured drives rather than introspection.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --json | option | False |  |

## `pravrudhi build`

Show or dispatch a self-build plan against the engine's own tree, one task per line.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| plan | argument | required | Self-build plan YAML; see application.selfbuild.PACKAGED_EXAMPLE. |
| --run | option | False | Dispatch the plan; otherwise preview only. |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi contract`

House alias for `gate check`.

Subcommands: `check`.

## `pravrudhi contract check`

Check a gate JSON (the house alias of `gate check`).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| path | argument | required |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi convergence`

Is this loop converging, or merely ticking?

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --hours | option | 24 | trailing window to report over |
| --json | option | False | machine-readable figures |

## `pravrudhi cycle`

The unattended cycle's one entry point: propose a card for the packaged self-build example's first task, dispatch it through `selfbuild.run_unattended_cycle` under the given wall-clock budget, and close its gate if the run passed. Exits non-zero when the gate did not close. `--dry-run` proposes the card and previews the dispatch without running it.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --budget | option | required | wall-clock seconds allotted to the cycle's one self-build task |
| --dry-run | option | False | propose the card and preview the task; do not dispatch it |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi demo-export`

Record this engine's results and capabilities as the snapshot the public site and dashboard render.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --dest | option | app/frontend/public/demo.json | where the snapshot is written |
| --private-names | option | required | Private-name list (one per line, outside any repo). Required. |
| --private-names-sha256 | option | required | The list's pinned sha256; any other file is refused. |

## `pravrudhi doctor`

Is this installation ready to run? Reports every check and exits non-zero if any failed.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --json | option | False | machine-readable output |

## `pravrudhi evidence`

Render docs/evidence/<name>.md from the ledger alone (make reproduce).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| name | argument | noise_floor |  |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --check | option | False | Compare with the committed document; exit 1 on any difference. |

## `pravrudhi export`

Copy the promoted (green) adapter and its provenance manifest to DEST. Never merges into base weights.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| dest | argument | required |  |
| --candidate | option |  |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi ext-record`

Admit an external scorer's result file into the ledger by hash (tier: external).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| path | argument | required | lm-eval results.json or EvalPlus *_eval_results.json |
| --tool | option | required | lm-eval \| evalplus \| prabhasa |
| --track | option | required | M (model) \| H (harness) |
| --condition | option | required | base \| adapter:c-0045 \| harness:c-0012 |
| --model | option | required |  |
| --night | option | 0 |  |
| --dataset | option |  | EvalPlus dataset: humaneval \| mbpp |
| --seed | option |  |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi gate`

Emit, check and sign gate JSON. Gates are never hand-edited.

Subcommands: `check`, `emit`, `sign`.

## `pravrudhi gate check`

Check a gate JSON against its contract.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| path | argument | required |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi gate emit`

Emit a gate JSON for a card from an evidence file.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| card_id | argument | required |  |
| --evidence | option | required |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi gate sign`

Sign a gate: with --by as a person's name, or with --delegated as the delegation's identity under its conditions.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| path | argument | required |  |
| --by | option |  | A person's name. Omit with --delegated. |
| --note | option |  |  |
| --delegated | option | False | Close as the delegation's identity under its conditions. |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi harness-night`

Track H night: fixed model, mutable harness, paired on the configured pool's rotations.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --night | option | 1 |  |
| --budget | option |  | GPU-hours; default from research/prereg/lora_night.yaml |
| --k | option |  |  |
| --policy | option |  | selection arm for H1: efe (default, from prereg) \| greedy \| thompson \| random |
| --proposer-endpoint | option |  | OpenAI-compatible endpoint to borrow for the proposer (e.g. a fleet host serving llama.cpp), leaving this GPU free |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --gguf | option |  |  |
| --seed-recipe | option | [] | harness recipe JSON to admit as a candidate through the kernel's own door, alongside the proposer's; repeatable |
| --config | option |  | Frozen night pre-registration to run; default research/prereg/harness_night.yaml. |

## `pravrudhi heartbeat`

One heartbeat: find the most neglected undone objective step and dispatch it; with --loop, keep going.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --loop | option | False | keep beating at the configured interval |
| --json | option | False |  |

## `pravrudhi hosts`

Machines this engine can place work on. A single machine needs no configuration.

Subcommands: `add`, `list`, `place`.

## `pravrudhi hosts add`

Enrol a machine, probe it immediately, and refuse to record one that does not answer.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| name | argument | required | short name for the machine, e.g. mac-mini |
| --address | option |  | hostname or IP for the ssh transport |
| --user | option |  | ssh user |
| --transport | option | ssh | local \| ssh \| orca |
| --orca-host-id | option |  | host id when transport is orca |
| --workdir | option | ~/pravrudhi |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi hosts list`

Probe every enrolled machine and report what it can actually do.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --json | option | False | machine-readable output |

## `pravrudhi hosts place`

Say which machine would take a job, and why each other machine would not.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| job | argument | train | train \| serve \| agent \| any |
| --min-vram-gb | option | 0.0 |  |
| --needs-agent | option |  |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi inbox`

List promotion packs awaiting the operator (badge from replay; nothing here is hand-set).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi inbox-sign`

Record a decision on a promotion pack.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --pack | option | required | Promotion pack directory, as `pravrudhi inbox` prints it |
| --decision | option | approve | approve \| reject \| defer |
| --note | option |  |  |
| --by | option |  | A person's name to sign as them; omit to sign autonomously under the recorded delegation |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi inbox-sweep`

Decide every unsigned promotion pack under the recorded delegation: approve a green badge, defer anything else with the standing equivocal-evidence reason.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi init`

Make this project ready for a night: kernel state dir, config, pre-registrations, prompts, genesis ledger.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --model | option |  |  |

## `pravrudhi integrate`

Merge every agent worktree's accepted work back, three-way per file rather than copy per worktree.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --dry-run | option | False | report what would happen and change nothing |
| --json | option | False |  |

## `pravrudhi messaging`

This workspace's own Telegram bot, separate from the engine operator's.

Subcommands: `clear`, `set`, `status`.

## `pravrudhi messaging clear`

Forget this workspace's bot entirely.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi messaging set`

Store or amend this workspace's bot. Each argument left out is left alone, so `--disabled` on its own silences delivery without re-sending the token.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --token | option |  | Bot token from BotFather; stored once, mode 0600, and never read back by any route. |
| --chat-id | option |  | Chat id to deliver to. |
| --enabled | option |  | Turn delivery on or off without discarding the stored credential. |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi messaging status`

Whether this workspace has a Telegram bot, whether it is delivering, and where -- never the token (application/messaging.py).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --json | option | False |  |

## `pravrudhi night`

Run one budgeted night: propose -> deliberate -> execute -> dispose. Every observation is kernel-scored.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --night | option | 1 |  |
| --budget | option |  | GPU-hours; default from research/prereg/lora_night.yaml |
| --k | option |  |  |
| --policy | option |  | selection arm for H1: efe (default, from prereg) \| greedy \| thompson \| random |
| --proposer-endpoint | option |  | OpenAI-compatible endpoint to borrow for the proposer (e.g. a fleet host serving llama.cpp), leaving this GPU free |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --train-parquet | option | .pravrudhi/data/gsm8k-train.parquet |  |
| --gguf | option |  |  |
| --objective | option |  | Run the night for this objective; the prereg that claims it supplies the rest. |
| --config | option |  | Frozen night pre-registration to run; default research/prereg/lora_night.yaml. |

## `pravrudhi nyaya`

Nyaya: a question of Indian law, answered from sources and checked.

Subcommands: `ask`, `validity-check`, `vendors`.

## `pravrudhi nyaya ask`

Ask every named vendor, in parallel; every citation is checked against the corpus; the record is written under research/nyaya/asks with provenance agama. A verdict is a check of the answer against the sources it was shown, not a statement about the law.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| question | argument | required |  |
| --vendor | option | ['claude-cli'] | Repeatable. See `pravrudhi nyaya vendors`. |
| --checker | option |  | A vendor that audits each answer (A1.1 shape). |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi nyaya validity-check`

Run the kernel's own classical-Nyaya validity check and admit it to the ledger (track nyaya, tier kernel).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --condition | option | base | base \| candidate:<id> |
| --night | option | 0 |  |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --constructed | option | False | Score the six-bank constructed hetvabhasa gold set against the external Lean checker instead of the 5-item hand-labelled smoke-check. |
| --per-class | option | 100 | constructed only: items per class. |
| --seed | option | 0 | constructed only: generator seed. |

## `pravrudhi nyaya vendors`

Which vendors this install can ask, and why not when it cannot.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi objective`

What you are trying to achieve, and whether it is happening.

Subcommands: `list`, `loom`, `new`, `progress`, `show`, `subagents`.

## `pravrudhi objective list`

Every objective in this workspace, with how far along it is.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi objective loom`

The objective's plan as Loom source: readable, editable, and not yet run.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| objective_id | argument | required |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi objective new`

State what you want the loop to achieve. An objective with no benchmark is refused.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| objective_id | argument | required | short id, lowercase and hyphens |
| --from | option |  | copy a packaged example objective instead of writing one by hand |
| --intent | option |  | what you want, in your own words |
| --track | option |  | ledger track this objective's evidence accumulates under |
| --metric | option |  | benchmark metric, named as the evidence document names it |
| --tool | option | lm-eval | lm-eval \| evalplus \| prabhasa |
| --domain | option |  |  |
| --target-delta | option |  | how much improvement counts as success; omit for 'better is better' |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi objective progress`

Baseline, current and the difference, per benchmark.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| objective_id | argument | required |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi objective show`

The objective and its standing, as JSON. Every number comes from the ledger.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| objective_id | argument | required |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi objective subagents`

Fan the objective's plan out to subagents, one scoped task per step. Their output is a proposal.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| objective_id | argument | required |  |
| --run | option | False | dispatch; otherwise preview |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi panel`

Ask many vendors the same thing and record every answer identically.

Subcommands: `run`, `vendors`.

## `pravrudhi panel run`

Ask every named vendor every prompt. A vendor that cannot answer is recorded as a gap.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| prompts | argument | required | JSONL with an `id` and a `prompt` per line. |
| --vendor | option | [] | Repeatable. Default: every reachable vendor. |
| --out | option | research/panel |  |
| --panel-config | option |  | Per-vendor parameters; default configs/panel.yaml when it exists. |
| --param | option | [] | Repeatable tuning override, `vendor:key=value` (e.g. codex-cli:temperature=0.7). |

## `pravrudhi panel vendors`

Every declared vendor, whether it can be asked right now, and why not if it cannot.

## `pravrudhi paper-data`

Regenerate paper/generated/*.tex from the ledger alone, so the paper's tables never drift from it.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi parity`

Inspect the checked-in capability matrix and re-run its evidence.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| action | argument |  | gaps: show rival advantages only |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --json | option | False |  |

## `pravrudhi pool`

Seal benchmark pools into the kernel state directory.

Subcommands: `seal-apps`, `seal-casehold`, `seal-gsm8k`, `seal-lsi`, `seal-mbppplus`, `seal-mmlu`.

## `pravrudhi pool seal-apps`

Seal a held-out APPS slice as the harness track's second internal code pool.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --source | option | required | APPS split on disk (test.jsonl or parquet), fetched separately |
| --count | option | 400 |  |
| --seed | option | 0 |  |
| --bench | option | apps |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi pool seal-casehold`

Seal CaseHOLD's validation split as the law tracks' internal choice pool.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --source | option | required | CaseHOLD val CSV on disk, fetched separately |
| --bench | option | casehold-val |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi pool seal-gsm8k`

Seal a benchmark pool from a parquet file into the kernel state directory.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| parquet | argument | required |  |
| --bench | option | gsm8k-test |  |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --offset | option | 0 |  |
| --count | option |  |  |

## `pravrudhi pool seal-lsi`

Seal IL-TUR's LSI validation split as the first internal `set` pool.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --source | option | required | IL-TUR lsi dev parquet on disk, fetched separately |
| --statutes | option | required | IL-TUR lsi statutes parquet (the 100-section label space) |
| --bench | option | iltur-lsi-dev |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi pool seal-mbppplus`

Seal EvalPlus MBPP+ as a kernel pool (hidden tests executed only inside the sandbox).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --cache | option | .pravrudhi/ext_cache |  |

## `pravrudhi pool seal-mmlu`

Seal a law slice of MMLU as the product objective's internal choice pool.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --bench | option | mmlu-law-val |  |
| --cache | option | .pravrudhi/ext_cache |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi preflight`

Measure peak VRAM and tokens/s on this card with one real job; write research/prereg/measured_stack.json.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --model | option | Qwen/Qwen3-4B |  |
| --bench | option | gsm8k-test |  |
| --template | option | harness/prompts/eval/gsm8k_v1.md |  |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --n-items | option | 32 |  |
| --batch-size | option | 16 |  |

## `pravrudhi publish`

Export the snapshot, build the interface, check the pages carry content, commit and push.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --write-root | option |  | Where the snapshot/build/commit/push land; defaults to --root, unchanged. |
| --message, -m | option | Refresh the recorded snapshot so the published pages show what the engine has done | commit message for the snapshot refresh |
| --no-push | option | False | build and commit, but do not push |
| --json | option | False |  |

## `pravrudhi recipes`

Published training and evaluation recipes an objective may draw on, and which are installed here.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --capability | option |  |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi replay`

Rebuild state.json from the ledger alone, and verify the chain with --verify.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --ledger | option |  | default <root>/research/ledger.jsonl |
| --state | option |  | default <root>/research/state.json |
| --verify | option | False | Verify the hash chain and compare against the committed state; exit 1 on any difference. |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests`

The operator's asks: triage them without hand-editing.pravrudhi/requests.json.

Subcommands: `amend`, `decline`, `mark-met`, `set-mode`, `show`.

## `pravrudhi requests amend`

Translate a named fragment of a criterion's text, keeping the original and the reason on the record.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| request_id | argument | required | request id, e.g. r-1a2b3c4d |
| index | argument | required | criterion index, 0-based |
| --old | option | required | the exact fragment to replace; must occur exactly once |
| --new | option | required | what it becomes |
| --all | option | False | translate every occurrence of the fragment, not only a unique one |
| --why | option | required | why, for the note kept on the request |
| --actor | option | agent-for-operator | who is making this edit, for the note kept on the request |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests decline`

Say no, on the record. With an index, only that criterion is declined and the rest of the request stands; without one, the whole request moves to `declined` (the existing state machine).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| request_id | argument | required | request id, e.g. r-1a2b3c4d |
| index | argument |  | criterion index, 0-based; omit to decline the whole request |
| --why | option | required | why, for the note kept on the request |
| --actor | option | agent-for-operator | who is making this edit, for the note kept on the request |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests mark-met`

Mark one acceptance criterion met by hand, with the evidence and the reason both kept on the request.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| request_id | argument | required | request id, e.g. r-1a2b3c4d |
| index | argument | required | criterion index, 0-based |
| --evidence | option | required | a commit sha, ledger sequence, path, command, screenshot, or url |
| --kind | option | commit | commit \| ledger_seq \| file \| command \| screenshot \| url |
| --why | option | required | why, for the note kept on the request |
| --actor | option | agent-for-operator | who is making this edit, for the note kept on the request |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests set-mode`

Flip one criterion between `proposal` and `build` dispatch, with the reason kept on the request.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| request_id | argument | required | request id, e.g. r-1a2b3c4d |
| index | argument | required | criterion index, 0-based |
| mode | argument | required | proposal \| build |
| --why | option | required | why, for the note kept on the request |
| --actor | option | agent-for-operator | who is making this edit, for the note kept on the request |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests show`

The full ask, each criterion with who wrote it and whether it is met, and its evidence and audit notes.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| request_id | argument | required | request id, e.g. r-1a2b3c4d |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests-advance`

Move a request along the state machine, refusing an illegal move, a close without evidence, or — for `verified` — a completion gate that has not actually passed: re-verified evidence, an adversarial review with no unanswered finding, and a passing end-to-end check.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| request_id | argument | required | request id, e.g. r-1a2b3c4d |
| state | argument | required | captured \| clarified \| planned \| in_progress \| delivered \| verified \| declined |
| --note | option |  |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests-capture`

Record an ask verbatim. Idempotent: re-running the same text returns the existing request.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| text | argument | required | the operator's ask, verbatim |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests-met`

Mark one acceptance criterion met, with the evidence that makes it so.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| request_id | argument | required | request id, e.g. r-1a2b3c4d |
| index | argument | required | criterion index, 0-based |
| --kind | option | required | commit \| ledger_seq \| file \| command \| screenshot \| url |
| --ref | option | required |  |
| --note | option |  |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests-show`

The full ask, each criterion with who wrote it and whether it is met, and its evidence. Kept as the original flat command name for `pravrudhi requests show`'s sake; identical rendering, either name.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| request_id | argument | required | request id, e.g. r-1a2b3c4d |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi requests-triage`

Give every captured ask its acceptance criteria now, instead of one per beat.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --limit | option | 0 | How many untriaged asks to draft criteria for; 0 = all. |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi routes`

Which model can be used right now, how well it has done, and when a spent one comes back.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi routing`

Which agent and model each difficulty tier would use now, and why, from measured outcomes.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --json | option | False |  |

## `pravrudhi search`

Show the shape of the search: how it branches, and how often selection actually had a choice.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi serve`

Serve the ledger, candidates, observations, evidence and inbox over HTTP (sign-off needs an operator header).

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --host | option | 127.0.0.1 |  |
| --port | option | 8765 |  |

## `pravrudhi status`

What the ledger says: chain, candidates, badges, nights, inbox.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi study`

Pre-registered studies that write real observe rows.

Subcommands: `harness-noise-floor`, `noise-floor`, `paired-confirm`.

## `pravrudhi study harness-noise-floor`

A/A of the baseline harness on the configured pool; writes the floor that config names.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --rotations | option | 3 |  |
| --seeds | option | 3 |  |
| --k | option | 100 |  |
| --night | option | 0 |  |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --config | option |  | Frozen night pre-registration to run; default research/prereg/harness_night.yaml. |

## `pravrudhi study noise-floor`

R rotations x S seeds of the unmodified trainee, each a real kernel-scored observe row; writes variance.json, or --out for a track that must not share the model track's floor.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --model | option | Qwen/Qwen3-4B |  |
| --bench | option | gsm8k-test |  |
| --template | option | harness/prompts/eval/gsm8k_v1.md |  |
| --root | option | . | Repository root holding contracts/ and gates/. |
| --rotations | option | 10 |  |
| --seeds | option | 3 |  |
| --k | option | 100 |  |
| --exposure-cap | option | 3 |  |
| --temperature | option | 0.7 |  |
| --max-new-tokens | option | 512 |  |
| --batch-size | option | 16 |  |
| --night | option | 0 |  |
| --out | option |  | Where to write the floor; default research/prereg/variance.json. |

## `pravrudhi study paired-confirm`

Incumbent adapter vs baseline on a fresh rotation, per-item paired; Hedges g with BCa CI and a permutation p.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --night | option | 1 |  |
| --candidate | option |  |  |
| --seed | option | 7 |  |
| --k | option | 100 |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi telegram-poll`

Answer whatever the operator has sent the bot since the last poll.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --json | option | False |  |

## `pravrudhi tools`

Tools, connectors and plugins this engine can draw on, and which are installed here.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --category | option |  |  |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi update`

Is a newer release available; and with --apply, catch this install up under the safeguards.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --apply | option | False | apply the update if every safeguard clears |
| --channel | option |  | dev (git checkout) or release (GitHub Release) |
| --rollback | option | False | switch back to the previous release install |
| --if-due | option | False | do nothing unless check_interval_min has elapsed |
| --json | option | False |  |

## `pravrudhi watch`

Look for the shape of a stall: a loop repeating itself, a night that cost nothing, a cheap account down.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |
| --notify | option | False | send the findings to the configured chat, if any |
| --json | option | False | machine-readable findings |
| --systemd-timer | option |  | a user-scope systemd timer unit (e.g. pravrudhi-heartbeat.timer) that is supposed to be firing this root's own heartbeat; reported immediately if systemd says it is not active, rather than waiting for heartbeat.jsonl to go stale |

## `pravrudhi workflow`

Declared, repeatable multi-step work: build it once, run it by name.

Subcommands: `list`, `run`, `show`.

## `pravrudhi workflow list`

Every workflow this workspace knows: packaged examples plus anything under workflows/.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi workflow run`

Run a workflow wave by wave, dispatching each step through the swarm.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| workflow_id | argument | required | workflow id, e.g. add-a-page |
| --input | option | [] | k=v, repeatable |
| --root | option | . | Repository root holding contracts/ and gates/. |

## `pravrudhi workflow show`

A workflow's inputs and steps.

| Flag or argument | Kind | Default | Meaning |
|---|---|---|---|
| workflow_id | argument | required | workflow id, e.g. add-a-page |
| --root | option | . | Repository root holding contracts/ and gates/. |
