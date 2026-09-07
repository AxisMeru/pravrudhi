"""vimarśa: score the live pool with L2, run the decorative check, fill the budget, write `select` rows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from pravrudhi.application.citta_view import build_citta, keys_for
from pravrudhi.application.archive import parent_map
from pravrudhi.application.policies import POLICIES, fill_budget, rank_scores, selection_weights
from pravrudhi_kernel.efe import (
    BeliefKeys,
    PrecisionView,
    Shares,
    decorative_check,
    efe,
    habit_prior,
    knapsack_batch,
    selection_probabilities,
)
from pravrudhi_kernel.ledger import LedgerWriter, replay
from pravrudhi_kernel.schema import AbstractionLevel, Candidate, EvidencePlan, Pramana, Prediction, Preferences, Residency, Stage


def mi_floor(mi_min_bits: float, *, bootstrap: bool, round_index: int) -> float:
    """C2: no information floor before any observation exists; ADR-0012: none after a night's first round."""
    return 0.0 if (bootstrap or round_index > 0) else mi_min_bits


class DecorativeAbort(RuntimeError):
    pass


def live_candidates(
    state_candidates: dict[str, Any], meta: dict[str, dict[str, Any]], incumbent_id: str, target_model: str | None = None
) -> list[str]:
    out = []
    for cid in meta:
        if cid == incumbent_id or cid.startswith("c-0000"):
            continue
        if target_model and (meta[cid].get("bucket") or {}).get("target_model") not in (None, target_model):
            continue  # a candidate proposed for another trainee is not live here (its adapter cannot load)
        c = state_candidates.get(cid)
        if c is None or c.pruned or c.promoted or c.audit_high or c.skipped:
            continue
        if c.last_boundary in ("confirm", "prune"):
            continue
        out.append(cid)
    return out


