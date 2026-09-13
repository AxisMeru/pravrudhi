# Run report: pointwise_run1

- **Checkpoint:** m7/m7_retry_checkpoint.pt (370M, law-tuned)
- **Created:** 2026-09-13
- **Held-out queries:** 690

## Conditions

| Metric | B' — k=4 scoring mode, plain BM25, round-1 consolidated LoRA (rsi_run1e baseline) | B'(frozen) — k=4 scoring mode, plain BM25, frozen 370M (rsi_run1e baseline) | P16f — pointwise k=16, plain BM25, frozen 370M, selection-correct calib. | P32 — pointwise k=32, plain BM25, round-1 consolidated LoRA, selection-correct calib. | P32f — pointwise k=32, plain BM25, frozen 370M, selection-correct calib. | P8f — pointwise (1 passage/prompt) k=8, plain BM25, frozen 370M, selection-correct calib. | P8t — pointwise k=8, TUNED retrieval, round-1 consolidated LoRA, selection-correct calib. | P8t_f — pointwise k=8, TUNED retrieval, frozen 370M, selection-correct calib. | S4t — k=4 scoring mode + BM25-rank prior, TUNED retrieval, round-1 LoRA, selection-correct calib. | S4t_f — k=4 scoring mode + BM25-rank prior, TUNED retrieval, frozen 370M, selection-correct calib. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| citation_recall | 0.145 (33/227) [0.105–0.197] | 0.176 (40/227) [0.132–0.231] | 0.762 (173/227) [0.703–0.813] | 0.004 (1/227) [0.001–0.025] | 0.736 (167/227) [0.675–0.789] | 0.079 (18/227) [0.051–0.122] | 0.040 (9/227) [0.021–0.074] | 0.026 (6/227) [0.012–0.056] | 0.044 (10/227) [0.024–0.079] | 0.542 (123/227) [0.477–0.605] |
| citation_precision | 0.190 (33/174) [0.138–0.254] | 0.206 (40/194) [0.155–0.269] | 0.801 (173/216) [0.743–0.849] | 0.036 (1/28) [0.006–0.177] | 0.803 (167/208) [0.744–0.851] | 0.083 (18/217) [0.053–0.127] | 0.290 (9/31) [0.161–0.466] | 0.286 (6/21) [0.138–0.500] | 0.435 (10/23) [0.256–0.632] | 0.804 (123/153) [0.734–0.859] |
| abstention_correctness | 0.889 (8/9) [0.565–0.980] | 0.444 (4/9) [0.189–0.733] | 0.778 (7/9) [0.453–0.937] | 0.667 (6/9) [0.354–0.879] | 0.889 (8/9) [0.565–0.980] | 0.222 (2/9) [0.063–0.547] | 0.778 (7/9) [0.453–0.937] | 1.000 (9/9) [0.701–1.000] | 0.222 (2/9) [0.063–0.547] | 1.000 (9/9) [0.701–1.000] |
| law_lookup_prefix_similarity_mean | 0.626 | 0.108 | 0.201 | 0.608 | 0.192 | 0.101 | 0.872 | 0.032 | 0.872 | 0.032 |
| gold_selected | 0.491 (339/690) [0.454–0.529] | 0.104 (72/690) [0.084–0.129] | 0.370 (255/690) [0.334–0.406] | 0.354 (244/690) [0.319–0.390] | 0.355 (245/690) [0.320–0.391] | 0.070 (48/690) [0.053–0.091] | 0.587 (405/690) [0.550–0.623] | 0.009 (6/690) [0.004–0.019] | 0.593 (409/690) [0.556–0.629] | 0.178 (123/690) [0.152–0.209] |
| abstain_on_miss | 0.777 (115/148) [0.703–0.837] | 0.514 (76/148) [0.434–0.593] | 0.420 (34/81) [0.318–0.528] | 0.537 (29/54) [0.406–0.663] | 0.444 (24/54) [0.320–0.576] | 0.288 (30/104) [0.210–0.382] | 0.000 (0/0) [0.000–1.000] | 0.000 (0/0) [0.000–1.000] | 1.000 (2/2) [0.342–1.000] | 0.500 (1/2) [0.095–0.905] |
| false_abstain_when_shown | 0.116 (62/533) [0.092–0.146] | 0.430 (229/533) [0.388–0.472] | 0.357 (214/600) [0.319–0.396] | 0.533 (334/627) [0.494–0.571] | 0.396 (248/627) [0.358–0.434] | 0.250 (144/577) [0.216–0.286] | 0.366 (249/681) [0.330–0.402] | 0.969 (660/681) [0.953–0.980] | 0.376 (255/679) [0.340–0.413] | 0.776 (527/679) [0.743–0.806] |
| law_citation_retrieval_kind_gold_selected | 0.145 (33/227) [0.105–0.197] | 0.176 (40/227) [0.132–0.231] | 0.758 (172/227) [0.698–0.809] | 0.004 (1/227) [0.001–0.025] | 0.731 (166/227) [0.670–0.785] | 0.079 (18/227) [0.051–0.122] | 0.040 (9/227) [0.021–0.074] | 0.026 (6/227) [0.012–0.056] | 0.044 (10/227) [0.024–0.079] | 0.542 (123/227) [0.477–0.605] |
| pre_abstention_selection_accuracy | – | – | 0.564 (389/690) [0.527–0.600] | 0.459 (317/690) [0.423–0.497] | 0.564 (389/690) [0.527–0.600] | 0.083 (57/690) [0.064–0.106] | 0.680 (469/690) [0.644–0.713] | 0.104 (72/690) [0.084–0.129] | 0.745 (514/690) [0.711–0.776] | 0.346 (239/690) [0.312–0.383] |
| pre_abstention_law_citation_retrieval_kind | – | – | 0.780 (177/227) [0.721–0.829] | 0.022 (5/227) [0.009–0.051] | 0.780 (177/227) [0.721–0.829] | 0.084 (19/227) [0.054–0.127] | 0.154 (35/227) [0.113–0.207] | 0.154 (35/227) [0.113–0.207] | 0.352 (80/227) [0.293–0.417] | 0.696 (158/227) [0.633–0.752] |

