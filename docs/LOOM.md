# Loom model pipelines

`pravrudhi.application.loom_pipeline.lower(source)` compiles a program without
I/O or GPU work. `lift(pipeline)` returns the identical AST, including source
positions and numeric spellings. `pipeline.source` retains the exact input text,
including comments and whitespace. The older intent-plan `loom.lower` API is
unchanged.

```loom
corpus text = load("datasets/text.jsonl");
corpus instructions = load("datasets/instructions.jsonl");
model initial = load("models/initial");
model teacher = load("models/teacher");
evalset reasoning = load("benchmarks/reasoning");
model base = pretrain(model=initial, corpus=text) { tokens = 800M; };
model continued = continue_pretrain(model=base, corpus=text);
model tuned = sft(model=continued, corpus=instructions) { lora_r = 16; };
model student = distill(teacher=teacher, student=tuned, corpus=instructions);
evaluation results = evaluate(model=student, benchmark=reasoning);
promotion release = promote(model=student, evaluation=results);
```

Locations and model roles are supplied by the program. Declare additional named
benchmarks and evaluation stages to evaluate several suites. Options in blocks
are literal job configuration, never measured evidence. Binding validators decide
which objectives and options their engine supports. Resources use `load` with a
single string. Every stage requires the named roles shown above; references must
point to earlier values of the correct type. Output names are unique. Promotion
must reference an evaluation of the same model. Unsupported syntax, including
`circuit`, fails explicitly rather than disappearing from execution.

## Execution boundary

`execute(pipeline, context, bindings, promote=policy)` preflights all capabilities
and option validators before creating jobs. The context is the existing
`execute.NightContext`. A binding prepares job inputs and returns `Job(command,
args, mounts, timeout_s, output)`. The dispatcher calls `NightContext.run_raw`,
which constructs the kernel `JobSpec` and calls `run_job`. There is no alternative
sandbox, scorer or ledger writer. Jobs run in dependency order; a failed exit or
missing metadata stops the pipeline. Results carry output paths and opaque
metadata, not Loom-computed scores.

`sft_binding()` is a concrete binding for an existing model snapshot and prepared
supervised JSONL corpus. It writes the existing `LoraRecipe` format and mounts the
corpus as `/in/train.jsonl` for `train_sft`. `lora_recipe(stage)` validates prefixed
`sft_*`, `lora_*`, and `grpo_*` fields using the engine grammar.
`harness_recipe(stage)` validates harness configuration with `HarnessRecipe`.
These helpers retain the grammars' current restrictions and defaults.

```python
from pravrudhi.application.loom_pipeline import lower, execute, sft_binding

pipeline = lower('''
model base = load("/snapshots/model");
corpus examples = load("/datasets/supervised.jsonl");
model tuned = sft(model=base, corpus=examples) { lora_r = 16; };
''')
# Explicitly opt into execution with an existing engine context:
outputs = execute(pipeline, context, {"sft": sft_binding()})
```

The full example compiles, but **does not yet execute without additional engine
bindings**. The named executor has no pretraining implementation and its existing
teacher sampling/evaluation/promotion orchestration assumes particular resources.
This change does not disguise SFT as pretraining or silently ignore the teacher.
Bindings for pretraining, continuation, distillation and named evaluation must
supply the corresponding supported engine jobs and input preparation. Adapter
continuation also requires an explicit binding; the concrete SFT binding accepts
base snapshots only. Missing bindings fail before any jobs run.

Promotion is a policy callback, not a GPU command. The host must connect its
existing evidence-admission and promotion policy; successful job exit alone is
never evidence for promotion. No default callback writes promotion rows.

CPU verification: `uv run pytest tests/test_loom_pipeline.py -q`. Tests use a fake
context and never launch training.

## Durable job execution

`pravrudhi.application.loom_run` adds `dry_run(pipeline)` and
`run(pipeline, context, record_path)`. The default planner runs prepared SFT using
`train_sft` and the existing `LoraRecipe` grammar. For example:

```python
from pathlib import Path
from pravrudhi.application.loom_run import dry_run, run

manifest = dry_run(pipeline)  # no directories, input writes, or kernel launches
print(manifest)
outputs = run(pipeline, context, Path("loom-run.json"))
# Calling run again with the same record reuses completed stages.
```

The manifest contains every command, argument, read-only mount, input file,
output name, timeout, dependency, and supplied spend reservation. It is JSON
serializable. Resource references determine execution order even when the lowered
stage tuple is reordered. The original AST and source remain unchanged.

Actual expenditure cannot be known exactly before execution: kernel jobs measure
runtime. The default spend field explicitly says `unknown until kernel execution`.
Loom neither calls the grammar's heuristic cost estimator nor invents a dollar,
token, GPU-hour, or score measurement. A host planner may supply an opaque quote
or reservation via `PlannedJob.spend`; this is configuration, not evidence. Thus
this is an exact job manifest, **not a guarantee of exact monetary spend**.

Custom pure planners can be supplied as `planners={operation: planner}` to both
functions. A planner receives the stage and role-to-location mapping and returns
`PlannedJob(Job(...), files=((relative_name, content), ...), spend=...)`.
Upstream outputs are symbolic `@stage` mount sources; the runner substitutes the
recorded output path at launch. Planners must use supported engine commands and
must include all preparation in the manifest. They must not launch work or score
results. The default planner rejects adapter continuation during preflight.

The record binds the complete manifest, including recipes and reservations, to
completed output paths and opaque kernel job metadata. It is atomically replaced
and locked against concurrent runners. Failed jobs stop execution; retrying skips
completed dependencies. Changed manifests, missing completed artifacts, and
inconsistent checkpoints fail before launch. Locations identify resources; this
record does not hash their contents or replace kernel provenance checks.

An interruption after launch but before checkpointing leaves `status: running`
and the job directory in the record. Resume refuses to duplicate that job. The
host must reconcile its kernel outcome before changing the checkpoint to failed
(retry permitted) or complete (with its artifact and kernel metadata). This
explicit recovery boundary avoids treating an uncertain job as unexecuted.

Pretraining, continued pretraining, named evaluation, and teacher distillation
still require supported host planners; this module does not add missing engine
capabilities. Promotion is refused by the durable raw-job runner because it needs
the engine's evidence-admission/policy integration, not a command exit status.
The older `loom_pipeline.execute` callback API remains available and unchanged.
Consequently the full lifecycle example above is not yet an executable default
pipeline; prepared SFT is the concrete supported path.

CPU verification:
`uv run pytest tests/test_loom_run.py tests/test_loom_pipeline.py -q`.
The tests fake the context boundary and do not train models.
