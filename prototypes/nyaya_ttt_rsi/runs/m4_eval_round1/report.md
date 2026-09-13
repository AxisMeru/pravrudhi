# Run report: m4_eval_round1

- **Checkpoint:** g0_sft_round1/final.pt (1.13B, Megatron-Core, SFT round 1)
- **Created:** 2026-09-13
- **Held-out queries:** 690

## Conditions

| Metric | M4t — 1.13B Megatron, k=4, PROMPT_CONFIG unchanged, tuned store, global selection-correct calib. | M4t_template — 1.13B Megatron, k=4, PROMPT_CONFIG unchanged, tuned store, PER-TEMPLATE calib. | M8t — 1.13B Megatron, k=8, body=200B/title=90B/budget=1400B, tuned store, global selection-correct calib. | M8t_template — 1.13B Megatron, k=8, body=200B/title=90B/budget=1400B, tuned store, PER-TEMPLATE calib. | P16f — 370M frozen, pointwise k=16, plain BM25, selection-correct calib. (pointwise_run1) | S4t_f — 370M frozen, k=4 scoring mode + BM25-rank prior, TUNED retrieval, selection-correct calib. (pointwise_run1) |
| --- | --- | --- | --- | --- | --- | --- |
| citation_recall | 0.084 (19/227) [0.054–0.127] | 0.330 (75/227) [0.272–0.394] | 0.079 (18/227) [0.051–0.122] | 0.374 (85/227) [0.314–0.439] | 0.762 (173/227) [0.703–0.813] | 0.542 (123/227) [0.477–0.605] |
| citation_precision | 0.950 (19/20) [0.764–0.991] | 0.636 (75/118) [0.546–0.717] | 0.947 (18/19) [0.754–0.991] | 0.612 (85/139) [0.529–0.688] | 0.801 (173/216) [0.743–0.849] | 0.804 (123/153) [0.734–0.859] |
| abstention_correctness | 1.000 (9/9) [0.701–1.000] | 1.000 (9/9) [0.701–1.000] | 1.000 (9/9) [0.701–1.000] | 1.000 (9/9) [0.701–1.000] | 0.778 (7/9) [0.453–0.937] | 1.000 (9/9) [0.701–1.000] |
| gold_selected | 0.681 (470/690) [0.645–0.715] | 0.758 (523/690) [0.725–0.788] | 0.678 (468/690) [0.643–0.712] | 0.771 (532/690) [0.738–0.801] | 0.370 (255/690) [0.334–0.406] | 0.178 (123/690) [0.152–0.209] |
| false_abstain_when_shown | 0.306 (208/679) [0.273–0.342] | 0.165 (112/679) [0.139–0.195] | 0.309 (210/679) [0.276–0.345] | 0.137 (93/679) [0.113–0.165] | 0.357 (214/600) [0.319–0.396] | 0.776 (527/679) [0.743–0.806] |
| law_citation_retrieval_kind_gold_selected | 0.084 (19/227) [0.054–0.127] | 0.326 (74/227) [0.268–0.389] | 0.079 (18/227) [0.051–0.122] | 0.366 (83/227) [0.306–0.430] | 0.758 (172/227) [0.698–0.809] | 0.542 (123/227) [0.477–0.605] |
| law_cite_to_title_kind_gold_selected | 1.000 (227/227) [0.983–1.000] | 0.991 (225/227) [0.968–0.998] | 0.996 (226/227) [0.975–0.999] | 0.991 (225/227) [0.968–0.998] | 0.198 (45/227) [0.152–0.255] | 0.000 (0/227) [0.000–0.017] |
| law_lookup_kind_gold_selected | 0.987 (224/227) [0.962–0.995] | 0.987 (224/227) [0.962–0.995] | 0.987 (224/227) [0.962–0.995] | 0.987 (224/227) [0.962–0.995] | 0.167 (38/227) [0.124–0.221] | 0.000 (0/227) [0.000–0.017] |
| pre_abstention_selection_accuracy | 0.845 (583/690) [0.816–0.870] | – | 0.830 (573/690) [0.801–0.857] | – | – | – |
| pre_abstention_law_citation_retrieval_kind | 0.568 (129/227) [0.503–0.631] | – | 0.524 (119/227) [0.459–0.588] | – | – | – |
| pre_abstention_law_cite_to_title_kind | 1.000 (227/227) [0.983–1.000] | – | 1.000 (227/227) [0.983–1.000] | – | – | – |
| pre_abstention_law_lookup_kind | 1.000 (227/227) [0.983–1.000] | – | 1.000 (227/227) [0.983–1.000] | – | – | – |

