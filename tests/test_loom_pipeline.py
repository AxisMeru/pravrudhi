from types import SimpleNamespace

import pytest

from pravrudhi.application.loom import lift as parse
from pravrudhi.application.loom_pipeline import (
    STAGE_EXECUTABILITY,
    STAGE_EXECUTABLE,
    STAGE_PENDING,
    Binding,
    Job,
    PipelineError,
    executable_bindings,
    execute,
    harness_recipe,
    lift,
    lora_recipe,
    lower,
    sft_binding,
)

SOURCE = '''// arbitrary resources and model roles
corpus raw = load("datasets/raw.jsonl");
corpus lessons = load("datasets/lessons.jsonl");
model seed = load("models/seed");
model teacher = load("models/teacher");
evalset math = load("benchmarks/math");
evalset code = load("benchmarks/code");
model base = pretrain(model=seed, corpus=raw) { tokens = 800M; };
model continued = continue_pretrain(model=base, corpus=raw);
model tuned = sft(model=continued, corpus=lessons);
model student = distill(teacher=teacher, student=tuned, corpus=lessons);
evaluation a = evaluate(model=student, benchmark=math);
evaluation b = evaluate(model=student, benchmark=code);
promotion release = promote(model=student, evaluation=b);
'''


def test_exact_round_trip():
    pipeline = lower(SOURCE)
    assert lift(pipeline) == parse(SOURCE)
    assert pipeline.source == SOURCE
    assert lower(lift(pipeline)).stages == pipeline.stages
    assert dict(pipeline.stages[0].options) == {"tokens": 800_000_000}
    assert dict(pipeline.stages[3].inputs)["teacher"] == "teacher"


class Context:
    def __init__(self, root, fail=False):
        self.root, self.fail, self.calls = root, fail, []

    def job_dir(self, name):
        path = self.root / name
        (path / 'in').mkdir(parents=True)
        (path / 'out').mkdir()
        return path

    def run_raw(self, *args):
        self.calls.append(args)
        return SimpleNamespace(exit_code=int(self.fail)), {"kernel_ref": "opaque"}


def test_pipeline_dispatch_and_policy(tmp_path):
    ctx = Context(tmp_path)
    prepared = []
    def prepare(stage, inputs, jd):
        prepared.append((stage, inputs))
        return Job("existing-engine-command", (stage.name,))
    bindings = {op: Binding(lambda stage: None, prepare) for op in
                ('pretrain', 'continue_pretrain', 'sft', 'distill', 'evaluate')}
    policy = []
    result = execute(lower(SOURCE), ctx, bindings,
                     promote=lambda stage, inputs: policy.append(inputs) or 'kernel-decision')
    assert len(ctx.calls) == 6
    assert prepared[3][1]['teacher'] == 'models/teacher'
    assert prepared[3][1]['student']['path'] == tmp_path / 'tuned/out/adapter'
    assert policy[0]['evaluation']['metadata'] == {'kernel_ref': 'opaque'}
    assert result['release'] == 'kernel-decision'


def test_missing_capability_preflight(tmp_path):
    ctx = Context(tmp_path)
    with pytest.raises(PipelineError, match='no binding'):
        execute(lower(SOURCE), ctx, {})
    assert not ctx.calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('source', [
    'model m = pretrain(model=missing, corpus=missing);',
    'model m = load("x"); model m = load("y");',
    'corpus c = load("x"); model m = sft(model=c, corpus=c);',
    'model m = load("x"); x = sft(model=m, model=m);',
    'circuit c = unknown();',
    'import "anything";',
    'model m = load("x"); x = typo(model=m);',
    'model m = load("x"); evalset b = load("b"); '
    'model n = load("y"); e = evaluate(model=m, benchmark=b); p = promote(model=n, evaluation=e);',
])
def test_reject_invalid(source):
    with pytest.raises(PipelineError):
        lower(source)


