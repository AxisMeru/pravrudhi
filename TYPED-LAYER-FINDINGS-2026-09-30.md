# Typed layer night findings, 2026-09-30

Every claim below is **Tag's claim, pending verification** until a second reader re-runs the repro commands.
All test inputs are **constructed** (hand-written logprob dictionaries, toy strings, fake transports / a fake
`ChatClient` that live only inside the tests). No real material, no network, no kernel edit, no paid API.

- Branch: `tag/night-typed-133` (origin). Base: `main` @ `b9f0435` (shallow clone).
- Code + tests verified at **`8b60b2d`**; this findings file and `DRAFT-ISSUES-TYPED.md` are the commit after it (docs only).
- No pre-existing `tag/*` branch covers the typed layer. `git ls-remote --heads origin 'tag/*'` at start listed 14
  branches (`tag/allowlist-inversion`, `tag/claude-axismeru/*`, `tag/denial-missing-answer-regression`,
  `tag/element-status-tests`, `tag/fail-open-lint`, `tag/legalbench-slice-fallback`); none touches
  `src/pravrudhi/application/typed/`. `tag/night-typed-133` did not exist, so no `-b` suffix was needed.
- Rule applied (operator, binding): a severity grade stands only with a committed, runnable test. Anything
  without one is listed at the end as an **unverified observation, ungraded**.
- Environment: Python 3.13 venv, Hypothesis 6.168.3 (already a dev dependency in `pyproject.toml`), pytest.
  Run with `pip install -e . -e pravrudhi_kernel` (read-only use of the kernel; nothing in it was edited).

## Headline

**#133 reproduces.** `typed_layer: true` swaps `HouseJudge` for `TypedHouseJudge`, and `TypedHouseJudge` has no
`label_mass_floor`: it neither checks that the top-1 first token is a label token nor that the label tokens
hold at least `label_mass_floor` of the probability mass. A prose completion ("Based on the facts...") with
`" established"` at logprob -1.6 and `" not"` at -6.0 scores p = 0.988 and is returned as `established`;
`HouseJudge` refuses the same reply with `JudgeOutputError`. Latent, not live: `typed_layer` defaults to
`False` and no shipped config (`configs/`, `deploy/`, `docker/`) turns it on, so no blocker.

Severity counts (Tag's grading): **blocker 0, high 1, medium 3, low 3** (7 defects, F1 to F6 plus F5b). For filing, F5b is handled as H-01 (harness
workstream), so `DRAFT-ISSUES-TYPED.md` carries 6 issues: high 1, medium 2, low 3.

## How #133 was reproduced

`#133` is not described anywhere in the repo text (no issue text, changelog or comment cites it by number); the
meaning was taken from the operator brief ("known #133 label_mass_floor drop") and the code: `label_mass_floor`
is threaded through `HouseJudge` (`src/pravrudhi/application/nyaya_judges.py:205-262, 307-316, 419, 461`),
through the env-built second judge (`nyaya_agent.py:265, 311`), and is absent from
`src/pravrudhi/application/typed/house_judge.py` and from the typed branch of
`_build_house_judge` (`nyaya_agent.py:846-870` at `b9f0435`). That identification is Tag's inference.

Repro command (fails on `b9f0435`, passes on this branch):

    PYTHONPATH=src:pravrudhi_kernel/src python -m pytest tests/test_typed_label_mass_floor_133.py -q

Committed first as `7e75f73` with the unfixed source: **5 failed** (both refusal cases, constructor kwarg,
builder threading, builder requiring the key). Hypothesis independently found the minimal counterexample when
pointed at parity (`tests/test_typed_properties_night.py::test_p3_house_and_typed_judges_agree`, run against
`b9f0435` source): `top={' established': -1.0}` gives `house=refused` (label mass 0.368 < 0.5) versus
`typed=established p=0.5 lower_bound`.

## Defects

Repro for all: `PYTHONPATH=src:pravrudhi_kernel/src python -m pytest tests/test_typed_night_defects.py tests/test_typed_label_mass_floor_133.py -q`
(run against `b9f0435` source with these two test files copied in: **18 failed, 4 passed, 2 xfailed**; on this
branch: all pass, the 2 xfails are F5b and are expected).

### F1 (high) #133: TypedHouseJudge drops the label-mass guard
- Tests: `tests/test_typed_label_mass_floor_133.py` (5 tests).
- Why high, not blocker: removes a production-safety guard, but only reachable with `typed_layer: true`
  (default off, in no shipped config). A prose reply can be returned as `established` with a fact id attached.
