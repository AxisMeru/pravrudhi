Imagination experiment, 2026-09-07

This is a queue-only predictor, not evidence of candidate quality. The module has no evaluator, ledger writer, gate, promotion, or publication integration. Its frozen output type always carries `provenance=anumana` and `usage=queue_only`; it deliberately does not implement the kernel's observed measurement schema. No existing engine call site was changed under the permitted file scope. Call `Imaginer(training, calibration).imagine(recipe)` or `order_queue(recipes)` to obtain advice. Training examples can be extracted with `history(ledger, root, train_through)`; split its returned examples by night before constructing the model. Only pass measured examples, never predictions, to the fitting interface.

The adaptation is concrete but smaller than PWM. I read its README, `pwm/world_model/trika.py`, `rssm.py`, `pwm/active_inference/efe_actor.py`, `efe_utils.py`, `pwm/memory/citta_store.py`, and results `phase_2_gate.json`, `h5_live_ablation.json`, and `ablation_a6_1level_wm.json`. PWM's `imagine_step` uses a prior without an observation encoder. Here the analogous step recalls an outcome distribution from recipe keys without any candidate measurement. PWM's HopfieldBank computes softmax(beta * query @ keys.T); this module implements that retrieval exactly over normalized execution-field tokens, with anchored outcomes as values and fixed beta=4. This is an associative conditional outcome model, not a trained Trika RSSM. It does not claim recurrent dynamics, a learned EFE actor, sleep consolidation, or intrinsic reward. Queue ordering uses expected pragmatic value alone. PWM's actual actor loss currently omits its EFE term; its live ablation reports g=-0.4733, and describes the earlier 2.14x internal result as simulated. Those findings do not establish this predictor's utility.

Recipe keys exclude rationale, candidate IDs, claimed deltas, and other proposal prose. Numeric execution settings are categorical tokens; extrapolation to unseen numeric settings is therefore weak. There is no hyperparameter search. This model also lacks parent-state conditioning, so it cannot claim reliable generalization across new parents. Repeated evaluations remain correlated, and selection-biased history cannot establish performance for unevaluated recipes.

Protocol: train on nights 0–6, calibrate on 7–10, test on 11–16. Only kernel `pratyaksha` observations supply outcomes; only candidate-arm observations with recipes become supervised examples. Incumbent measurements contribute item difficulty. Training labels use anchor.py's leave-one-out definition and a difficulty table built solely from training nights. A first attempt to retain that table for every later label yielded zero complete later anchors because the pools change. A diagnostic 0–12/13/14–16 split also yielded zero. Neither attempt produced error metrics or led to model tuning.

The reported protocol retains the original split, using a separate table for each later night: training observations plus that night's measurements, with the scored observation left out. Later measurements construct labels only; they never update training memory or training targets. This makes the held-out target measurable, but entails difficulty-epoch drift and often sparse peer baselines. It is not a test against a single immutable difficulty scale. The JSON records the training epoch, all label epochs, and the ledger byte hash. Missing files, incomplete item coverage, and unavailable anchors are counted rather than imputed. No raw pass rate or incumbent delta substitutes for an anchor.

Result: 112 training observations, 46 calibration observations across four nights, 66 test observations. Test RMSE is 0.055903 versus 0.056820 for the frozen training mean (about 1.6% lower); MAE is 0.044272 versus 0.043932 (slightly worse). This is mixed, marginal performance, not convincing evidence of useful prediction. No statistical significance or queue improvement is claimed. The benchmark aggregates observations, not independent candidates.

Uncertainty uses the finite-sample conformal rank ceil((m+1)*0.9) over per-night maximum absolute calibration residuals. With m=4 the requested rank exceeds the available nights, so the honest interval is the full anchored-score support [-1,1]. Coverage is 100%, mean width 2.0: valid as a bound but useless for discrimination. Even with more calibration nights, temporal exchangeability is unproven, so nominal 90% coverage would require empirical checking. This experiment has not demonstrated informative calibrated uncertainty.

Read-scope note: the worktree lacks `research/ledger.jsonl`, so the explicitly named ledger was read at `/home/ss/projects/pravrudhi/research/ledger.jsonl`. The additional unnamed files read were the exact `per_item_scores.jsonl` paths referenced by those observations, under the main checkout's `.pravrudhi/kernel/jobs/`; these are necessary to compute item-anchored labels. They were read only. No kernel source, gates, or unrelated repository directories were surveyed. PWM's root and its named results directory were listed to locate the requested sources.

Reproduce from this worktree:

```sh
PYTHONPATH=src python -m pravrudhi.application.imagine /home/ss/projects/pravrudhi/research/ledger.jsonl --root /home/ss/projects/pravrudhi
uv run pytest tests/test_imagine.py -q
```

In this sandbox the default uv cache is read-only and downloads are unavailable. Validation used the existing environment with `UV_CACHE_DIR=/tmp/imagine-uv-cache UV_PROJECT_ENVIRONMENT=/home/ss/projects/pravrudhi/.venv UV_NO_SYNC=1 PYTHONPATH=src` preceding the exact pytest command: 4 passed. An automatically created worktree `.venv` from the unsuccessful default invocation was removed.