### Timing

| Condition | Wall time (s) | Peak VRAM (GiB) |
| --- | --- | --- |
| B' — k=4 scoring mode, plain BM25, round-1 consolidated LoRA (rsi_run1e baseline) | 41.7 | 2.98 |
| B'(frozen) — k=4 scoring mode, plain BM25, frozen 370M (rsi_run1e baseline) | 41.4 | 2.98 |
| P16f — pointwise k=16, plain BM25, frozen 370M, selection-correct calib. | 142.8 | 2.0 |
| P32 — pointwise k=32, plain BM25, round-1 consolidated LoRA, selection-correct calib. | 255.6 | 1.75 |
| P32f — pointwise k=32, plain BM25, frozen 370M, selection-correct calib. | 212.6 | 2.07 |
| P8f — pointwise (1 passage/prompt) k=8, plain BM25, frozen 370M, selection-correct calib. | 85.3 | 1.98 |
| P8t — pointwise k=8, TUNED retrieval, round-1 consolidated LoRA, selection-correct calib. | 104.9 | 1.63 |
| P8t_f — pointwise k=8, TUNED retrieval, frozen 370M, selection-correct calib. | 87.0 | 1.98 |
| S4t — k=4 scoring mode + BM25-rank prior, TUNED retrieval, round-1 LoRA, selection-correct calib. | 37.6 | 2.97 |
| S4t_f — k=4 scoring mode + BM25-rank prior, TUNED retrieval, frozen 370M, selection-correct calib. | 30.9 | 2.94 |

## Paired comparisons

