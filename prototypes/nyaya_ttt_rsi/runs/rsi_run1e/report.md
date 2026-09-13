# Run report: rsi_run1e

- **Checkpoint:** /trackB-local/m7/m7_retry_checkpoint.pt
- **Created:** 2026-09-13
- **Held-out queries:** 690

## Conditions

| Metric | A — frozen closed-book (published baseline) | B' — grounded compact context + scoring + calibrated abstention (round-1e persistent LoRA) | C' — B' + ephemeral TTT on shown passages, then rescore (gated) |
| --- | --- | --- | --- |
| citation_recall | 0.004 (1/227) [0.001–0.025] | 0.145 (33/227) [0.105–0.197] | 0.128 (29/227) [0.090–0.177] |
| citation_precision | 0.004 (1/227) [0.001–0.025] | 0.190 (33/174) [0.138–0.254] | 0.190 (29/153) [0.135–0.259] |
| abstention_correctness | 0.556 (5/9) [0.267–0.811] | 0.889 (8/9) [0.565–0.980] | 0.889 (8/9) [0.565–0.980] |
| law_lookup_prefix_similarity_mean | 0.059 | 0.626 | 0.617 |
| gold_selected | – | 0.491 (339/690) [0.454–0.529] | 0.467 (322/690) [0.430–0.504] |
| abstain_on_miss | – | 0.777 (115/148) [0.703–0.837] | 0.770 (114/148) [0.696–0.831] |
| false_abstain_when_shown | – | 0.116 (62/533) [0.092–0.146] | 0.182 (97/533) [0.152–0.217] |

### Timing

| Condition | Wall time (s) | Peak VRAM (GiB) |
| --- | --- | --- |
| A — frozen closed-book (published baseline) | 189.8 | 5.33 |
| B' — grounded compact context + scoring + calibrated abstention (round-1e persistent LoRA) | 41.7 | 2.98 |
| C' — B' + ephemeral TTT on shown passages, then rescore (gated) | 463.2 | 3.55 |

## Paired comparisons

| a → b | Metric | b only | c only | McNemar p | Bootstrap CI |
| --- | --- | --- | --- | --- | --- |
| A → B' | gold-citation selected | 179 | 2 | 1.07e-50 | [0.223, 0.288] |
| A → B' | grounded | 690 | 0 | 3.89e-208 | [1.000, 1.000] |
| B' → C' | gold-citation selected | 11 | 17 | 0.345 | [-0.023, 0.006] |
| B' → C' | grounded | 0 | 0 | 1 | [0.000, 0.000] |

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