### Timing

| Condition | Wall time (s) | Peak VRAM (GiB) |
| --- | --- | --- |
| M4t — 1.13B Megatron, k=4, PROMPT_CONFIG unchanged, tuned store, global selection-correct calib. | 68.0 | 12.61 |
| M4t_template — 1.13B Megatron, k=4, PROMPT_CONFIG unchanged, tuned store, PER-TEMPLATE calib. | 67.8 | 12.61 |
| M8t — 1.13B Megatron, k=8, body=200B/title=90B/budget=1400B, tuned store, global selection-correct calib. | 76.7 | 14.53 |
| M8t_template — 1.13B Megatron, k=8, body=200B/title=90B/budget=1400B, tuned store, PER-TEMPLATE calib. | 71.6 | 14.53 |
| P16f — 370M frozen, pointwise k=16, plain BM25, selection-correct calib. (pointwise_run1) | 142.8 | 2.0 |
| S4t_f — 370M frozen, k=4 scoring mode + BM25-rank prior, TUNED retrieval, selection-correct calib. (pointwise_run1) | 30.9 | 2.94 |

## Paired comparisons

| a → b | Metric | b only | c only | McNemar p | Bootstrap CI |
| --- | --- | --- | --- | --- | --- |
| M4t → S4t_f | gold selected (index-exact, only where both conditions report it) | 110 | 457 | 3.23e-51 | [-0.558, -0.449] |
| M4t → S4t_f | gold-citation present (690, lenient substring match) | 110 | 230 | 6.98e-11 | [-0.225, -0.125] |
| M4t → P16f | gold selected (index-exact, only where both conditions report it) | 157 | 372 | 3.81e-21 | [-0.372, -0.251] |
| M4t → P16f | gold-citation present (690, lenient substring match) | 159 | 178 | 0.327 | [-0.080, 0.023] |
| M8t → S4t_f | gold selected (index-exact, only where both conditions report it) | 110 | 455 | 8.4e-51 | [-0.555, -0.445] |
| M8t → S4t_f | gold-citation present (690, lenient substring match) | 110 | 229 | 9.48e-11 | [-0.223, -0.123] |
| M8t → P16f | gold selected (index-exact, only where both conditions report it) | 158 | 371 | 9.02e-21 | [-0.370, -0.249] |
| M8t → P16f | gold-citation present (690, lenient substring match) | 160 | 178 | 0.355 | [-0.077, 0.026] |
| M8t → M4t | gold selected (index-exact, only where both conditions report it) | 4 | 2 | 0.688 | [-0.004, 0.010] |
| M8t → M4t | gold-citation present (690, lenient substring match) | 3 | 2 | 1 | [-0.004, 0.007] |
| M4t_template → S4t_f | gold selected (index-exact, only where both conditions report it) | 73 | 473 | 9.69e-73 | [-0.629, -0.528] |
| M4t_template → S4t_f | gold-citation present (690, lenient substring match) | 73 | 249 | 1.25e-23 | [-0.300, -0.209] |
| M4t_template → M4t | gold selected (index-exact, only where both conditions report it) | 10 | 63 | 1.55e-10 | [-0.100, -0.052] |
| M4t_template → M4t | gold-citation present (690, lenient substring match) | 8 | 64 | 5.77e-12 | [-0.104, -0.058] |
| M8t_template → S4t_f | gold selected (index-exact, only where both conditions report it) | 74 | 483 | 1.47e-74 | [-0.641, -0.542] |
| M8t_template → S4t_f | gold-citation present (690, lenient substring match) | 74 | 260 | 2.17e-25 | [-0.319, -0.223] |
| M8t_template → M8t | gold selected (index-exact, only where both conditions report it) | 2 | 66 | 1.59e-17 | [-0.114, -0.071] |
| M8t_template → M8t | gold-citation present (690, lenient substring match) | 0 | 67 | 1.36e-20 | [-0.119, -0.075] |

## Notes