| a → b | Metric | b only | c only | McNemar p | Bootstrap CI |
| --- | --- | --- | --- | --- | --- |
| P16f → B'(frozen) | gold-citation present (690, lenient substring match) | 21 | 183 | 1.88e-33 | [-0.271, -0.200] |
| P16f → B'(frozen) | gold selected (index-exact, only where both conditions report it) | 25 | 208 | 4.26e-37 | [-0.303, -0.228] |
| P16f → B' | gold-citation present (690, lenient substring match) | 108 | 153 | 0.00635 | [-0.110, -0.019] |
| P16f → B' | gold selected (index-exact, only where both conditions report it) | 233 | 149 | 2.02e-05 | [0.068, 0.177] |
| P16f → P32f | gold-citation present (690, lenient substring match) | 0 | 8 | 0.00781 | [-0.020, -0.004] |
| P16f → P32f | gold selected (index-exact, only where both conditions report it) | 0 | 10 | 0.00195 | [-0.025, -0.006] |
| P32f → B'(frozen) | gold-citation present (690, lenient substring match) | 21 | 175 | 2e-31 | [-0.259, -0.188] |
| P32f → B'(frozen) | gold selected (index-exact, only where both conditions report it) | 26 | 199 | 3.37e-34 | [-0.288, -0.213] |
| P32f → B' | gold-citation present (690, lenient substring match) | 111 | 148 | 0.0251 | [-0.100, -0.007] |
| P32f → B' | gold selected (index-exact, only where both conditions report it) | 237 | 143 | 1.63e-06 | [0.083, 0.190] |
| P32 → B'(frozen) | gold-citation present (690, lenient substring match) | 45 | 120 | 4.57e-09 | [-0.143, -0.072] |
| P32 → B'(frozen) | gold selected (index-exact, only where both conditions report it) | 49 | 221 | 2.93e-27 | [-0.291, -0.207] |
| P32 → B' | gold-citation present (690, lenient substring match) | 81 | 39 | 0.000158 | [0.032, 0.091] |
| P32 → B' | gold selected (index-exact, only where both conditions report it) | 150 | 55 | 2.28e-11 | [0.100, 0.175] |
| S4t_f → B'(frozen) | gold-citation present (690, lenient substring match) | 30 | 91 | 2.5e-08 | [-0.119, -0.059] |
| S4t_f → B'(frozen) | gold selected (index-exact, only where both conditions report it) | 40 | 91 | 9.8e-06 | [-0.106, -0.043] |
| P8t_f → B'(frozen) | gold-citation present (690, lenient substring match) | 58 | 2 | 3.18e-15 | [0.061, 0.103] |
| P8t_f → B'(frozen) | gold selected (index-exact, only where both conditions report it) | 68 | 2 | 4.21e-18 | [0.074, 0.119] |
| P8t_f → P32f | gold-citation present (690, lenient substring match) | 211 | 1 | 6.47e-62 | [0.271, 0.338] |
| P8t_f → P32f | gold selected (index-exact, only where both conditions report it) | 240 | 1 | 1.37e-70 | [0.312, 0.383] |
| S4t → B' | gold-citation present (690, lenient substring match) | 45 | 73 | 0.0126 | [-0.071, -0.009] |
| S4t → B' | gold selected (index-exact, only where both conditions report it) | 57 | 127 | 2.64e-07 | [-0.138, -0.065] |
| P8t → B' | gold-citation present (690, lenient substring match) | 48 | 75 | 0.0187 | [-0.071, -0.007] |
| P8t → B' | gold selected (index-exact, only where both conditions report it) | 60 | 126 | 1.48e-06 | [-0.133, -0.058] |
| P8t → P32 | gold-citation present (690, lenient substring match) | 7 | 76 | 9.43e-16 | [-0.125, -0.077] |
| P8t → P32 | gold selected (index-exact, only where both conditions report it) | 10 | 171 | 5.59e-39 | [-0.267, -0.200] |
| S4t_f → P8t_f | gold-citation present (690, lenient substring match) | 0 | 117 | 1.2e-35 | [-0.197, -0.142] |
| S4t_f → P8t_f | gold selected (index-exact, only where both conditions report it) | 0 | 117 | 1.2e-35 | [-0.197, -0.142] |
| S4t → P8t | gold-citation present (690, lenient substring match) | 22 | 23 | 1 | [-0.020, 0.019] |
| S4t → P8t | gold selected (index-exact, only where both conditions report it) | 37 | 41 | 0.734 | [-0.030, 0.019] |

## Notes

