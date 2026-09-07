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