- Proposed fix (on the branch, `f60e290`): new `decoder.check_label_mass(top, field, *, label_mass_floor)`
  (keyword-only, required, raises `DecodeError`; refuses prose top-1, refuses mass below floor, refuses a NaN
  mass); `TypedHouseJudge.__init__(..., label_mass_floor=LABEL_MASS_FLOOR)` calls it after `score_decision` and
  re-raises as `JudgeOutputError`; `_build_house_judge` passes `float(hj_cfg["label_mass_floor"])` as a bare
  subscript, same as `HouseJudge.from_config`, so the second judge (which inherits the key) is covered.
  `score_decision`'s signature is unchanged (`scripts/t2_*.py` and `scripts/typed_layer_parity*.py` call it directly).

### F2 (medium): `enforce_served_model` silently dropped on the typed path
- Test: `tests/test_typed_night_defects.py::test_f2_*` (3 tests; fake `ChatClient`).
- `HouseJudge.from_config` passes `enforce_served_model` (set by `NYAYA_HOUSE_JUDGE_ENFORCE_SERVED_MODEL`);
  `VLLMDecoder`/`_client_complete` have no such option, so a typed slot scores an answer from an unpinned model
  that `HouseJudge` would refuse (`ServedModelMismatch`). Latent for the same reason as F1.
- Proposed fix (on the branch): `enforce_served_model` param on `_client_complete`, `VLLMDecoder`,
  `SGLangDecoder`; primary-only pin, same check and same message as `HouseJudge`; builder reads
  `hj_cfg.get("enforce_served_model", False)` (off by default, so nothing changes until a deployment names it).

### F3 (low): typed builder silently defaults keys `HouseJudge.from_config` requires
- Test: `test_f3_*` (3 parametrized cases: `timeout_s`, `max_tokens`, `top_logprobs`).
- Typed branch used `.get("timeout_s", 60)`, `.get("max_tokens", 30)`, `.get("top_logprobs", 20)`; the house
  path raises `KeyError`. Repo rule: a missing input raises, it never defaults. Fix on branch: bare subscripts.

### F4 (low): `Field` accepts token variants that make `score_decision` meaningless
- Tests: `test_f4_*` (4 tests; the fourth checks the real established/not field still constructs).
- An option with `()` variants is always "missing" (returns 0.5/0.5 bound), a token shared by two options is
  counted for both (0.5/0.5 split the model never made), and `""` is accepted as a token. Constructed schemas
  only today (`lean_schema` uses fixed tokens), hence low. Fix on branch: validation in `Field.__post_init__`.

### F5 (medium): a NaN label logprob yields `established` with p = NaN (typed judge)
- Tests: `test_f5_*` (2 cases). Probe output on `b9f0435`: `{' established': nan, ' not': -1.0}` gives
  `established p=nan`. NaN compares False with everything, so `p < tau` and `mass < floor` both "pass".
  `CompletionResult` accepts NaN (pydantic float) and `json.loads` accepts the literal `NaN`. Needs a malformed
  server reply, hence medium. Fix on branch: `check_label_mass` refuses a non-finite or NaN mass.

### F5b (medium): the same NaN fail-open exists in `HouseJudge` itself (NOT fixed here)
- **Also found by the harness workstream as H-01, which owns the fix.** Removed from `DRAFT-ISSUES-TYPED.md` so it is
  not filed twice at the 09:05 standup. The F5b test and the one-line proposed fix below stay here as independent
  corroboration only; H-01 is the issue of record.
- Test: `test_f5b_house_judge_refuses_a_nan_label_logprob`, `xfail(strict=True)` (documents the defect; turns
  into a failure the moment it is fixed, so it cannot rot).
- `nyaya_judges.py` is outside `tag/night-typed-*` scope, so no edit. Proposed one-liner for the harness owner,
  in `p_established_from_top_logprobs`, after computing `label_mass`:
  `if not math.isfinite(label_mass) or not label_mass >= label_mass_floor: raise JudgeOutputError(...)`
  (note the inverted comparison: it is what makes NaN refuse). Probe also shows `+inf` est returns `established p=1.0`
  on `HouseJudge`; the same line refuses it (mass = inf).

