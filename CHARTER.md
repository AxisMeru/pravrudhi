*Public pre-registration of Pravrudhi. Copied from the blueprint charter on 2026-09-04; amendments C1–C18 of the review response apply and are recorded in the paper as they land. Numbers about the outside world carry sources; numbers about this system are targets until a gate produces them.*

# Pravrudhi Charter

*प्रवृद्धि · pravṛddhi · "growth, increase, flourishing". Blueprint v0.2, 2026-09-04 (v0.1 amended by the adversarial-review change list `04-reviews/00-review-response.md` C1–C18; items marked ADR there are binding on the first sessions). This charter is the pre-registration of the project's hypotheses, thresholds, non-goals and closure contract. Changing a threshold requires an ADR in `docs/decisions/`.*

## 1. Mission

Build, from scratch, a recursive self-improvement framework for LLM agent harnesses that goes head-to-head with the published 2026 industry state of the art (Darwin Gödel Machine / Huxley-Gödel Machine / HyperAgents; AlphaEvolve-class program evolution; Karpathy-lineage autoresearch incl. Bilevel and GEAR; SEAL / Absolute Zero / R-Zero / Agent0 weight self-training) on their own measurable terms, while taking a different path: the controller is an expected-free-energy (active inference) planner over an explicit posterior, the epistemology is darśana-derived (pramāṇa-tagged evidence, pratyabhijñā as re-cognition of testimony by execution, bādha as the only update rule, Nyāya six-phase claim admission), and mechanistic interpretability is inside the loop as sensor and actuator. The system must run on one shared RTX 5090 with local open models only. Its first product use is to close the propose→build gap in prabhasa-samskrutam.

The Sanskrit vocabulary is a naming and audit discipline inherited from the portfolio, not a claim in itself (change C17). What is testable and new is the *typed evidence ordering* (pramāṇa provenance with a bādha defeat rule, ablated in RD-4) and the *epistemic term* in the controller (H2); stripped of both, this is cost-aware Bayesian optimisation over harness edits with a sequential gate, and it should be described that way to anyone outside the project until RD-4 and H2 return.

Publication is secondary. The statistical rigour is not there to falsify; it is the pruning shears for the exploration tree (prabodha `HANDOFF.md` §0). The aim is the working, self-growing instrument.

**Precondition on everything below.** Before any controller exists, P0 runs the ~13 GPU-hour signal-to-noise study of `04-reviews/00-review-response.md` §4 and writes σ (the null distribution of the primary metric), the effect-size distribution of known-good edits, and the per-experiment wall-clock into the prereg config. Δ\*, the sequential boundaries, and the seeds-per-decision economics are all *derived from those three numbers* (C5, C10). If known-good edits sit inside the noise band at n=1, the nanochat testbed cannot separate selection policies and the harness testbed becomes primary for H1 — that decision is made in week one, not month three.

## 2. Hypotheses (pre-registered)

Each hypothesis names its tier ceiling, its metric, its threshold, its kill criterion, and its phase. Claims may only be stated at the tier they passed (smoke → screen → confirm). "Confirm" means ≥3 seeds (≥5 for whole-loop comparisons), paired design, BCa 95% CI, Hedges *g* reported, and family-wise correction applied **at family closure**, where a family is defined by candidate lineage and surface rather than by the calendar night (C11); online FDR (e-BH / alpha-investing) governs the long-run stream of confirmations. Sequential screening uses a **variance-adaptive** e-process (self-normalised / empirical-Bernstein), not a fixed-σ mSPRT (C10). Every confirm reports its **minimum detectable effect and power** alongside the result; at 5 loop seeds the MDE is large, so effect size and CI are primary and the *p*-value is secondary.

