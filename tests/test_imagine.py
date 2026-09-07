import json
from dataclasses import asdict

import pytest

from pravrudhi.application.imagine import Example, Imaginer, backtest, features, history


def example(night, target, strategy='sft'):
    return Example(night, str(night), {'strategy': strategy}, target, 'epoch')


def test_prior_responds_to_recipe_and_is_prediction_only():
    model = Imaginer([example(0, .5), example(1, -.5, 'grpo')], [])
    p = model.imagine({'strategy': 'sft'})
    assert p.predicted_anchor > model.imagine({'strategy': 'grpo'}).predicted_anchor
    assert p.lower == -1 and p.upper == 1
    assert asdict(p)['provenance'] == 'anumana'
    assert asdict(p)['usage'] == 'queue_only'
    assert 'observed' not in asdict(p)
    assert features({'strategy': 'sft', 'rationale': 'score=1'}) == features({'strategy': 'sft'})
    assert model.order_queue([{'strategy': 'grpo'}, {'strategy': 'sft'}])[0][0]['strategy'] == 'sft'


def test_calibration_is_separate_and_night_clustered():
    train = [example(0, 0)]
    cal = [example(n, .2) for n in range(1, 10)] + [example(1, .3)]
    model = Imaginer(train, cal)
    p = model.imagine({})
    assert p.predicted_anchor == 0
    assert p.calibration_nights == 9
    assert p.upper == pytest.approx(.3)
    with pytest.raises(ValueError):
        Imaginer(train, [example(0, 1)])
    with pytest.raises(ValueError):
        Imaginer([], [])


def ledger(tmp_path, later=0):
    rows = []
    for night in range(3):
        cid = str(night)
        rows.append(dict(kind='propose', candidate_id=cid, payload={'recipe': {'strategy': 'sft'}}))
        for arm, score in [('candidate', later if night == 2 else 1), ('incumbent', 0)]:
            ref = f'{night}-{arm}.jsonl'
            (tmp_path / ref).write_text(json.dumps({'id': 'item', 'score': score}) + '\n')
            rows.append(dict(kind='observe', actor='kernel', provenance='pratyaksha', night=night,
                             candidate_id=cid, payload={'arm': arm, 'observed': {
                                 'metric': 'pass_rate', 'value': score, 'n_items': 1,
                                 'per_item_scores_ref': ref}}))
    rows.append(dict(kind='observe', actor='kernel', provenance='anumana', payload={}))
    path = tmp_path / 'ledger.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in rows))
    return path


def test_later_outcomes_cannot_change_training_or_calibration(tmp_path):
    path = ledger(tmp_path)
    before, counts, _, epoch = history(path, tmp_path, 0)
    assert counts['kernel_observations'] == 6
    assert before[0].target == 1  # excludes its own item success
    ledger(tmp_path, later=1)
    after, _, _, new_epoch = history(path, tmp_path, 0)
    assert before[:2] == after[:2]
    assert epoch == new_epoch
    assert before[-1].target != after[-1].target
    result = backtest(path, tmp_path, 0, 1)
    assert result['sizes'] == {'train': 1, 'calibration': 1, 'test': 1}
    assert result['status'] == 'measured_backtest'


def test_missing_scores_are_counted(tmp_path):
    path = ledger(tmp_path)
    (tmp_path / '0-candidate.jsonl').unlink()
    _, counts, _, _ = history(path, tmp_path, 0)
    assert counts['missing_or_incomplete_scores'] == 1


def test_dynamics_uses_parent_state_but_never_child_target():
    from dataclasses import replace

    from pravrudhi.application.imagine import DynamicsImaginer
    train = [replace(example(n, v), parent='p', parent_state=(v, v, v))
             for n, v in enumerate([-.4, -.2, .2, .4])]
    model = DynamicsImaginer(train, [])
    query = replace(example(10, 0), parent='p', parent_state=(.3, .3, .3))
    prediction = model.imagine(query)
    assert prediction == model.imagine(replace(query, target=-1, candidate='different'))
    assert prediction.predicted_anchor > model.imagine(replace(query, parent_state=(-.3,)*3)).predicted_anchor
    assert prediction.provenance == 'anumana' and prediction.usage == 'queue_only'
    assert model.imagine(replace(query, parent_state=None)).predicted_anchor == pytest.approx(model.recall._prior(query.recipe))
    assert model.expected_free_energy(query) == model.expected_free_energy(replace(query, target=1))
    assert model.order_queue([query])[0][1] == prediction
    with pytest.raises(ValueError):
        model.imagine(replace(query, parent_state=(float('nan'), 0, 0)))


def test_parent_state_is_frozen_and_excludes_same_night(tmp_path):
    from pravrudhi.application.imagine import DynamicsImaginer
    path = ledger(tmp_path)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        if row['kind'] == 'propose':
            row['payload']['lineage'] = ['ignored-root', '0']
    path.write_text('\n'.join(json.dumps(r) for r in rows))
    before, _, _, _ = history(path, tmp_path, 0)
    assert before[0].parent_state is None
    assert before[1].parent == '0'
    assert before[1].parent_state is not None
    assert before[1].parent_state == before[2].parent_state
    model = DynamicsImaginer(before[:1], before[1:2])
    prediction = model.imagine(before[2])
    # Change a held-out parent measurement: neither frozen state nor prediction moves.
    (tmp_path / '2-incumbent.jsonl').write_text(json.dumps({'id': 'item', 'score': 1})+'\n')
    after, _, _, _ = history(path, tmp_path, 0)
    assert [x.parent_state for x in before] == [x.parent_state for x in after]
    assert model.imagine(after[2]) == prediction
    assert DynamicsImaginer(after[:1], after[1:2]).coef == model.coef