### F6 (low): a NaN or out-of-range `label_mass_floor` silently disables the guard
- Test: `test_f6_*` (nan, -0.1, 1.5, inf). `mass < nan` is always False; `float("nan")` is accepted by
  `_second_override("NYAYA_SECOND_JUDGE_LABEL_MASS_FLOOR", ..., float)` (`nyaya_agent.py:265`).
- Fix on branch: `TypedHouseJudge.__init__` raises `ValueError` unless `0 <= floor <= 1`.
  The same hole exists in `HouseJudge.__init__` (not fixed here, not separately tested: same out-of-scope file as F5b).

## Property-test results (Hypothesis 6.168.3, `derandomize=True`, 400 examples each)

File: `tests/test_typed_properties_night.py` @ `8b60b2d`. Result: **6 passed** on this branch
(`pytest tests/test_typed_properties_night.py`, about 6 s). Run against `b9f0435` source: P1, P2, P5, P6 pass;
**P3 fails** (the #133 counterexample above); P4 cannot import (`check_label_mass` does not exist there).

| Property | What it checks | Result on branch |
|---|---|---|
| P1 | `score_decision`: scores in [0,1], sum 1, dict-order invariant, `missing` exact, refuses iff no label token | pass |
| P2 | G-28 bound soundness: truncating a real full distribution to top-k, `lower_bound <= exact` and `upper_bound >= exact` | pass (so the G-28 fix holds on the typed side) |
| P3 | `HouseJudge` and `TypedHouseJudge` agree on every generated first-token distribution, including refusals | pass (fails on `b9f0435`) |
| P4 | label-mass guard monotone in its floor; never accepts a prose top-1 even at floor 0 | pass |
| P5 | lowering `tau` never turns `established` into `not_established` | pass |
| P6 | `validate_id_ref` is exact membership over unicode candidates, never a nearest match | pass |

Whole-suite comparison, same command on both (`python -m pytest tests -q -p no:cacheprovider`, Python 3.13 venv,
base run from a separate `git worktree` of `b9f0435` with `PYTHONPATH` pointing at that worktree's `src`; confirmed
the worktree's `pravrudhi.__file__` was loaded):

| Tree | Commit | Result |
|---|---|---|
| base (`main`) | `b9f0435` | **3758 passed, 147 skipped, 4 xfailed, 0 failed** (142 s) |
| this branch | `8b60b2d` (code and tests; later commits are docs only) | **3786 passed, 147 skipped, 6 xfailed, 0 failed** (164 s) |

Difference: +28 passed = the 28 non-xfail tests this branch adds (5 in `test_typed_label_mass_floor_133.py`,
17 in `test_typed_night_defects.py`, 6 in `test_typed_properties_night.py`); +2 xfailed = F5b's two parametrized
cases; skipped unchanged at 147. The base has **no pre-existing failures**, so none can be blamed on or hidden by
this branch. "No regression" is therefore measured, not claimed. (The 147 skips and 4 xfails are identical
pre-existing ones; not investigated.) `ruff check` on the changed files: clean.

## Files on the branch

- Source (proposed fixes): `src/pravrudhi/application/typed/decoder.py`, `typed/house_judge.py`,
  `typed/schema.py`, `src/pravrudhi/application/nyaya_agent.py` (builder only, 2 hunks).
- Tests: `tests/test_typed_label_mass_floor_133.py`, `tests/test_typed_night_defects.py`,
  `tests/test_typed_properties_night.py`.
- Not touched: `pravrudhi_kernel/`, `nyaya_judges.py`, every config, `main`.

## Unverified observations (ungraded: no committed repro test, per the operator rule)

- A `-inf` logprob on one label token with the other finite: `score_decision` and `HouseJudge` treat it as
  "missing" and use `bound = min(top.values())` = `-inf`, giving `p = 1.0` tagged `lower_bound`. If `-inf`
  means a real zero probability, `p = 1.0` is exact and correct; if it is a server floor, it reintroduces the
  old clamp-to-1.0. Not decidable from the repo; needs a statement from the serving team on what vLLM/SGLang emit.
- A positive logprob (`0.5`) is accepted by both judges (probability above 1). Malformed server reply only.
- `contract_schema(DescribedContract("c", [], []))` returns an empty `Schema` (vacuously "all elements
  established" for any consumer). Unreachable today: `parse_describe_output` raises on no elements, and
  `contract_schema` has no caller in `src/`.
- `typed/validators.validate_id_ref(field, None)` returns `None` ("no reference made"); by design, but a
  caller that treats `None` as valid evidence would be fail-open. No such caller found in `src/`.