| ID | Hypothesis | Primary metric & threshold | Kill criterion (→ `pruned`, with hetvābhāsa label) | Phase |
|---|---|---|---|---|
| **H1 Controller** | An EFE controller over an explicit hierarchical posterior reaches the pilot-derived target Δ\* with lower regret per GPU-hour than (a) greedy ratchet, (b) **greedy ratchet + Pravrudhi's sequential gate** — the arm that isolates selection from gating (C4) — (c) GEAR-like frontier search and (d) HGM-like lineage Thompson sampling, at matched experiment count. Candidates are bucketed by *edit family* so the hierarchy has real between-bucket variance (C4). | Regret-per-GPU-hour to Δ\* (Δ\* = 60th percentile of the greedy arm's 100-experiment gain distribution from the P0 pilot, C5); effect size and CI primary, power reported; *g* ≥ 0.5 vs each baseline as the target effect | Screen (P1, 1–2 loop seeds): after 3 nights × 100 experiments per arm EFE is not better than greedy on the point estimate → do not proceed to confirm; run the H2 ablation to learn why. Confirm (P6, 5 loop seeds, ≈240 GPU-h) is the headline claim. | P1 screen → P6 confirm |
| **H2 Epistemic term** | Removing the epistemic term (γ_epi = 0) or the pure-epistemic nights degrades regret. Confirmed on the **harness testbed** (genuinely multi-surface); the nanochat screen is indicative only, because with one surface the EIG term varies only across edit families (C4). | Paired Δ regret between full and ablated controller; *g* ≥ 0.3 | Ablated ≥ full after 5 loop seeds → epistemic term is decorative; keep posterior, drop EIG, record as honest negative | P1–P2 |
| **H3 Sequential gating** | Group-sequential seed testing reduces seeds spent per decision by ≥40% at a false-promotion rate ≤5% on planted-null candidates, versus a fixed 3-seed battery. | Seeds/decision averaged over *all* decisions including screen-stage prunes; false-promotion rate on **A/A planted nulls** — the same candidate against itself under a different execution seed and held-out rotation, so the null carries real variance (C8; cosmetic-diff nulls are a separate cache-integrity test and are never the false-promotion estimate). n ≥ 200; the reportable resolution of the estimate is stated with it | False-promotion rate > 8% → revert to fixed battery; re-derive boundaries | P0 (simulation) → P2 (real) |
| **H4 Anti-gaming** | Kernel isolation + evaluator rotation + diff auditor detect ≥95% of planted evaluator-gaming edits (test-log fakery, marker removal, metric-path edits, held-out leakage) with ≤2% false alarms on benign diffs. | Detection rate / false-alarm rate on a red-team battery of ≥100 planted diffs | Detection < 80% → isolation model is insufficient; escalate to TRIZ (mandatory on second failure) | P2 |
| **H5 Transfer** | Harness improvements found with the Tier-1 model retain ≥50% of their held-out gain when the harness is run with the Tier-2 model, and on a held-out task family. | Retained fraction of Δ_out; CI excludes 0.5 from below | Retention < 25% on both → improvements are model-specific; controller must bucket by model (record; not a project kill) | P2–P3 |
| **H6 Interp sensors** | At least one internal sensor (SAE feature deltas, lens readback, refusal/truth-direction shift, entropy budget) predicts held-out outcome of a self-modification better than chance and improves the predictor's calibrated reliability ρ_pred when used as a prior. | AUROC > 0.6 (CI excludes 0.5); Δ Brier of predictor < 0 | No sensor exceeds AUROC 0.55 after ≥200 labelled cycles → interp stays diagnostic-only (honest negative expected to be plausible; prabodha readback BA 0.59 is the prior) | P3 |
| **H7 Weight-level** | LoRA-only self-edits (RFT / GRPO-with-verifiable-rewards / SEAL-style) selected by the controller on the v1 trainee **Qwen3-4B** (plain transformer, Apache-2.0, Qwen-Scope SAE coverage, GRPO fits ≈15 GiB; C18 — Qwen3.5-9B moves to a P4 transfer probe) achieve gains ≥ the R-Zero/Agent0 baseline on the target suite over ≥10 iterations **without** canary non-inferiority failure (no catastrophic forgetting). | Target-suite Δ vs baseline; canary non-inferiority as a one-sided test at α=0.05 (90% CI lower bound > −δ, δ pre-registered per canary; full TOST reported for the record) | Canary failure in ≥3 of 10 iterations, or plateau below baseline → weight-level actions demoted to `W1`/`W2` only | P4 |
| **H8 Prabhasa closure** | Knob synthesis converts ≥50% of prabhasa's historically skipped (exit-75) EFE candidates into runnable two-arm proxies that pass their generated invariants, and at least one synthesised knob is promoted by prabhasa's own gate. | Conversion rate; promotions | Conversion < 25% after two attempts per knob → record NULL with ≥2 documented interventions | P5 |

Whole-loop comparisons (H1, H2) are the project's headline; H3–H4 are its trust claims; H5–H7 are its breadth claims; H8 is its product claim.

## 3. Beat-SOTA targets (external, from Report 01 §9.1)

