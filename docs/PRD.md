# Product requirements

Improving a model is useful only if it gets better at something its user needs. Pravrudhi is being built so a user
can state an objective, run an improvement loop against a benchmark that measures it, and see whether the result
justifies keeping the change. The value is the connection between intent, measured progress and a usable artefact
whose history can be traced. A busy run or a promising proposal cannot substitute for that connection.

The intended users are model developers and people maintaining coding-agent harnesses who can run evaluations
on their own hardware. They need to improve a particular capability without manually coordinating every proposal,
experiment and comparison. A domain specialist might want answers grounded in relevant source material; a harness
maintainer might want more tasks completed correctly. Each needs a suitable benchmark and a measured baseline
before the engine can say whether a change helped.

The product is an installable engine with a command line, a local web interface and a hosted recorded site.
Since 2026-09-11 (ADR-0049, local decisions record) the product is its own repository, `pravrudhi-app`: a
clean-slate platform that pins an engine version from this repository's release channel and ships its own
interface. This repository is Studio, where the engine improves itself; a user's objective produces an artifact
repository of its own, of which `prabhasa-nyaya` is the operator's first.
Model adaptation and harness changes are its current execution paths. Objectives and progress reporting exist,
but connecting an objective to the execution choices remains work to complete. These requirements describe the
intended experience; the [roadmap](ROADMAP.md) distinguishes current behavior from the next work.

## Objectives and evidence

An objective must preserve the user's intent verbatim and name the track under which results accumulate, the
benchmarks that measure it and the recipes it may draw on. A benchmark must identify its scoring tool, metric
and direction of improvement. A target change is optional. An objective without a benchmark must be refused.
The engine must not imply that recording an intent automatically creates a training plan or a domain evaluator.

Progress must be recomputed from external evaluation results admitted to the ledger. The user must be able to
compare the baseline with the latest candidate measurement, inspect uncertainty and trace the comparison to its
source records. Missing baseline, baseline without a candidate comparison, and measured change must remain
visibly different. Uncertain change must be described as inconclusive, and a regression must follow the metric's
declared direction. An internal selection result must retain its evidence tier; external benchmark improvement
requires external scoring.

The recipe library must distinguish catalogued techniques from skills available on the user's machine and report
unknown references. A listed or installed recipe is not evidence of successful execution. Techniques for curation,
training and evaluation become evidence only through recorded runs. Public claims must remain within what the
ledger or a gate JSON supports, and public language must use English product terms.

## Command line requirements

The command line must support preparing a workspace, checking execution readiness and running a budgeted model
or harness experiment. The user must be able to author and inspect objectives, view progress and discover recipe
availability. Selecting an objective for execution must eventually connect its measurement choices to the run;
merely saving its prose is insufficient.

Runs must execute candidates in disposable environments and preserve the evaluator boundary. The user must be
able to inspect status, review proposed promotions through the inbox and export an accepted artefact with its
provenance. Promotion to the user's canonical model or harness remains an operator decision. Verification and
replay must make the record inspectable without requiring the browser interface.

## Local web interface requirements

The local interface must help a user state what they want, understand how it will be measured and follow the
result. Objectives must show intent alongside baseline and current results, uncertainty and recipe availability.
The run form must connect the objective to a supported target, benchmark, budget and proposer, and explain
unsupported combinations before presenting them as runnable work.

The user must be able to start and stop work, follow progress and revisit run history. Models must connect a
retained artefact to its comparison and make export accessible. Machines must explain execution availability;
settings must expose the choices needed to run the engine. Missing data, unreachable services and unavailable
operations must remain explicit rather than becoming apparent successes.

The engine must serve the interface and its API locally without an account. State-changing requests must retain
the engine's local authorization boundary. The main experience should explain the result in ordinary language,
with provenance available for inspection when the user needs to establish why a result is credible.

## Hosted site requirements

The hosted site must demonstrate the workflow using recorded engine output and clearly identify it as a recording.
Playback must not suggest that the visitor started a live experiment. Recorded results must retain their source
and scope, and unavailable actions must be clear. Installation guidance must lead users to their own local engine.
Connecting a remotely hosted interface to an engine is a separate deployment concern and must not be presented
as an existing account-based service.

## Success criteria

The product succeeds when a user can carry an objective from stated intent through a baseline and a candidate
comparison to an informed decision about an artefact. They must be able to identify what was measured, whether
the change supports their goal, what uncertainty remains and which records support the conclusion. A run that
finds no defensible improvement can still complete this workflow honestly.

Acceptance requires the command line and local interface to agree about objective progress and run outcomes,
exports to retain provenance, and recorded demonstrations to remain distinguishable from live work. Published
performance claims require supporting evidence; this document sets no unevidenced performance target.

## Non-goals

The current scope excludes interpreting free prose into an autonomous plan, choosing recipes on the user's behalf
and inventing benchmarks for unmeasured domains. It does not promise improvement for every model, task or budget.
Reimplementing the training systems represented by the recipe catalogue is outside the product's purpose.
Shared multi-user engine operation and billing are outside the current release scope. A desktop application is in scope as the milestone after the multi-user layer: a native window onto a locally running engine, which it detects and guides rather than installs, never bundling a Python runtime; the shell is attempted with Tauri and falls back to Electron under a pre-declared abort condition, Linux first and macOS second.
