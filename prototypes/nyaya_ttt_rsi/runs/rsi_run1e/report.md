# Run report: rsi_run1e

- **Checkpoint:** /trackB-local/m7/m7_retry_checkpoint.pt
- **Created:** 2026-09-13
- **Held-out queries:** 690

## Conditions

| Metric | A — frozen closed-book (published baseline) | B' — grounded compact context + scoring + calibrated abstention (round-1e persistent LoRA) | B'(frozen) — grounded compact context + scoring + calibrated abstention, NO consolidation (frozen 370M) | C' — B' + ephemeral TTT on shown passages, then rescore (gated, untuned settings) | D — Phase 4: round-2 consolidation on B'-gate-accepted pseudo-labels (no gold), re-calibrated |
| --- | --- | --- | --- | --- | --- |
| citation_recall | 0.004 (1/227) [0.001–0.025] | 0.145 (33/227) [0.105–0.197] | 0.176 (40/227) [0.132–0.231] | 0.128 (29/227) [0.090–0.177] | 0.198 (45/227) [0.152–0.255] |
| citation_precision | 0.004 (1/227) [0.001–0.025] | 0.190 (33/174) [0.138–0.254] | 0.206 (40/194) [0.155–0.269] | 0.190 (29/153) [0.135–0.259] | 0.232 (45/194) [0.178–0.296] |
| abstention_correctness | 0.556 (5/9) [0.267–0.811] | 0.889 (8/9) [0.565–0.980] | 0.444 (4/9) [0.189–0.733] | 0.889 (8/9) [0.565–0.980] | 0.889 (8/9) [0.565–0.980] |
| law_lookup_prefix_similarity_mean | 0.059 | 0.626 | 0.108 | 0.617 | 0.617 |
| gold_selected | – | 0.491 (339/690) [0.454–0.529] | 0.104 (72/690) [0.084–0.129] | 0.467 (322/690) [0.430–0.504] | 0.501 (346/690) [0.464–0.539] |
| abstain_on_miss | – | 0.777 (115/148) [0.703–0.837] | 0.514 (76/148) [0.434–0.593] | 0.770 (114/148) [0.696–0.831] | 0.851 (126/148) [0.785–0.900] |
| false_abstain_when_shown | – | 0.116 (62/533) [0.092–0.146] | 0.430 (229/533) [0.388–0.472] | 0.182 (97/533) [0.152–0.217] | 0.086 (46/533) [0.065–0.113] |

### Timing

| Condition | Wall time (s) | Peak VRAM (GiB) |
| --- | --- | --- |
| A — frozen closed-book (published baseline) | 189.8 | 5.33 |
| B' — grounded compact context + scoring + calibrated abstention (round-1e persistent LoRA) | 41.7 | 2.98 |
| B'(frozen) — grounded compact context + scoring + calibrated abstention, NO consolidation (frozen 370M) | 41.4 | 2.98 |
| C' — B' + ephemeral TTT on shown passages, then rescore (gated, untuned settings) | 463.2 | 3.55 |
| D — Phase 4: round-2 consolidation on B'-gate-accepted pseudo-labels (no gold), re-calibrated | 37.9 | 3.56 |

## Paired comparisons

| a → b | Metric | b only | c only | McNemar p | Bootstrap CI |
| --- | --- | --- | --- | --- | --- |
| A → B' | gold-citation selected | 179 | 2 | 1.07e-50 | [0.223, 0.288] |
| A → B' | grounded | 690 | 0 | 3.89e-208 | [1.000, 1.000] |
| B' → C' | gold-citation selected | 11 | 17 | 0.345 | [-0.023, 0.006] |
| B' → C' | grounded | 0 | 0 | 1 | [0.000, 0.000] |
| B' → D | gold-citation selected | 24 | 19 | 0.542 | [-0.012, 0.026] |
| B' → D | grounded | 0 | 0 | 1 | [0.000, 0.000] |

## Gate

- **Accepted:** 690
- **Rejected:** 0
- **Reasons:**
  - ok: 690

## Notes