Targets are stated so that a reviewer can check them; they are *aspirations* until the internal hypotheses pass. All GPU-hour figures live in `03-implementation/03-benchmarks-and-targets.md` and are cited, never restated here (C6).

1. **Autoresearch:** on Karpathy's unmodified `prepare.py`/eval with a fixed slice budget (300 s on the 5090, as Bilevel used), beat GEAR-Evolve's frontier search and Bilevel's L2 at equal experiment count, reported with ≥5 loop seeds and CIs — the first autoresearch result with a principled selection rule and error bars.
2. **Scaffold self-improvement:** with a frozen local model (Qwen3.8-27B or Qwen3.6-27B), exceed a DGM-style archive baseline run under identical budget on Polyglot / SWE-bench Multilingual subsets, and show transfer to a held-out family (HyperAgents' criterion); absolute SWE-bench Pro leaderboards are out of reach for local models and are not a target.
3. **Weight self-training:** gains that do not plateau over ≥10 iterations on Qwen3-4B/Qwen3.5-9B with held-out non-math retention (beats the documented plateau/forgetting pattern of R-Zero/SAGE/SEAL).
4. **Verification:** zero fabricated results under an MLR-Judge-style audit by construction (kernel-executed evidence only) — a structural claim, demonstrated on a sample of nights.

## 4. Non-goals (v1)

Frontier-API models in the loop; use of Gemma-licensed models as the *primary* redistributed trainee without a licence review (Apache-2.0/MIT models are the default); whole-harness code rewrite of the DGM kind; multi-GPU or DGX Spark dependence (GB10 is out for RMA); merging adapters into canonical checkpoints without human sign-off; any self-modification of the kernel `T0`; a paper before the system passes H1 and H3–H4; Sanskrit vocabulary on external API surfaces.

## 5. Closure contract (six layers, machine-checked)

Inherited from PSALM / prabhasa-samskrutam (`closure.py`, `<cli> contract check`), with the pranava dual-verdict rule. A loop, phase or hypothesis is closed only when:

1. **TECHNICAL** — tests green (≥80% coverage on `T0`, property tests on every kernel function), Docker image builds, `make reproduce` regenerates artefacts from fixed seeds.
2. **EMPIRICAL** — the pre-registered metric was computed by the kernel from hash-verified runs, at the tier claimed, with the stats protocol of §2 (sequential e-process boundaries at screen; Holm–Bonferroni over the live family at *confirm* only, never double-applied to screen e-values); `deviations` field lists every post-hoc change.
3. **INTEGRITY** — a Tarka memo states the strongest objection to the finding; the auditor's Nyāya six-phase trace (saṃśaya → pramāṇa → pañca-avayava → tarka → hetvābhāsa → nirṇaya) is attached; the decorative-controller check passed; no `T0` path was touched (broker log clean).
4. **ARTIFACTS** — ledger rows, gate JSON (`gates/gate_<ID>.json` with `code_gate ∧ domain_gate`, status `pass|fail|pruned`), evidence pack in the inbox, consistency audit across README/spec/journal (headline-drift check).
5. **MEMORY** — `research/state.json` replayed from the ledger matches; journal entry appended; ADR if a threshold moved.
6. **SIGN-OFF** — a human has read the interpretation. Overnight autonomy never substitutes for this layer; it queues for it.

Honest negatives are closures (status `pruned`). Never declare NULL on attempt 1; NULL requires ≥2 documented interventions. A gate that fails twice for the same reason triggers a mandatory TRIZ escalation (sage convention).

## 6. The Sākṣī (session-invariant hard rules)

Pushed as the witness prefix at every model call (PCEH `set_sakshi`; fallback: this section verbatim in `CLAUDE.md`):

* Evidence comes only from the kernel. No number is stated that the ledger does not contain.
* Every proposal is āgama until executed; every stored claim carries a pramāṇa tag; updates are sublations with reasons.
* `T0` is not yours to edit. If a task seems to need it, write an ADR request and stop.
* Real GPU runs are sandboxed and disposable. A gated `T2` change is applied by the broker; promotion to *canonical* (merging adapters into a base checkpoint, merging to `main`, signing an interp claim) is a human act via the inbox.
* Claims are stated at the tier they passed. Pipeline-measured is labelled pipeline-measured.
* Falsification is not the aim; growth is. Prune with statistics, then propose the experiment that finds what is true.
* Commit as `SharathSPhD <admin@axismeru.com>`, no Co-Authored-By trailer (house rule; overrides harness defaults).
