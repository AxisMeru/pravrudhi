import json
import random
from dataclasses import asdict

import pytest

from pravrudhi.application import variation as v
from pravrudhi.application.archive import selection_pressure
from pravrudhi.targets.harness_grammar import HarnessRecipe, parse_harness
from pravrudhi.targets.lora_grammar import LoraRecipe, parse_recipe


def parents(harness=False):
    if harness:
        return [v.Parent('a', HarnessRecipe(strategy='prompt_only', execution_family='template')),
                v.Parent('b', HarnessRecipe(strategy='retry_policy', execution_family='retries',
                                           retries=3, n_samples=4, temperature=0.9, thinking=True))]
    return [v.Parent('a', LoraRecipe(strategy='sft_rejection', execution_family='data_mixture')),
            v.Parent('b', LoraRecipe(strategy='grpo_verifiable', execution_family='grpo',
                                    lora={'r': 64, 'alpha': 256, 'dropout': 0.3},
                                    sft={'n_kept': 4096, 'lr': 0.005},
                                    grpo={'steps': 60, 'lr': 0.0001}, eval_template='gsm8k_v3_boxed'))]


@pytest.mark.parametrize('harness', [False, True])
@pytest.mark.parametrize('operator', ['mutation', 'block_crossover', 'field_crossover'])
def test_real_grammar(operator, harness):
    a, b = parents(harness)
    rng = random.Random(713)
    counts = v.Counts()
    parse = parse_harness if harness else parse_recipe
    for _ in range(300):
        child = v.vary(a, other=b, operator=operator, rng=rng, counts=counts)
        assert child is not None
        assert not isinstance(parse(child.recipe.model_dump()), str)
        assert v.canonical(child.recipe) not in {v.canonical(a.recipe), v.canonical(b.recipe)}
        assert operator in child.recipe.rationale and 'a' in child.recipe.rationale
        left = {p: value for p, _, value in v._leaves(a.recipe)}
        right = {p: value for p, _, value in v._leaves(b.recipe)}
        actual = {p: value for p, _, value in v._leaves(child.recipe)}
        if operator == 'mutation':
            assert sum(actual[p] != left[p] for p in left) == 1
        else:
            assert all(actual[p] in (left[p], right[p]) for p in left)
            if operator == 'block_crossover' and not harness:
                for block in ('lora', 'sft', 'grpo'):
                    assert getattr(child.recipe, block) in (getattr(a.recipe, block), getattr(b.recipe, block))
    assert counts.attempts == counts.accepted + counts.invalid + counts.duplicate + counts.blocked
    assert counts.rejection_rate < 0.65
    print(harness, operator, asdict(counts), counts.rejection_rate)


def test_bounded_rejection_and_blocklist(monkeypatch):
    a, b = parents()
    child = v.vary(a, rng=random.Random(4))
    monkeypatch.setattr(v, '_draw', lambda *args: child.recipe.model_dump())
    counts = v.Counts()
    assert v.vary(a, rng=random.Random(4), blocklist={v.diff_hash(child.recipe)}, counts=counts) is None
    assert counts.attempts == counts.blocked == 32
    counts = v.Counts()
    assert v.vary(a, rng=random.Random(4), seen={v.canonical(child.recipe)}, counts=counts) is None
    assert counts.duplicate == 32
    monkeypatch.setattr(v, '_draw', lambda *args: {'strategy': 'invalid'})
    counts = v.Counts()
    assert v.vary(a, rng=random.Random(4), counts=counts) is None
    assert counts.invalid == 32
    assert v.vary(a, other=a, operator='block_crossover', rng=random.Random(4)) is None


@pytest.mark.parametrize('harness', [False, True])
def test_archive_pool_pressure(tmp_path, harness):
    archive = parents(harness)
    ledger = tmp_path / 'ledger.jsonl'
    rows = [dict(kind='propose', candidate_id=p.candidate_id, night=0,
                 payload={'recipe': p.recipe.model_dump(), 'lineage': []}) for p in archive]
    rows += [dict(kind='prune', candidate_id='a', night=0, payload={})]
    ledger.write_text('\n'.join(map(json.dumps, rows)))
    loaded = v.load_archive(ledger, harness=harness)
    assert loaded == archive
    first = v.generate(loaded, budget=8, rng=random.Random(12), blocklist=set())
    blocked = {v.diff_hash(c.recipe) for c in first}
    counts = v.Counts()
    children = v.generate(loaded, budget=8, rng=random.Random(12), blocklist=blocked, counts=counts)
    assert len(children) == 16
    assert len({v.canonical(c.recipe) for c in children}) == 16
    assert not blocked.intersection(v.diff_hash(c.recipe) for c in children)
    assert any('a' in c.parents for c in children)
    assert counts.blocked > 0
    for i, child in enumerate(children):
        payload = child.payload()
        assert payload['variation_parents'] == list(child.parents)
        assert payload['vak']['para'] == child.recipe.rationale
        rows.append(dict(kind='propose', candidate_id=f'new-{i}', night=1, payload=payload))
        if i < 8:
            rows.append(dict(kind='select', candidate_id=f'new-{i}', night=1, payload={}))
    ledger.write_text('\n'.join(map(json.dumps, rows)))
    pressure = selection_pressure(ledger)[0]
    assert pressure.selected == 8 and pressure.live >= 16 and pressure.binding


def test_seed_reproducible_and_empty():
    assert v.generate([], budget=8, rng=random.Random(1), blocklist=set()) == []
    a = v.generate(parents(), budget=8, rng=random.Random(1), blocklist=set())
    assert a == v.generate(parents(), budget=8, rng=random.Random(1), blocklist=set())
    assert v.load_archive(__import__('pathlib').Path('/tmp/no-such-variation-ledger')) == []
