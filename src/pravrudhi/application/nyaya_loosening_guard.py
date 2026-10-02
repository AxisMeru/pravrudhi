"""Night-loop guard for #303: a candidate config may not LOOSEN the safety operating point (Lead-2, 2026-10-02).

A candidate is a nested dict of overrides on the baseline agent config (configs/nyaya_agent.yaml shape). `reject_loosening`
returns the list of violations; the runner must refuse a candidate with any, before evaluation. Stricter changes, prompts and
wiring flags pass. Loosening a safety threshold is a human prereg act with its own sealed test, never a loop action."""
from __future__ import annotations

PRIMARY_TAU_FLOOR = 0.74
SECOND_TAU_FLOOR = 0.97
REFER_DELTA_FLOOR = 0.125


def _get(d, *path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None, False
        d = d[k]
    return d, True


def reject_loosening(candidate, baseline):
    bad = []

    def num_floor(path, floor, label):
        v, present = _get(candidate, *path)
        if present and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < floor):
            bad.append(f"{label}: {v!r} below floor {floor}")

    num_floor(("tau",), PRIMARY_TAU_FLOOR, "primary tau")
    num_floor(("house_judge", "tau"), PRIMARY_TAU_FLOOR, "house_judge.tau")
    num_floor(("second_judge", "tau"), SECOND_TAU_FLOOR, "second_judge.tau")
    num_floor(("second_judge", "refer_logit_delta"), REFER_DELTA_FLOOR, "second_judge.refer_logit_delta")
    num_floor(("house_judge", "label_mass_floor"), (_get(baseline, "house_judge", "label_mass_floor")[0] or 0.0), "label_mass_floor")

    band, present = _get(candidate, "refer_band")
    if present:
        base = _get(baseline, "refer_band")[0]
        ok = isinstance(band, (list, tuple)) and len(band) == 2 and all(isinstance(x, (int, float)) for x in band)
        if not ok or (base and (band[0] > base[0] or band[1] < base[1])):
            bad.append(f"refer_band: {band!r} narrows or removes baseline {base!r}")

    if _get(baseline, "second_judge")[1] and _get(baseline, "second_judge")[0]:
        sj, present = _get(candidate, "second_judge")
        if present and not sj:
            bad.append("second_judge: disabled or removed")

    vc, present = _get(candidate, "validated_contracts")
    if present:
        base = _get(baseline, "validated_contracts")[0]
        if not (isinstance(vc, (list, dict, tuple, set)) and isinstance(base, (list, dict, tuple, set)) and set(vc) <= set(base)):
            bad.append("validated_contracts: widened or malformed (safety allowlist)")

    def walk(d, prefix=""):
        for k, v in d.items():
            p = f"{prefix}{k}"
            if "quote" in str(k).lower() and _get(baseline, *p.split("."))[0] != v:
                bad.append(f"{p}: quote-check parameter changed (any change is refused; direction is not decidable generically)")
            if isinstance(v, dict):
                walk(v, p + ".")
    if isinstance(candidate, dict):
        walk(candidate)
    return bad
