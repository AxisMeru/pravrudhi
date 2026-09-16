# P2b/SFT eval gate + base-model — Track B (content/provenance) input, 2026-09-16 09:38 BST

Shapes the P2b prereg. No GPU until this gate is frozen, the leakage-disjoint held-out eval is built + hashed, and both bases are preflighted against it.

## 1. Eval gate — measurable "meets or exceeds objectives"

A CONJUNCTION over three held-out tiers mirroring T/P/R, not one metric. The model's job: author checker-certifiable nyaya-law-v1 IR, abstain when it should, retrieve/identify. Metrics:

1. **Structural validity ≥ 0.98** — generated wire parses + canonical target.elements (el0..el_{n-1}, valid roles/ops). Table stakes; a model emitting malformed IR is unusable.
2. **Held-out full-IR checker-pass rate (HEADLINE)** — on held-out established-element instances, the model's answer produces IR that check(answer, contract_id) CERTIFIES. Threshold: measure-and-set above the base-model baseline (the un-SFT'd model's checker-pass, measured in preflight) — SFT must LIFT checker-pass meaningfully, not just hit an absolute. Floor proposed after the base baseline is measured (same measure-then-set discipline as R_min-to-reality).
3. **False-prove rate ≤ 0.02 (HARD ceiling, safety-critical)** + abstention recall ≥ 0.90 — on held-out UNPROVABLE/omission/unknown-contract instances, the model MUST NOT certify-proved (NIGAMANA proved / ABSTAIN false) and MUST abstain/refuse. A legal model that fabricates a proof is disqualifying; this is the one metric I set as a hard ceiling, not a measure-and-set.
4. **Held-out retrieval recall ≥ 0.493** — statute/title-kind identification on held-out R-style tasks; beat the 1.13B prior traction (0.366→0.493), else measure-and-set above base.

Primary gate = (1)∧(2)∧(3); (4) is the retrieval-competency check. "Exceeds" = clears all four AND out-performs base by a measured margin on (2)/(4).

## 2. Leakage-disjoint held-out eval — the provenance-critical carve (frozen + hashed BEFORE any training minute)

Split by the LEAKAGE UNIT, not by record, so no unit straddles train/eval; verified against T/P/R with the P2a exclusion-index method:
- **Full-IR eval:** do NOT carve from the thin T=212. Build FRESH checker-CERTIFIED held-out scenarios per the 14 contracts (~10-15/contract = ~140-210), leakage-disjoint from every T record by entity-set AND narrative-only Jaccard (the P2a guards). Gold = checker-verified IR (checker-anchored, not teacher-trust-anchored — so the alias-teacher provenance caveat does not touch the eval's authority). Keeps all 212 T for training.
- **P eval:** partition CaseHOLD-val by casehold_example_id — hold out ~10% (≈530), disjoint from the P training rows; re-run P build on the train partition only.
- **R eval:** partition by PROVISION — hold out ~10-15% of the 915 provisions ENTIRELY (all 3 framings), eval on those; no held-out provision appears in any training instance (framing-level split would leak a provision across the boundary).
The split manifest is frozen, sha-pinned, and leakage-verified before training; the eval harness never reads training data.

## 3. Base model — EMPIRICAL decide, no upfront pick

Agree with the lead: preflight BOTH (1.13B-existing g0/rsi checkpoints vs 3-4B-fresh) — the house rules require a ≤30min preflight regardless — measure each against the frozen eval gate, pick the base that meets/exceeds. No content/provenance reason forces one upfront: both train on the same T/P/R, identical provenance; the differentiator is measured eval performance, which is what the preflight+gate measures. Additions: (i) preflight measures each BASE (un-SFT'd) on the held-out eval too, so we know the SFT LIFT not just the post-SFT number; (ii) the thin T=212 is the risk the eval exposes — whether a fresh 4B generalizes full-IR from thin T or the 1.13B's prior traction wins is exactly what held-out checker-pass reveals; (iii) tiebreak if BOTH meet the gate: prefer the operator's stated 3-4B product direction; if only one meets, that one; if neither, no scale-up — fix data/approach first, no GPU spend chasing a gate neither base clears.