Follow-up: parent-conditioned dynamics, 2026-09-07

The original report above and `imagination-backtest.json` remain the historical
baseline. The reproducible comparison is now `imagination-dynamics-backtest.json`.
All figures in this follow-up refer to ledger SHA256
`4e9e4b20125dfe40015115951b474031c89a5ab9368008171ed39f542b35f351` and training
difficulty epoch
`f32174cb137612ee60bd8374a607ba3870b8a2bc9fb09b11c7280591d2013619`;
the JSON records every later target epoch. These are offline prediction-error
measurements, never published candidate-quality estimates.

| Predictor | Test RMSE | Test MAE |
|---|---:|---:|
| Frozen training mean | 0.056820 | 0.043932 |
| Original Hopfield recall (reproduced exactly) | 0.055903 | 0.044272 |
| Latest measured parent anchor, frozen at training cutoff | 0.053400 | 0.041355 |
| Three-timescale parent dynamics plus recipe recall | 0.048213 | 0.037045 |

The fixed richer model beats the mean and the original predictor on both error
measures. Per-night RMSE and MAE also improve over recall on every test night;
see the JSON rather than treating repeated evaluations as independent evidence.
The split, exclusions, labels, and sample sizes are unchanged. Calibration and
test outcomes never fit coefficients or update parent states. There was one fixed
configuration and no tuning against these results.

The adaptation is deliberately small: aggregate each parent's measured training
anchors by night, then carry three scalar states with update rates 1, 1/4, 1/16.
Initialise all three from the first available measured night; exclude the child's
own night and all measurements beyond training. Include baseline/incumbent
measurements even when their recipes are missing. Resolve the nearest parent
using archive.py's parent map, never recipe prose. These posterior summaries
condition a linear Gaussian child transition: recipe recall plus a fitted
intercept and three state coefficients, ridge penalty 0.01 on all coefficients.
The training difficulty table is shared with the original protocol, including
its use of all training observations; this is an offline frozen-training
experiment, not a causal online replay within training. Missing parent state
falls back exactly to recipe recall. The model never consumes the child's target
at inference, and its state is unchanged by imagination.

This adapts RSSM observation/prior separation and multiple timescales, not PWM's
categorical encoder, GRU/Mamba backbone, learned three-level hierarchy or neural
training objective. The Gaussian EFE queue score is expected negative log
preference under N(1,1), minus Bayesian linear information gain
`0.5*log(1 + epistemic_variance/noise_variance)`. Noise comes from training
residuals; parameter variance uses the ridge precision inverse. Constants shared
by every action are omitted. This uncertainty is a model assumption, not a
validated epistemic estimate; policy entropy from PWM's actor is not silently
relabeled as information gain. No queue-performance claim follows from outcome
RMSE. PWM's live ablation is negative and explicitly calls its earlier internal
scores simulated; the current README describes that gain as internal. None of
those claims are transferred here.

The limitation is decisive for interpretation: training has two parent IDs, test
has only one, and every held-out parent state is identical because it is frozen.
Thus the learned state term is a constant correction on this test set. This
experiment supports an ancestry-conditioned calibration improvement; it does
not establish that three timescales outperform one, that recurrence is necessary,
or that a neural Trika RSSM would help. It cannot establish impossibility of
better prediction either. Deeper, varied measured lineages are needed to test
those stronger claims. No further configurations were tried after this result.

The richer implementation is available explicitly as
`DynamicsImaginer(training, calibration).imagine(example)` and
`order_queue(examples)`. The existing recipe-only `Imaginer` interface remains
compatible. `Example.parent_state` must contain measured summaries from `history`,
never imagined rollouts passed back as observations. All prediction objects still
carry frozen `anumana` / `queue_only` fields. There is no gate, promotion, ledger
writer, evaluator or publication integration. The conformal procedure is unchanged:
four calibration nights still produce full-support intervals of width 2 and
coverage 1. Neither model has demonstrated useful calibrated intervals.

Validation: six tests pass with
`UV_CACHE_DIR=/tmp/imagine-uv-cache UV_PROJECT_ENVIRONMENT=/home/ss/projects/pravrudhi/.venv UV_NO_SYNC=1 PYTHONPATH=src uv run pytest tests/test_imagine.py -q`.
The unqualified command fails before pytest because the default uv cache is
read-only. Tests cover state conditioning, missing-state fallback, inference
independence from child outcomes, frozen held-out parent state, provenance and
existing protocol/calibration behavior.

Additional read scope for this follow-up: the exact ledger and its explicitly
referenced per-item files in the main checkout were necessary to reproduce the
held-out labels (read only). PWM's README and the exact
`benchmarks/results/h5_live_ablation.json` and
`benchmarks/results/phase_5_gate_step0500000.json` were read to check the requested
claims. No directory survey was performed. All writes are confined to the allowed
worktree paths.
