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
