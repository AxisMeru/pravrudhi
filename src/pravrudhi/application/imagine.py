"""Prior-only recipe imagination; outputs are queue advice, never kernel evidence.

Adapts PWM's observation/prior separation and softmax associative retrieval.
Includes an experimental linear Gaussian parent-state transition and EFE queue score.
No neural RSSM, synthetic observations, evaluator, gate, or ledger writer lives here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from .anchor import anchored, difficulty, load_per_item
from .archive import parent_map


def features(recipe: Mapping[str, Any]) -> frozenset[str]:
    """Execution fields only: prose, identifiers and claimed outcomes are not inputs."""
    allowed = {'strategy', 'execution_family', 'lora', 'sft', 'grpo', 'eval_template'}
    out: set[str] = set()

    def visit(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, child in sorted(value.items()):
                visit(child, path + '.' + key)
        else:
            out.add(path + '=' + json.dumps(value, sort_keys=True))
    for key in sorted(allowed & recipe.keys()):
        visit(recipe[key], key)
    return frozenset(out)


@dataclass(frozen=True)
class Example:
    night: int
    candidate: str
    recipe: Mapping[str, Any]
    target: float
    epoch: str
    parent: str | None = None
    parent_state: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class Imagination:
    predicted_anchor: float
    lower: float
    upper: float
    calibration_nights: int
    difficulty_epoch: str
    provenance: str = field(default='anumana', init=False)
    usage: str = field(default='queue_only', init=False)


class Imaginer:
    """Fixed beta=4 Hopfield key/value recall with chronological conformal residuals.

    Unit-normalized recipe keys, observed anchored scores as values. Calibration
    uses maximum absolute error per night to avoid treating paired runs as IID.
    Coverage is empirical under temporal drift, not a distribution-free promise.
    """
    def __init__(self, training: list[Example], calibration: list[Example], alpha: float = 0.1) -> None:
        if not training or not 0 < alpha < 1:
            raise ValueError('Need training evidence and 0 < alpha < 1')
        if calibration and max(x.night for x in training) >= min(x.night for x in calibration):
            raise ValueError('Calibration must follow training')
        epochs = {x.epoch for x in training}
        if len(epochs) != 1 or any(not math.isfinite(x.target) or abs(x.target) > 1 for x in training + calibration):
            raise ValueError('Targets must share a frozen difficulty epoch and lie in [-1, 1]')
        self.epoch = training[0].epoch
        self.memory = tuple((features(x.recipe), x.target) for x in training)
        self.baseline = mean(x.target for x in training)
        errors: dict[int, float] = {}
        for x in calibration:
            errors[x.night] = max(errors.get(x.night, 0), abs(x.target - self._prior(x.recipe)))
        self.n_calibration = len(errors)
        rank = math.ceil((len(errors) + 1) * (1 - alpha))
        self.radius = sorted(errors.values())[rank - 1] if rank <= len(errors) else 2.0

    def _prior(self, recipe: Mapping[str, Any]) -> float:
        query = features(recipe)
        weights = [math.exp(4 * len(query & key) / math.sqrt(len(query) * len(key)))
                   if query and key else 1.0 for key, _ in self.memory]
        return sum(w * value for w, (_, value) in zip(weights, self.memory, strict=False)) / sum(weights)

    def imagine(self, recipe: Mapping[str, Any]) -> Imagination:
        prediction = self._prior(recipe)
        return Imagination(prediction, max(-1., prediction - self.radius),
                           min(1., prediction + self.radius), self.n_calibration, self.epoch)

    def order_queue(self, recipes: list[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], Imagination]]:
        """Stable pragmatic ordering only; does not evaluate or emit evidence rows."""
        return sorted(((r, self.imagine(r)) for r in recipes),
                      key=lambda pair: pair[1].predicted_anchor, reverse=True)


class DynamicsImaginer:
    """Experimental three-timescale linear Gaussian state transition.

    A small-data adaptation, not PWM's neural categorical RSSM. Parent posterior
    summaries evolve at rates 1, 1/4, 1/16 per measured night. A Bayesian linear
    transition predicts the child residual over recipe recall. Fixed ridge .01;
    no calibration/test fitting. Missing state falls back to recipe recall.
    """
    def __init__(self, training: list[Example], calibration: list[Example]) -> None:
        self.recall = Imaginer(training, calibration)
        self.epoch = self.recall.epoch
        pairs = [(x.parent_state, x.target - self.recall._prior(x.recipe))
                 for x in training if x.parent_state is not None]
        # Include an intercept only for the state-conditioned branch.
        design = [((1., *state), y) for state, y in pairs]
        a = [[sum(v[i] * v[j] for v, _ in design) + (.01 if i == j else 0.)
              for j in range(4)] for i in range(4)]
        augmented = [row + [float(i == j) for j in range(4)] for i, row in enumerate(a)]
        for i in range(4):
            pivot = augmented[i][i]
            augmented[i] = [v / pivot for v in augmented[i]]
            for j in range(4):
                if j != i:
                    factor = augmented[j][i]
                    augmented[j] = [v - factor * w for v, w in zip(augmented[j], augmented[i], strict=False)]
        self.inverse = [row[4:] for row in augmented]
        b = [sum(v[i] * y for v, y in design) for i in range(4)]
        self.coef = [sum(row[j] * b[j] for j in range(4)) for row in self.inverse]
        self.noise = max(1e-8, mean((y - sum(c*t for c, t in zip(self.coef, v, strict=False)))**2
                                   for v, y in design)) if design else 1.
        errors: dict[int, float] = {}
        for x in calibration:
            errors[x.night] = max(errors.get(x.night, 0.), abs(x.target - self.prior(x)))
        self.n_calibration = len(errors)
        rank = math.ceil((len(errors) + 1) * .9)
        self.radius = sorted(errors.values())[rank-1] if rank <= len(errors) else 2.

    def prior(self, x: Example) -> float:
        p = self.recall._prior(x.recipe)
        if x.parent_state is not None:
            if any(not math.isfinite(v) or abs(v) > 1 for v in x.parent_state):
                raise ValueError('Parent state must contain measured anchored summaries')
            p += sum(c*v for c, v in zip(self.coef, (1., *x.parent_state), strict=False))
        return max(-1., min(1., p))

    def imagine(self, x: Example) -> Imagination:
        p = self.prior(x)
        return Imagination(p, max(-1., p-self.radius), min(1., p+self.radius),
                           self.n_calibration, self.epoch)

    def expected_free_energy(self, x: Example) -> float:
        """Gaussian preference risk minus linear-model information gain.

        Preference N(1,1); observation noise is training residual variance.
        This is model-based queue advice, not measured policy improvement.
        """
        v = (1., *x.parent_state) if x.parent_state is not None else (0.,)*4
        epistemic = max(0., self.noise * sum(v[i]*self.inverse[i][j]*v[j]
                                            for i in range(4) for j in range(4)))
        return .5*((1-self.prior(x))**2 + self.noise + epistemic) - .5*math.log1p(epistemic/self.noise)

    def order_queue(self, candidates: list[Example]) -> list[tuple[Example, Imagination]]:
        return [(x, self.imagine(x)) for x in sorted(candidates, key=self.expected_free_energy)]


def history(ledger: Path, root: Path, train_through: int) -> tuple[list[Example], dict[str, int], str, str]:
    """Read only kernel measurements and their explicitly referenced score files.

    Training difficulty is frozen before later nights are inspected. Training
    targets use anchor.py's LOO; later labels use training plus their own night's
    measurements (LOO), never supplied to the predictor. Label epochs are distinct.
    Unseen/partial items and absent recipes are excluded and counted explicitly.
    """
    data = ledger.read_bytes()
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    proposals: dict[str, Any] = {}
    observations: list[Any] = []
    counts: Counter[str] = Counter()
    for row in rows:
        payload = row.get('payload', {})
        if row.get('kind') == 'propose':
            proposals[row['candidate_id']] = payload.get('recipe', {})
        if (row.get('kind') != 'observe' or row.get('actor') != 'kernel'
                or row.get('provenance') != 'pratyaksha'):
            continue
        counts['kernel_observations'] += 1
        obs = payload.get('observed', {})
        ref = obs.get('per_item_scores_ref')
        if obs.get('metric') != 'pass_rate' or not ref:
            counts['missing_reference'] += 1
            continue
        path = (root / ref).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('Score reference escapes root')
        scores = load_per_item(path)
        if not scores or len(scores) != obs.get('n_items'):
            counts['missing_or_incomplete_scores'] += 1
            continue
        recipe = proposals.get(row['candidate_id'])
        observations.append((row, scores, recipe))
    table = difficulty(scores for row, scores, _ in observations if row['night'] <= train_through)
    later_tables = {night: difficulty(scores for row, scores, _ in observations
                                      if row['night'] <= train_through or row['night'] == night)
                    for night in {row['night'] for row, _, _ in observations if row['night'] > train_through}}
    # Only training measurements may initialise held-out parent states. Aggregate
    # within a night before recurrence so seed replication does not set the clock.
    parents = parent_map(ledger)
    parent_values: dict[tuple[str, int], list[float]] = {}
    for row, scores, _ in observations:
        if row['night'] <= train_through:
            result = anchored(scores, row['payload']['observed']['value'], table)
            if result.complete and result.anchored is not None:
                parent_values.setdefault((row['candidate_id'], row['night']), []).append(result.anchored)

    def state_before(parent: str | None, night: int) -> tuple[float, float, float] | None:
        state = None
        for (cid, measured_night), values in sorted(parent_values.items(), key=lambda p: p[0][1]):
            if cid != parent or measured_night >= night:
                continue
            value = mean(values)
            if state is None:
                state = (value, value, value)
            else:
                fast, mid, slow = state
                state = (fast + 1.0 * (value - fast), mid + 0.25 * (value - mid), slow + 0.0625 * (value - slow))
        return state

    examples = []
    for row, scores, recipe in observations:
        if not recipe or row['payload'].get('arm') != 'candidate':
            counts['not_recipe_candidate'] += 1
            continue
        value = row['payload']['observed']['value']
        if row['night'] <= train_through:
            result = anchored(scores, value, table)
            target = result.anchored if result.complete else None
        else:
            result = anchored(scores, value, later_tables[row['night']])
            target = result.anchored if result.complete else None
        if target is None:
            counts['unanchorable'] += 1
            continue
        examples.append(Example(row['night'], row['candidate_id'], recipe, target, result.epoch,
                                parents.get(row['candidate_id']),
                                state_before(parents.get(row['candidate_id']), row['night'])))
    return examples, dict(counts), hashlib.sha256(data).hexdigest(), table.epoch


def backtest(
    ledger: Path, root: Path, train_through: int = 6, calibrate_through: int = 10
) -> dict[str, Any]:
    if train_through >= calibrate_through:
        raise ValueError('Training cutoff must precede calibration cutoff')
    examples, counts, digest, epoch = history(ledger, root, train_through)
    train = [x for x in examples if x.night <= train_through]
    calibration = [x for x in examples if train_through < x.night <= calibrate_through]
    test = [x for x in examples if x.night > calibrate_through]
    report = dict(ledger_sha256=digest, difficulty_epoch=epoch, exclusions=counts,
                  train_through=train_through, calibrate_through=calibrate_through,
                  sizes={'train': len(train), 'calibration': len(calibration), 'test': len(test)},
                  target_epochs=sorted({x.epoch for x in examples}),
                  status='insufficient_history', prediction_provenance='anumana')
    if not train or not test:
        return report
    model = Imaginer(train, calibration)
    predictions = [model.imagine(x.recipe) for x in test]
    errors = [p.predicted_anchor - x.target for p, x in zip(predictions, test, strict=False)]
    baseline = [model.baseline - x.target for x in test]
    report.update(status='measured_backtest', mae=mean(abs(e) for e in errors),
                  rmse=math.sqrt(mean(e * e for e in errors)),
                  mean_baseline_mae=mean(abs(e) for e in baseline),
                  mean_baseline_rmse=math.sqrt(mean(e * e for e in baseline)),
                  interval_coverage=mean(p.lower <= x.target <= p.upper for p, x in zip(predictions, test, strict=False)),
                  interval_mean_width=mean(p.upper - p.lower for p in predictions),
                  calibration_nights=model.n_calibration,
                  beats_mean_rmse=sum(e*e for e in errors) < sum(e*e for e in baseline))
    richer = DynamicsImaginer(train, calibration)

    def metrics(values: list[float], subset: list[Example]) -> dict[str, float]:
        residuals = [p-x.target for p, x in zip(values, subset, strict=False)]
        return dict(rmse=math.sqrt(mean(e*e for e in residuals)),
                    mae=mean(abs(e) for e in residuals))

    report['dynamics'] = dict(
        **metrics([richer.prior(x) for x in test], test),
        coefficients=richer.coef, ridge=.01, rates=[1., .25, .0625],
        calibration_nights=richer.n_calibration,
        interval_coverage=mean(richer.imagine(x).lower <= x.target <= richer.imagine(x).upper for x in test),
        interval_mean_width=mean(richer.imagine(x).upper-richer.imagine(x).lower for x in test))
    report['parent_carry'] = metrics([x.parent_state[0] if x.parent_state else model.baseline for x in test], test)
    report['parent_state_available'] = {name: sum(x.parent_state is not None for x in split)
                                        for name, split in [('train', train), ('calibration', calibration), ('test', test)]}
    report['distinct_parents'] = {name: sorted({x.parent for x in split if x.parent})
                                 for name, split in [('train', train), ('test', test)]}
    report['per_test_night'] = {str(n): dict(
        recall=metrics([model._prior(x.recipe) for x in test if x.night == n], [x for x in test if x.night == n]),
        dynamics=metrics([richer.prior(x) for x in test if x.night == n], [x for x in test if x.night == n]))
        for n in sorted({x.night for x in test})}
    report['distinct_test_parent_states'] = len({x.parent_state for x in test})
    report['available_models'] = ['hopfield', 'parent_dynamics']
    report['default_recipe_only_model'] = 'hopfield'
    report['selection_note'] = 'Experimental comparison only; no automatic test-set model selection.'
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(backtest(args.ledger, args.root), indent=2, sort_keys=True))