def deliberate(
    root: Path,
    w: LedgerWriter,
    *,
    night: int,
    budget_gpu_h: float,
    sigma_seed: float,
    incumbent_id: str,
    harness_hash: str,
    model_hash: str,
    rng_seed: int,
    log: Any = print,
    surface: str | None = None,
    target_model: str | None = None,
    round_index: int = 0,
    selection_policy: str = "efe",
) -> list[str]:
    cfg = yaml.safe_load((root / "research" / "prereg" / "controller.yaml").read_text())
    night_yaml = root / "research" / "prereg" / "lora_night.yaml"
    cfg_night = yaml.safe_load(night_yaml.read_text()) if night_yaml.exists() else None
    sigma2_eval = max(sigma_seed, 1e-4) ** 2
    ledger = root / "research" / "ledger.jsonl"
    citta, meta = build_citta(
        ledger,
        root / ".pravrudhi" / "kernel" / "sealed" / "predictions",
        sigma2_eval=sigma2_eval,
        tau0_2=float(cfg["tau0_2"]),
    )
    st = replay(ledger)
    tm = target_model or (str(cfg_night.get("model")) if cfg_night else None)
    pool = live_candidates(st.candidates, meta, incumbent_id, target_model=tm)
    if surface:
        pool = [c for c in pool if meta[c]["surface"] == surface]
    if not pool:
        log("deliberate: no live candidates")
        return []
    if len(pool) == 1:
        # A choice among one is no choice: the decorative check does not apply (it would rightly fail), and the
        # survivor receives its next seed so the boundary can decide it (night 5 aborted here with 2.4 GPU-h unspent).
        cid = pool[0]
        n_obs = st.candidates[cid].n_obs if cid in st.candidates else 0
        w.append(
            "select",
            "controller",
            {
                "Q": 1.0,
                "strategy": meta[cid]["strategy"],
                "plan": {"seeds": [n_obs], "heldout_rotation_id": None, "sensors_to_read": [], "stage": "screen",
                         "sequential_stage": n_obs},
                "decorative": {"cv_G": None, "mi_bits": None, "verdict": "not_applicable", "reason": "single live candidate"},
                "night_mode": "continuation",
                "policy": selection_policy,
                "harness_hash": harness_hash,
                "model_hash": model_hash,
                "epistemic": False,
                "rank": 0,
            },
            epoch=0,
            night=night,
            cycle=1,
            candidate_id=cid,
            surface=meta[cid]["surface"],
            bucket=meta[cid]["bucket"],
        )
        log(f"deliberate: single live candidate {cid} continues (n_obs={n_obs}); decorative check not applicable")
        return [cid]
    prefs = Preferences.model_validate(
        {
            "beta": float(cfg["preferences"]["beta"]),
            "lambda": float(cfg["preferences"]["lambda"]),
            "eta": float(cfg["preferences"]["eta"]),
        }
    )
    post_var = [citta.candidates[c].sigma2 for c in pool if c in citta.candidates]
    rho = float(np.mean(list(citta.rho_pred.values()))) if citta.rho_pred else 0.0
    from pravrudhi_kernel.efe import infer_precision

    gamma = infer_precision(
        PrecisionView(
            pool_post_var=post_var,
            sigma2_eval=sigma2_eval,
            rho_pred=rho,
            f_epi=float(cfg["f_epi"]),
            rho_floor=float(cfg["rho_floor"]),
        )
    )
    cands: dict[str, Candidate] = {}
    keys: dict[str, BeliefKeys] = {}
    terms = {}
    habits = {}
    for cid in pool:
        m = meta[cid]
        n_obs = st.candidates[cid].n_obs if cid in st.candidates else 0
        k = keys_for(cid, m["surface"], m["bucket"], m["strategy"], m["family"])
        keys[cid] = k
        cand = Candidate(
            id=cid,
            surface=m["surface"],
            bucket=m["bucket"],
            edit_family=m["family"] or "-",
            strategy=m["strategy"],
            lineage=[incumbent_id],
            diff_ref="0" * 64,
            cost_est_gpu_h=float((m["recipe"] or {}).get("_cost", 0.0)) or _cost(m),
            residency_need=Residency.executor,
            predicted=Prediction(delta_in=0.0, delta_out=None, conf=0.0, hash="0" * 64),
            abstraction_level=AbstractionLevel.madhyama,
            provenance=Pramana.agama,
        )
        cands[cid] = cand
        plan = EvidencePlan(
            seeds=[n_obs],
            heldout_rotation_id=None,
            sensors_to_read=[],
            stage=Stage.screen,
            sequential_stage=n_obs,
        )
        terms[cid] = efe(
            citta,
            cand,
            plan,
            prefs,
            gamma,
            float(cfg["kappa"]),
            budget_gpu_h,
            sigma2_eval,
            float(cfg["tau0_2"]),
            keys=k,
        )
        habits[cid] = habit_prior(citta, k, float(cfg["tau0_2"]))
    if selection_policy not in POLICIES:
        raise ValueError(f"unknown selection_policy {selection_policy!r}; expected one of {POLICIES}")
    G = {c: t.G for c, t in terms.items()}
    rng = np.random.default_rng(rng_seed)
    bootstrap = not any((st.candidates[c].n_obs if c in st.candidates else 0) > 0 for c in pool)
    if selection_policy == "efe":
        Q = selection_probabilities(G, habits)
        # C2 bootstrap: before any candidate in the pool has a kernel observation, near-uniform selection is the
        # expected behaviour, not a decorative controller; the identical-score and CV criteria still apply, the MI
        # floor does not. ADR-0012: the information floor applies only in a night's first round.
        mi_min = mi_floor(float(cfg["decorative"]["mi_min_bits"]), bootstrap=bootstrap, round_index=round_index)
        verdict = decorative_check(G, Q, float(cfg["decorative"]["cv_min"]), mi_min)
        decorative = verdict.model_dump()
    else:
        # A baseline arm of H1: rank by the arm's own rule and fill the budget in that order. The decorative check
        # tests whether the EFE scores condition on the action; a baseline does not compute those scores, and the
        # random arm is decorative by construction, which is the point of having it.
        #
        # The two lineage arms are handed the parent map folded from the ledger. Without it `gear` calls every
        # unmeasured candidate equally novel and `hgm` shrinks toward a leave-one-out archive mean, which are the
        # degenerate fallbacks their docstrings name rather than the rules they were written for. The other arms
        # ignore the argument.
        scores = rank_scores(selection_policy, citta, pool, rng, parent_map(ledger))
        baseline_order = fill_budget(scores, {c: cands[c].cost_est_gpu_h for c in pool}, budget_gpu_h)
        Q = selection_weights(scores, baseline_order)
        decorative = {"cv_G": None, "mi_bits": None, "verdict": "not_applicable", "reason": f"baseline arm {selection_policy}"}
    (root / "research" / "last_select.json").write_text(
        json.dumps(
            {"night": night, "policy": selection_policy, "scores": G, "selection": Q, "verdict": decorative},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    if selection_policy == "efe":
        if verdict.verdict == "fail":
            w.append(
                "audit",
                "controller",
                {"kind": "decorative_controller", "severity": "high", "detail": verdict.model_dump()},
                epoch=0,
                night=night,
            )
            raise DecorativeAbort(verdict.reason or "decorative")
        shares = Shares(
            planted=float(cfg["shares"]["planted"]), sensors=float(cfg["shares"]["sensors"]), f_epi=float(cfg["f_epi"])
        )
        batch = knapsack_batch(Q, cands, {c: t.EIG for c, t in terms.items()}, budget_gpu_h, shares, rng)
        chosen = batch.deliberation + batch.execution
        order = sorted(chosen, key=lambda c: -Q[c])
        epistemic_ids, budget_effective, spent_planned = batch.epistemic_ids, batch.budget_effective, batch.spent_gpu_h
    else:
        order = baseline_order
        epistemic_ids = []
        budget_effective = budget_gpu_h
        spent_planned = sum(cands[c].cost_est_gpu_h for c in order)
    for i, cid in enumerate(order):
        t = terms[cid]
        w.append(
            "select",
            "controller",
            {
                "G": t.G,
                "EIG": t.EIG,
                "pragmatic": t.pragmatic,
                "cost_term": t.cost_term,
                "gamma": t.gamma.model_dump(),
                "kappa": t.kappa,
                "habit_prior": habits[cid],
                "Q": Q[cid],
                "strategy": meta[cid]["strategy"],
                "plan": {
                    "seeds": [st.candidates[cid].n_obs if cid in st.candidates else 0],
                    "heldout_rotation_id": None,
                    "sensors_to_read": [],
                    "stage": "screen",
                    "sequential_stage": st.candidates[cid].n_obs if cid in st.candidates else 0,
                },
                "decorative": decorative,
                "policy": selection_policy,
                "night_mode": "bootstrap" if bootstrap else "exploit",
                "harness_hash": harness_hash,
                "model_hash": model_hash,
                "epistemic": cid in epistemic_ids,
                "rank": i,
                "budget_effective": budget_effective,
                "spent_planned": spent_planned,
            },
            epoch=0,
            night=night,
            cycle=i + 1,
            candidate_id=cid,
            surface=meta[cid]["surface"],
            bucket=meta[cid]["bucket"],
        )
    cv = decorative.get("cv_G")
    mi = decorative.get("mi_bits")
    detail = f"cv_G={cv:.3f} mi={mi:.3f}" if cv is not None and mi is not None else "decorative=n/a"
    log(f"deliberate[{selection_policy}]: pool={len(pool)} selected={len(order)} gamma={gamma.model_dump()} {detail}")
    return order


def _cost(m: dict[str, Any]) -> float:
    from pravrudhi.targets import parse_recipe

    r = parse_recipe(m["recipe"] or {})
    return r.cost_est_gpu_h() if not isinstance(r, str) else 0.1