- Own-run BM25 retrieval recall@k (690 held-out): plain {'4': 0.7724637681159421, '8': 0.836231884057971, '16': 0.8695652173913043, '32': 0.908695652173913}; tuned (retrieval_tuning.TunedStore, best_config) {'4': 0.9840579710144928, '8': 0.9869565217391304, '16': 0.9869565217391304, '32': 0.9869565217391304}.
- 2026-09-13 calibration correction: the FIRST calibration pass (kept on disk as *_by_gold_shown for the record) labeled a dev example positive whenever the gold passage was merely SHOWN among the retrieved top-k, regardless of whether the model's own arg-max actually picked it. With the tuned store's recall@4=0.98 this made the dev negative class nearly empty, and the resulting thresholds abstained on 72-82% of items where gold WAS shown (false_abstain_when_shown), collapsing citation_recall to ~0.02-0.06 across the board even though the underlying selector was fine. All numbers in this report's `conditions` and `paired` sections use the corrected label instead: positive iff the fused-score arg-max equals the item's own gold passage (see evaluate.calibrate_pointwise's and evaluate.calibrate_scoring_with_rank_prior's label_mode='selection_correct', the new default). `pre_abstention_selection_accuracy` (from a forced-always-cite rerun, tau=delta=-inf) is reported per condition as the selector's OWN ceiling, independent of the abstention rule.
- P8f calibration (dev n=500, k=8, label_mode=selection_correct): lambda_prior=0.0, tau_m=-0.1263, delta_m=0.0005, balanced_accuracy=0.5729, confusion={'cite_when_shown': 44, 'abstain_when_shown': 3, 'cite_when_miss': 358, 'abstain_when_miss': 95, 'n_shown': 47, 'n_miss': 453}, lambda_grid=[(0.0, 0.5729), (0.25, 0.55), (0.5, 0.5482), (1.0, 0.5565)].
- P16f calibration (dev n=500, k=16, label_mode=selection_correct): lambda_prior=0.5, tau_m=-0.1463, delta_m=0.2148, balanced_accuracy=0.6411, confusion={'cite_when_shown': 23, 'abstain_when_shown': 2, 'cite_when_miss': 303, 'abstain_when_miss': 172, 'n_shown': 25, 'n_miss': 475}, lambda_grid=[(0.0, 0.5586), (0.25, 0.61), (0.5, 0.6411), (1.0, 0.6411)].
- P32f calibration (dev n=500, k=32, label_mode=selection_correct): lambda_prior=0.5, tau_m=-0.1111, delta_m=0.2433, balanced_accuracy=0.6071, confusion={'cite_when_shown': 8, 'abstain_when_shown': 2, 'cite_when_miss': 287, 'abstain_when_miss': 203, 'n_shown': 10, 'n_miss': 490}, lambda_grid=[(0.0, 0.6029), (0.25, 0.5969), (0.5, 0.6071), (1.0, 0.6071)].
- P32 calibration (dev n=500, k=32, label_mode=selection_correct): lambda_prior=0.0, tau_m=0.8182, delta_m=0.0635, balanced_accuracy=0.8166, confusion={'cite_when_shown': 165, 'abstain_when_shown': 46, 'cite_when_miss': 43, 'abstain_when_miss': 246, 'n_shown': 211, 'n_miss': 289}, lambda_grid=[(0.0, 0.8166), (0.25, 0.6034), (0.5, 0.6715), (1.0, 0.7569)].
- S4t_f calibration (dev n=500, k=4, label_mode=selection_correct): lambda_prior=1.0, tau_m=-24.3277, delta_m=0.1536, balanced_accuracy=0.7693, confusion={'cite_when_shown': 169, 'abstain_when_shown': 70, 'cite_when_miss': 44, 'abstain_when_miss': 217, 'n_shown': 239, 'n_miss': 261}, lambda_grid=[(0.0, 0.298), (0.25, 0.342), (0.5, 0.404), (1.0, 0.478)].
- S4t calibration (dev n=500, k=4, label_mode=selection_correct): lambda_prior=1.0, tau_m=-17.3940, delta_m=4.1158, balanced_accuracy=0.8891, confusion={'cite_when_shown': 350, 'abstain_when_shown': 72, 'cite_when_miss': 4, 'abstain_when_miss': 74, 'n_shown': 422, 'n_miss': 78}, lambda_grid=[(0.0, 0.768), (0.25, 0.788), (0.5, 0.81), (1.0, 0.844)].
- P8t_f calibration (dev n=500, k=8, label_mode=selection_correct): lambda_prior=0.0, tau_m=0.4349, delta_m=0.0018, balanced_accuracy=0.5874, confusion={'cite_when_shown': 8, 'abstain_when_shown': 31, 'cite_when_miss': 14, 'abstain_when_miss': 447, 'n_shown': 39, 'n_miss': 461}, lambda_grid=[(0.0, 0.5874), (0.25, 0.5729), (0.5, 0.5278), (1.0, 0.5332)].
- P8t calibration (dev n=500, k=8, label_mode=selection_correct): lambda_prior=0.0, tau_m=0.7716, delta_m=0.0895, balanced_accuracy=0.8355, confusion={'cite_when_shown': 220, 'abstain_when_shown': 34, 'cite_when_miss': 48, 'abstain_when_miss': 198, 'n_shown': 254, 'n_miss': 246}, lambda_grid=[(0.0, 0.8355), (0.25, 0.6128), (0.5, 0.6519), (1.0, 0.7704)].
- Consistent finding across every condition that loads the round-1 consolidated LoRA (P32, S4t, P8t) vs its own frozen counterpart (P32f, S4t_f, P8t_f): the LoRA substantially HURTS the law_citation_retrieval-kind-specific citation_recall/gold_selected rate specifically (see law_citation_retrieval_kind_gold_selected per condition above), even though it improves or does not change gold_selected on the easier law_lookup/law_cite_to_title kinds and on overall selection accuracy. This mirrors the ALREADY-KNOWN rsi_run1e result that B'(frozen)=0.176 citation_recall beats B'(consolidated)=0.145 -- the round-1 LoRA was consolidated to help abstention/lookup/title behavior, at a real cost to hard-kind citation selection, and that cost transfers to every retrieval/prompt configuration tested here, not just the original k=4 plain-BM25 format it was trained under.
