import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from pravrudhi.application.loom_pipeline import Job, PipelineError, lift, lower
from pravrudhi.application.loom_run import PlannedJob, dry_run, run

SOURCE = '''// Preserve this comment and 1e-4 spelling.
model seed = load("models/seed");
corpus data = load("data/train.jsonl");
a = sft(model=seed, corpus=data) { sft_lr = 1e-4; };
b = sft(model=a, corpus=data);
c = sft(model=b, corpus=data);
d = sft(model=c, corpus=data);
'''


def planner(stage, inputs):
    return PlannedJob(Job('train_sft', ('--model-dir', '/model'),
                          ((inputs['model'], '/model'), (inputs['corpus'], '/in/train.jsonl'))),
                      spend='host reservation: opaque')


PLANNERS = {'sft': planner}


class Context:
    def __init__(self, root, fail=None, crash=False):
        self.root, self.fail, self.crash = root, fail, crash
        self.calls = []

    def job_dir(self, name):
        path = self.root / f'{name}-{len(self.calls)}'
        (path / 'in').mkdir(parents=True, exist_ok=True)
        (path / 'out').mkdir(exist_ok=True)
        return path

    def run_raw(self, command, args, jd, mounts, timeout):
        name = jd.name.split('-')[0]
        self.calls.append((name, command, args, mounts, timeout))
        if name == self.fail and self.crash:
            raise RuntimeError('interrupted')
        (jd / 'out' / 'adapter').mkdir(exist_ok=True)
        return SimpleNamespace(exit_code=int(name == self.fail)), {'kernel_ref': name}


def test_dry_run_pure_and_round_trip(tmp_path):
    pipeline = lower(SOURCE)
    manifest = dry_run(pipeline, planners=PLANNERS)
    assert not list(tmp_path.iterdir())
    assert manifest['stages'][3]['dependencies'] == ['c']
    assert manifest['stages'][0]['spend'] == 'host reservation: opaque'
    assert manifest['stages'][0]['job']['timeout_s'] == 5400
    assert lift(pipeline) == lower(SOURCE).program
    assert pipeline.source == SOURCE


def test_dependency_order_from_edges():
    pipeline = lower(SOURCE)
    shuffled = replace(pipeline, stages=tuple(reversed(pipeline.stages)))
    assert dry_run(shuffled, planners=PLANNERS) == dry_run(pipeline, planners=PLANNERS)


def test_resume_stage_four(tmp_path):
    pipeline = lower(SOURCE)
    context = Context(tmp_path / 'jobs', fail='d')
    checkpoint = tmp_path / 'run.json'
    with pytest.raises(PipelineError, match='d: engine job failed'):
        run(pipeline, context, checkpoint, planners=PLANNERS)
    assert [call[0] for call in context.calls] == list('abcd')
    context.fail = None
    values = run(pipeline, context, checkpoint, planners=PLANNERS)
    assert [call[0] for call in context.calls] == list('abcdd')
    assert values['d']['metadata'] == {'kernel_ref': 'd'}
    assert context.calls[-1][3][values['c']['path']] == '/model'
    run(pipeline, context, checkpoint, planners=PLANNERS)
    assert len(context.calls) == 5


def test_interrupted_job_not_blindly_relaunched(tmp_path):
    context = Context(tmp_path / 'jobs', fail='d', crash=True)
    checkpoint = tmp_path / 'run.json'
    with pytest.raises(RuntimeError):
        run(lower(SOURCE), context, checkpoint, planners=PLANNERS)
    with pytest.raises(PipelineError, match='reconciliation'):
        run(lower(SOURCE), context, checkpoint, planners=PLANNERS)
    assert len(context.calls) == 4


def test_changed_job_or_missing_artifact_refuses_resume(tmp_path):
    context = Context(tmp_path / 'jobs')
    checkpoint = tmp_path / 'run.json'
    pipeline = lower(SOURCE)
    values = run(pipeline, context, checkpoint, planners=PLANNERS)
    changed = {'sft': lambda s, i: replace(planner(s, i), spend='different reservation')}
    with pytest.raises(PipelineError, match='manifest differs'):
        run(pipeline, context, checkpoint, planners=changed)
    from pathlib import Path
    Path(values['a']['path']).rmdir()
    with pytest.raises(PipelineError, match='artifact is missing'):
        run(pipeline, context, checkpoint, planners=PLANNERS)
    assert len(context.calls) == 4


def test_default_sft_contract(tmp_path):
    pipeline = lower('model m = load("model"); corpus c = load("train.jsonl"); '
                     's = sft(model=m, corpus=c) { lora_r = 16; };')
    manifest = dry_run(pipeline)
    assert json.loads(manifest['stages'][0]['files'][0][1])['lora']['r'] == 16
    assert manifest['stages'][0]['job']['command'] == 'train_sft'
    assert manifest['stages'][0]['spend'] == 'unknown until kernel execution'
    ctx = Context(tmp_path / 'jobs')
    run(pipeline, ctx, tmp_path / 'run.json')
    assert ctx.calls[0][1] == 'train_sft'


def test_preflight_all_stages_before_io(tmp_path):
    context = Context(tmp_path)
    with pytest.raises(PipelineError, match='continuation'):
        run(lower(SOURCE), context, tmp_path / 'run.json')
    assert not list(tmp_path.iterdir())
    pipeline = lower('model m = load("m"); corpus c = load("c"); '
                     'a = sft(model=m, corpus=c); b = pretrain(model=a, corpus=c);')
    with pytest.raises(PipelineError, match='pretrain'):
        run(pipeline, context, tmp_path / 'run.json')
    assert not list(tmp_path.iterdir())


def test_invalid_grammar_and_ir():
    pipeline = lower('model m = load("m"); corpus c = load("c"); '
                     'a = sft(model=m, corpus=c) { lora_r = 900; };')
    with pytest.raises(ValueError):
        dry_run(pipeline)
    with pytest.raises(PipelineError, match='source tree'):
        dry_run(replace(pipeline, stages=()))