def test_recipe_grammars():
    stage = lower('model m = load("m"); corpus c = load("c"); '
                  'n = sft(model=m, corpus=c) { lora_r = 16; sft_lr = 1e-4; };').stages[0]
    assert lora_recipe(stage).lora.r == 16
    stage = lower('model m = load("m"); evalset b = load("b"); '
                  'e = evaluate(model=m, benchmark=b) { strategy = "prompt_only"; '
                  'execution_family = "template"; };').stages[0]
    assert harness_recipe(stage).strategy == 'prompt_only'


def test_concrete_sft_contract(tmp_path):
    ctx = Context(tmp_path)
    p = lower('model m = load("/snapshots/m"); corpus c = load("/data/train.jsonl"); '
              'n = sft(model=m, corpus=c) { lora_r = 16; };')
    execute(p, ctx, {'sft': sft_binding()})
    command, args, jd, mounts, timeout = ctx.calls[0]
    assert command == 'train_sft'
    assert args[:2] == ['--model-dir', '/model']
    assert mounts['/data/train.jsonl'] == '/in/train.jsonl'
    assert '"r": 16' in (jd / 'in/recipe.json').read_text()


def test_job_failure_stops_pipeline(tmp_path):
    ctx = Context(tmp_path, fail=True)
    p = lower('model m = load("m"); corpus c = load("c"); n = sft(model=m, corpus=c);')
    with pytest.raises(PipelineError, match='job failed'):
        execute(p, ctx, {'sft': sft_binding()})
    assert len(ctx.calls) == 1


def test_invalid_recipe_preflight(tmp_path):
    ctx = Context(tmp_path)
    p = lower('model m = load("m"); corpus c = load("c"); '
              'n = sft(model=m, corpus=c) { lora_r = 1000; };')
    with pytest.raises(ValueError):
        execute(p, ctx, {'sft': sft_binding()})
    assert not list(tmp_path.iterdir())


def test_promotion_requires_policy_before_jobs(tmp_path):
    ctx = Context(tmp_path)
    p = lower('model m = load("m"); evalset b = load("b"); '
              'e = evaluate(model=m, benchmark=b); p = promote(model=m, evaluation=e);')
    binding = Binding(lambda stage: None, lambda *args: Job('eval'))
    with pytest.raises(PipelineError, match='policy handler'):
        execute(p, ctx, {'evaluate': binding})
    assert not list(tmp_path.iterdir())


def test_tampered_ir_rejected(tmp_path):
    from dataclasses import replace
    p = lower(SOURCE)
    with pytest.raises(PipelineError, match='source tree'):
        execute(replace(p, stages=()), Context(tmp_path), {})


def test_stage_executability_declares_every_loom_stage():
    """docs/LOOM.md's Execution boundary must name every grammar stage exactly once."""
    assert set(STAGE_EXECUTABILITY) == {
        'pretrain', 'continue_pretrain', 'sft', 'distill', 'evaluate', 'promote',
    }
    assert STAGE_EXECUTABILITY['sft'] == STAGE_EXECUTABLE
    assert all(status == STAGE_PENDING for op, status in STAGE_EXECUTABILITY.items() if op != 'sft')


def test_executable_bindings_matches_executability_table():
    """The concrete binding registry must offer exactly the stages marked executable."""
    assert set(executable_bindings()) == {
        op for op, status in STAGE_EXECUTABILITY.items() if status == STAGE_EXECUTABLE
    }


def test_executable_bindings_runs_prepared_sft(tmp_path):
    ctx = Context(tmp_path)
    p = lower('model m = load("/snapshots/m"); corpus c = load("/data/train.jsonl"); '
              'n = sft(model=m, corpus=c) { lora_r = 16; };')
    execute(p, ctx, executable_bindings())
    assert ctx.calls[0][0] == 'train_sft'


def test_pending_stage_fails_preflight_without_host_binding(tmp_path):
    """A stage marked pending must still fail loudly, never run silently."""
    ctx = Context(tmp_path)
    p = lower('model m = load("m"); corpus c = load("c"); n = pretrain(model=m, corpus=c);')
    with pytest.raises(PipelineError, match='no binding'):
        execute(p, ctx, executable_bindings())
    assert not ctx.calls
