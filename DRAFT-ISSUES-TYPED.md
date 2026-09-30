# DRAFT issues, typed layer (for Goal to file at the 09:05 standup; NOT filed on GitHub)

Every claim is **Tag's claim, pending verification**. Branch `tag/night-typed-133`, code verified at `8b60b2d`,
base `main` @ `b9f0435`. Constructed inputs only. One issue per defect; each has a committed repro test.
Common repro prefix: `PYTHONPATH=src:pravrudhi_kernel/src python -m pytest`. To see a repro FAIL, copy the
named test file onto a checkout of `b9f0435` and run it there.

---
## 1. TypedHouseJudge drops the label-mass guard (#133)
- Severity: **high** (latent: `typed_layer` defaults off and no shipped config enables it)
- Body: `HouseJudge` refuses, with `JudgeOutputError`, a first-token distribution whose top-1 is not a label
  token or whose label mass is below `house_judge.label_mass_floor`. `TypedHouseJudge` (`typed_layer: true`)
  has no floor and neither check, so `{"Based": -0.2, " established": -1.6, " not": -6.0}` is returned as
  `established` p=0.988 where `HouseJudge` refuses. Turning the flag on silently removes a production safety guard.
- Repro: `... pytest tests/test_typed_label_mass_floor_133.py` (5 fail on `b9f0435`)
- Proposed fix: branch commit `f60e290`: `decoder.check_label_mass`, `TypedHouseJudge(label_mass_floor=...)`,
  `_build_house_judge` passes `float(hj_cfg["label_mass_floor"])` (bare subscript).

## 2. enforce_served_model is dropped on the typed-layer path
- Severity: **medium** (latent, same flag dependency)
- Body: `HouseJudge.from_config` honours `enforce_served_model`; the typed `VLLMDecoder` has no such option, so
  a typed slot scores replies from an unpinned model that `HouseJudge` would refuse with `ServedModelMismatch`.
- Repro: `... pytest tests/test_typed_night_defects.py -k f2`
- Proposed fix: thread `enforce_served_model` through `_client_complete`/`VLLMDecoder`/`SGLangDecoder` and the
  builder (off by default); branch `f60e290`.

## 3. NaN label logprob returns `established` with p = NaN (typed judge)
- Severity: **medium** (needs a malformed server reply)
- Body: NaN fails every comparison, so both `p < tau` and the label-mass floor pass it. Reply
  `{" established": NaN, " not": -1.0}` gives `established`, `p=nan`, with a fact id attached.
- Repro: `... pytest tests/test_typed_night_defects.py -k "f5 and not f5b"`
- Proposed fix: `check_label_mass` refuses a non-finite mass (`not mass >= floor` form); branch `f60e290`.

## 4. Same NaN fail-open in HouseJudge.p_established_from_top_logprobs (not fixed on branch)
- Severity: **medium**
- Body: as issue 3 but in `nyaya_judges.py` (out of `tag/night-typed-*` scope). `+inf` also returns `established p=1.0`.
- Repro: `... pytest tests/test_typed_night_defects.py -k f5b` (strict xfail: currently reproduces)
- Proposed fix: after computing `label_mass`: `if not math.isfinite(label_mass) or not label_mass >= label_mass_floor: raise JudgeOutputError(...)`.

## 5. Typed builder defaults timeout_s / max_tokens / top_logprobs that HouseJudge.from_config requires
- Severity: **low**
- Body: `_build_house_judge(typed=True)` uses `.get(key, 60/30/20)`; the house path raises `KeyError`. Repo rule: missing input raises.
- Repro: `... pytest tests/test_typed_night_defects.py -k f3`
- Proposed fix: bare subscripts (branch `6314f36`).

## 6. Field accepts empty, blank or cross-option-shared token variants
- Severity: **low**
- Body: `bool_field("s", true_tokens=(), ...)` constructs, and `score_decision` then always reports that option as
  missing; a token under two options is counted for both (0.5/0.5). `""` accepted as a token.
- Repro: `... pytest tests/test_typed_night_defects.py -k f4`
- Proposed fix: validation in `Field.__post_init__` (branch `6314f36`, line-length fix `8b60b2d`).

## 7. NaN or out-of-range label_mass_floor silently disables the guard
- Severity: **low**
- Body: `mass < nan` is always False; `NYAYA_SECOND_JUDGE_LABEL_MASS_FLOOR=nan` parses via `float()`. Typed judge now
  refuses a floor outside [0, 1]; `HouseJudge.__init__` has the same hole (no repro test for it, so only the typed side is graded).
- Repro: `... pytest tests/test_typed_night_defects.py -k f6`
- Proposed fix: `ValueError` unless `0 <= floor <= 1` (branch `f60e290`); same check for `HouseJudge.__init__` and the env override.
