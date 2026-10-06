"""Night-loop guard for #303: a candidate config may not LOOSEN the safety operating point.

Lead-2, 2026-10-02; DEFAULT-DENY since 6 Oct (R2 on #175). A candidate is a nested dict of overrides on the baseline
agent config (configs/nyaya_agent.yaml shape), optionally with the NYAYA_* environment it would run under. The guard
is an ALLOWLIST: a path the loop may change must be listed below with its rule; every other path, every unknown key,
every non-dict candidate and every non-finite number is a violation. Allowed paths are of three kinds:

* TIGHTEN_ONLY numeric knobs: a finite real number, at least its floor and at least the baseline's value (a stricter
  or equal setting).
* the refer band (must contain the baseline band), validated_contracts (a subset of the baseline's strings), and
  second_judge (must stay a non-empty dict when the baseline has one).
* FREE knobs: prompt and wiring settings with no safety effect, type-checked.

The night runner must call `guarded_candidate_config` (the only sanctioned merge): it raises `LooseningRefused` before
any evaluation. `reject_loosening` and `reject_loosening_env` return the violation lists. Loosening a safety threshold
is a human prereg act with its own sealed test, never a loop action."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from typing import Any

PRIMARY_TAU_FLOOR = 0.74
SECOND_TAU_FLOOR = 0.97
REFER_DELTA_FLOOR = 0.125
LABEL_MASS_FLOOR_FLOOR = 0.5

# path -> absolute floor (the baseline value is also a floor; the larger wins)
TIGHTEN_ONLY: dict[tuple[str, ...], float] = {
    ("tau",): PRIMARY_TAU_FLOOR,
    ("house_judge", "tau"): PRIMARY_TAU_FLOOR,
    ("second_judge", "tau"): SECOND_TAU_FLOOR,
    ("second_judge", "refer_logit_delta"): REFER_DELTA_FLOOR,
    ("house_judge", "label_mass_floor"): LABEL_MASS_FLOOR_FLOOR,
    ("second_judge", "label_mass_floor"): LABEL_MASS_FLOOR_FLOOR,
    ("second_judge_positive_control", "parity_floor"): 0.0,
    ("second_judge_positive_control", "ne_discrimination_min"): 0.0,
}
FREE: dict[tuple[str, ...], type] = {
    ("judge_prompt", "standard_line"): bool,
    ("house_judge", "prompt_variant"): str,
    ("house_judge", "max_concurrency"): int,
}
BAND = ("refer_band",)
CONTRACTS = ("validated_contracts",)
SECOND = ("second_judge",)

# NYAYA_* environment variables the loop may set, mapped to the config path whose rule then applies.
# Every other NYAYA_* variable is refused.
ENV_PATHS: dict[str, tuple[str, ...]] = {
    "NYAYA_HOUSE_JUDGE_TAU": ("tau",),
    "NYAYA_SECOND_JUDGE_TAU": ("second_judge", "tau"),
    "NYAYA_SECOND_JUDGE_REFER_LOGIT_DELTA": ("second_judge", "refer_logit_delta"),
    "NYAYA_SECOND_JUDGE_LABEL_MASS_FLOOR": ("second_judge", "label_mass_floor"),
    "NYAYA_JUDGE_MAX_CONCURRENCY": ("house_judge", "max_concurrency"),
}


class LooseningRefused(ValueError):
    def __init__(self, violations: list[str]) -> None:
        super().__init__("candidate refused: " + "; ".join(violations))
        self.violations = list(violations)


def _get(d: Any, path: tuple[str, ...]) -> tuple[Any, bool]:
    for k in path:
        if not isinstance(d, Mapping) or k not in d:
            return None, False
        d = d[k]
    return d, True


def _is_real(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _leaf_paths(d: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    """Every leaf path of a nested mapping; an empty mapping is itself a leaf (so {} cannot hide)."""
    out: list[tuple[tuple[str, ...], Any]] = []
    for k, v in d.items():
        p = prefix + (str(k),)
        if isinstance(v, Mapping) and v:
            out += _leaf_paths(v, p)
        else:
            out.append((p, v))
    return out


def _baseline_floor(baseline: Any, path: tuple[str, ...], floor: float) -> float:
    b, present = _get(baseline, path)
    if path == ("second_judge", "label_mass_floor") and not present:
        b, present = _get(baseline, ("house_judge", "label_mass_floor"))
    return max(floor, float(b)) if present and _is_real(b) else floor


def reject_loosening(candidate: Any, baseline: Any) -> list[str]:
    bad: list[str] = []
    if not isinstance(candidate, Mapping):
        return [f"candidate: {type(candidate).__name__} is not a dict (default-deny)"]
    if not isinstance(baseline, Mapping):
        return ["baseline: not a dict (cannot judge a candidate against it)"]

    handled: set[tuple[str, ...]] = set()
    for path, floor in TIGHTEN_ONLY.items():
        v, present = _get(candidate, path)
        if not present:
            continue
        handled.add(path)
        label = ".".join(path)
        if not _is_real(v):
            bad.append(f"{label}: {v!r} is not a finite number")
            continue
        need = _baseline_floor(baseline, path, floor)
        if v < need:
            bad.append(f"{label}: {v!r} below {need} (floor or baseline, whichever is higher)")

    band, present = _get(candidate, BAND)
    if present:
        handled.add(BAND)
        base = _get(baseline, BAND)[0]
        ok = (
            isinstance(band, (list, tuple))
            and len(band) == 2
            and all(_is_real(x) for x in band)
            and band[0] < band[1]
            and band[0] >= 0
            and band[1] <= 1
        )
        if not ok:
            bad.append(f"refer_band: {band!r} is malformed (need two finite numbers low < high within [0, 1])")
        elif isinstance(base, (list, tuple)) and len(base) == 2 and (band[0] > base[0] or band[1] < base[1]):
            bad.append(f"refer_band: {list(band)!r} narrows baseline {list(base)!r}")
        elif not (isinstance(base, (list, tuple)) and len(base) == 2):
            bad.append("refer_band: the baseline has no band to compare against")

    vc, present = _get(candidate, CONTRACTS)
    if present:
        handled.add(CONTRACTS)
        base = _get(baseline, CONTRACTS)[0]
        if not (
            isinstance(vc, (list, tuple))
            and all(isinstance(x, str) for x in vc)
            and isinstance(base, (list, tuple))
            and all(isinstance(x, str) for x in base)
        ):
            bad.append("validated_contracts: must be a list of strings (malformed or unhashable entries refused)")
        elif not set(vc) <= set(base):
            bad.append("validated_contracts: widened beyond the baseline allowlist")

    if _get(baseline, SECOND)[1] and _get(baseline, SECOND)[0]:
        sj, present = _get(candidate, SECOND)
        if present and (not isinstance(sj, Mapping) or not sj):
            bad.append("second_judge: disabled or removed")
            handled.add(SECOND)

    for path, typ in FREE.items():
        v, present = _get(candidate, path)
        if present:
            handled.add(path)
            ok = isinstance(v, typ) and not (typ is int and isinstance(v, bool))
            if ok and typ is int:
                ok = isinstance(v, int) and v >= 1
            if not ok:
                bad.append(f"{'.'.join(path)}: {v!r} is not a valid {typ.__name__} for this wiring knob")

    # DEFAULT-DENY: every other leaf is a violation, named so a human can allowlist it deliberately (or not)
    for path, _v in _leaf_paths(candidate):
        if any(path[: len(h)] == h for h in handled) or path in handled:
            continue
        if path[:1] == SECOND and SECOND in handled:
            continue
        if path[:1] == BAND or path[:1] == CONTRACTS:
            continue
        bad.append(
            f"{'.'.join(path)}: not in the loop's allowlist (default-deny)"
            + (" [quote-check or other safety-defining parameter]" if "quote" in ".".join(path).lower() else "")
        )
    return bad


def reject_loosening_env(env: Mapping[str, str] | None, baseline: Any) -> list[str]:
    """The NYAYA_* variables a candidate would run under: only ENV_PATHS may appear.

    Each value is parsed and held to its config path's rule."""
    bad: list[str] = []
    for k, raw in (env or {}).items():
        if not str(k).startswith("NYAYA_"):
            continue
        path = ENV_PATHS.get(k)
        if path is None:
            bad.append(f"{k}: environment override not in the loop's allowlist (default-deny)")
            continue
        try:
            val: Any = (
                raw
                if FREE.get(path) is str
                else (
                    raw.strip().lower() in ("1", "true", "yes", "on")
                    if FREE.get(path) is bool
                    else (int(raw) if FREE.get(path) is int else float(raw))
                )
            )
        except (TypeError, ValueError):
            bad.append(f"{k}: {raw!r} is not parseable for {'.'.join(path)}")
            continue
        node: dict[str, Any] = {}
        cur = node
        for p in path[:-1]:
            cur[p] = {}
            cur = cur[p]
        cur[path[-1]] = val
        bad += [f"env {k}: {m}" for m in reject_loosening(node, baseline)]
    return bad


def guarded_candidate_config(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], env: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The ONLY sanctioned way to apply a candidate.

    Refuses (LooseningRefused) before anything is merged or evaluated, else returns the baseline deep-merged with
    the candidate."""
    violations = reject_loosening(candidate, baseline) + reject_loosening_env(env, baseline)
    if violations:
        raise LooseningRefused(violations)
    merged = copy.deepcopy(dict(baseline))

    def merge(dst: dict[str, Any], src: Mapping[str, Any]) -> None:
        for k, v in src.items():
            if isinstance(v, Mapping) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = copy.deepcopy(v)

    merge(merged, candidate)
    return merged