- Round history: rsi_run1 (baseline pipeline, 0% grounded -- prompt-format mismatch), rsi_run1b (abstain string memorized, F14), rsi_run1c (titles added, F13, still F14 collapse), rsi_run1d (citation-only, no abstain string, F15 length bias found), rsi_run1e (compact k=4/60B-body/90B-title/950B-budget context, F16; uniform citation targets; calibrated abstention replaces in-band abstain string) -- this run.
- Retrieval recall with titles: recall@1=0.567, recall@3=0.736, recall@4=0.772, recall@5=0.793 (k=4 used here, chance for in-sample sanity = 1/4 = 0.25).
- In-sample scoring-mode sanity (30 train prompts, citation-only candidates): frozen model 0.400 hit rate, round-1e (2 epochs, lr 3e-4, r16) 0.767 (gate: >=0.5). SFT gate probe_delta_rel=0.075 (<0.15 threshold, accepted).
- Abstention calibration (500 train-split dev examples, seed 1, 30% synthetic miss, disjoint from the 2496-example SFT set): round-1e tau=5.284 delta=0.352 balanced_accuracy=0.843; frozen model tau=31.308 delta=0.105 balanced_accuracy=0.580 (same tau/delta from the round-1e calibration were used, unmodified, for both B' and C' held-out).
- B' dropped_passage_rate=0.0014 (1/690) -- rare over-budget-after-truncation fallback fired as expected.
- C' does NOT beat B' on gold-citation-selected (0.2507 vs 0.2594, McNemar p=0.345, not significant) -- per the run plan, Phase 4 (a second RSI consolidation round) was skipped.
- A vs B' is decisive: gold-citation-present 0.0029 -> 0.2594 (McNemar p=1.07e-50); grounded_rate 0.0 -> 1.0 by construction (scoring mode always emits either a shown citation or a calibrated abstention, never free-form hallucinated continuation).
- B'(frozen) vs B': consolidation alone is responsible for most of the gain -- gold_selected 0.104 (frozen) vs 0.491 (round-1e persistent LoRA); frozen-model calibration is also much weaker (balanced accuracy 0.580 vs 0.843).
- Phase 4 (RSI self-training, no gold): streamed 500 fresh train prompts (seed 2, disjoint from the 2,496 SFT examples and the 500 calibration dev items); 134 gate-rejected on B'-s own tau/delta, 366 accepted as pseudo-labels. For the record only (never used for gating): 0.702 of accepted pseudo-labels matched the train split's own gold citation. Round-2 consolidation (lr=1e-4, 1 epoch, from B''s persistent state) passed its regression-probe gate (probe_delta_rel=0.028 < 0.15). Re-calibrated on the SAME 500-item dev slice: tau=4.460, delta=0.000, balanced_accuracy=0.846 (vs 0.843 for B'). D vs B' on the 690: gold_selected 0.501 vs 0.491 (McNemar p=0.349, bootstrap CI [-0.007246376811594203, 0.028985507246376812]) -- directionally positive on several sub-metrics (law_citation_retrieval hit rate 0.145 -> 0.198, abstain_on_miss 0.777 -> 0.851, false_abstain_when_shown 0.116 -> 0.086) but NOT statistically significant on the headline gold_selected metric at n=690 -- reported plainly as a small, non-significant improvement, not a proven win.
- TTT dev sweep (120 dev items, seed 3, subsampled from the 500-item calibration dev slice, NOT held-out; B''s own tau/delta used throughout, never recalibrated per cell): no-TTT baseline gold_selected_rate=0.492. Best grid cell (steps=2, lr=0.0003, target=attn_only) delta vs baseline = 0.008 (< 0.03 threshold) -- no cell beat the no-TTT baseline by the required margin, so C'' was NOT run. Aggressive settings (lr=3e-3, steps=8, all persistent LoRA modules) were actively destructive (delta=-0.408, false_abstain_rate rose to 0.856). Clean negative finding for per-query ephemeral TTT at 370M, consistent with the untuned C' vs B' result above.
- Note on rank: the TTT sweep's 'attn-only' vs 'all' target dimension reuses subsets of the persistent r=16 LoRA's own modules rather than injecting a genuinely separate r=8 adapter -- F10 (in FIXES-FOR-MAIN-SESSIONS.md) explains why a second LoRA layer cannot be stacked on an already-injected one; disclosed here as a deviation from the literal r=8 spec, not a silent substitution.
