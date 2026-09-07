# How hard should Pravrudhi keep working?

Choose a level for each external artifact: a web app, model, fine-tune, or distillation. Its location and execution remain outside the engine. The ordered options in `src/pravrudhi/assets/configs/band.yaml` are the policy source.

| Choice | May act without asking | Must ask first | Lifetime ceiling (USD) | Run frequency |
|---|---|---|---|---|
| One-time build | Produce the initial artifact | None; further work requires changing the choice | $10 | One run, then stop |
| Critical updates only | Build; repair reported breakage; patch reported unsafe dependencies | None; other work is outside this level | $50 | At most once per day |
| Self-healing | Critical actions; check against a recorded baseline and repair a reported regression | None; other work is outside this level | $150 | At most once per hour |
| Continuous improvement | Self-healing actions; propose and test unsolicited improvements | Deploy an improvement | $500 | At most once per 15 minutes |

These are configurable limits, not estimates or subscriptions. Unlisted actions are denied, even with action approval. Choosing a higher level is an explicit user choice. An artifact can have a lower `spend_limit_cents`; requesting a higher limit than its selected level raises `ValueError`.

## Calling the permission module

Load `load_config().levels` to present the named options and their structured permissions, ceiling, and cadence. Persist the user's selected ID with the external artifact's stable ID. Pass that selection as `Artifact` to `decide`, along with an action from the config, an integer cost bound in USD cents, the current `kshudha.Appetite`, the current `svasthya.Health`, and a timezone-aware `now`. The result reports `allowed`, `ask_first`, or `denied`, a reason, the effective limit, remaining budget, and cadence; `to_dict()` exposes it for a UI or audit record.

`kshudha` still decides whether the engine wants to act. A resting appetite cannot be activated by this band. `svasthya.can_dispatch_new_work` still decides whether engine health permits work. This module never measures health, selects a drive, calls an external artifact, schedules, repairs, deploys, or persists anything.

The caller binds the proposed action to the selected appetite and artifact. Appetite descriptions alone cannot prove an artifact is broken. Supply artifact-scoped evidence references from the external observer: a breakage report, unsafe dependency report, or recorded baseline and regression comparison. Self-healing permits baseline checks, but repair requires both a baseline record and a report showing regression against it. The band checks that the references are present; the observer is responsible for their validity and comparison, including metric direction and tolerance. Engine health is not evidence of artifact regression.

The dispatcher must keep durable, trusted usage per artifact across restarts and level changes. `spent_cents` plus `reserved_cents` plus the proposed cost must fit the lifetime ceiling. There is no budget reset. Cost must be an enforceable upper bound, including tests and failed attempts. A run is one admitted action, including monitoring and proposals. Count it when started, even if it fails. One-time build therefore allows one bounded build job, with any internal steps inside that job's budget, and no subsequent runs.

An `allowed` report is not a reservation. The caller must serialize permission checks with budget reservations and run-start recording before execution, otherwise concurrent calls could overspend. Approval is specific to the proposed action and must come from the user; `approved=True` cannot override health, evidence, spend, cadence, or exhausted runs. The caller must enforce the approved cost bound while executing. Fresh health, appetite, evidence, and usage must be supplied on every check.

Validate with `uv run pytest tests/test_band.py -q`.