- lambda=0 (no BM25-rank prior) comparison: {"M4t": {"recall": {"successes": 15, "total": 227, "rate": 0.06607929515418502, "ci_low": 0.040450020461977, "ci_high": 0.10615093881052849}, "gold_selected": {"successes": 465, "total": 690, "rate": 0.6739130434782609}}, "M8t": {"recall": {"successes": 17, "total": 227, "rate": 0.07488986784140969, "ci_low": 0.04727999054515015, "ci_high": 0.11664886803146687}, "gold_selected": {"successes": 467, "total": 690, "rate": 0.6768115942028986}}}.
- M4t GLOBAL calibration (dev n=500, k=4): lambda_prior=1.0, tau_m=-14.3491, delta_m=3.8136, balanced_accuracy=0.9050, confusion={'cite_when_shown': 286, 'abstain_when_shown': 54, 'cite_when_miss': 5, 'abstain_when_miss': 155, 'n_shown': 340, 'n_miss': 160}, abstain_on_synthetic_miss={'successes': 93, 'total': 98, 'rate': 0.9489795918367347}.
- M4t PER-TEMPLATE calibration [law_citation_retrieval] (n=153): lambda_prior=1.0, tau_m=-11.9961, delta_m=0.4205, balanced_accuracy=0.6506, confusion={'cite_when_shown': 42, 'abstain_when_shown': 25, 'cite_when_miss': 28, 'abstain_when_miss': 58, 'n_shown': 67, 'n_miss': 86}, abstain_on_synthetic_miss={'successes': 18, 'total': 34, 'rate': 0.5294117647058824}.
- M4t PER-TEMPLATE calibration [law_cite_to_title] (n=172): lambda_prior=0.0, tau_m=-13.4151, delta_m=0.0000, balanced_accuracy=1.0000, confusion={'cite_when_shown': 132, 'abstain_when_shown': 0, 'cite_when_miss': 0, 'abstain_when_miss': 40, 'n_shown': 132, 'n_miss': 40}, abstain_on_synthetic_miss={'successes': 40, 'total': 40, 'rate': 1.0}.
- M4t PER-TEMPLATE calibration [law_lookup] (n=175): lambda_prior=0.0, tau_m=-13.7889, delta_m=0.0000, balanced_accuracy=1.0000, confusion={'cite_when_shown': 141, 'abstain_when_shown': 0, 'cite_when_miss': 0, 'abstain_when_miss': 34, 'n_shown': 141, 'n_miss': 34}, abstain_on_synthetic_miss={'successes': 24, 'total': 24, 'rate': 1.0}.
- M8t GLOBAL calibration (dev n=500, k=8): lambda_prior=1.0, tau_m=-14.9725, delta_m=4.0706, balanced_accuracy=0.9149, confusion={'cite_when_shown': 290, 'abstain_when_shown': 47, 'cite_when_miss': 5, 'abstain_when_miss': 158, 'n_shown': 337, 'n_miss': 163}, abstain_on_synthetic_miss={'successes': 93, 'total': 98, 'rate': 0.9489795918367347}.
- M8t PER-TEMPLATE calibration [law_citation_retrieval] (n=153): lambda_prior=1.0, tau_m=-14.9725, delta_m=0.8161, balanced_accuracy=0.6670, confusion={'cite_when_shown': 52, 'abstain_when_shown': 13, 'cite_when_miss': 41, 'abstain_when_miss': 47, 'n_shown': 65, 'n_miss': 88}, abstain_on_synthetic_miss={'successes': 16, 'total': 34, 'rate': 0.47058823529411764}.
- M8t PER-TEMPLATE calibration [law_cite_to_title] (n=172): lambda_prior=0.0, tau_m=-13.7933, delta_m=0.0000, balanced_accuracy=1.0000, confusion={'cite_when_shown': 132, 'abstain_when_shown': 0, 'cite_when_miss': 0, 'abstain_when_miss': 40, 'n_shown': 132, 'n_miss': 40}, abstain_on_synthetic_miss={'successes': 40, 'total': 40, 'rate': 1.0}.
- M8t PER-TEMPLATE calibration [law_lookup] (n=175): lambda_prior=0.0, tau_m=-14.4437, delta_m=2.4462, balanced_accuracy=1.0000, confusion={'cite_when_shown': 140, 'abstain_when_shown': 0, 'cite_when_miss': 0, 'abstain_when_miss': 35, 'n_shown': 140, 'n_miss': 35}, abstain_on_synthetic_miss={'successes': 24, 'total': 24, 'rate': 1.0}.
- Retrieval recall@k reused from pointwise_run1 (same tuned store): plain {'4': 0.7724637681159421, '8': 0.836231884057971, '16': 0.8695652173913043, '32': 0.908695652173913}; tuned {'4': 0.9840579710144928, '8': 0.9869565217391304, '16': 0.9869565217391304, '32': 0.9869565217391304}.
