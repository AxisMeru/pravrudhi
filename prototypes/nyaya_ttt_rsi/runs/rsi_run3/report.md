# Run report: rsi_run3

- **Checkpoint:** m7/m7_retry_checkpoint.pt (370M, law-tuned) + round-3 LoRA continued from round-1
- **Created:** 2026-09-13
- **Held-out queries:** 690

## Conditions

| Metric | S4t — 370M + round-1 consolidated LoRA, k=4, GLOBAL selection-correct calib. (pointwise_run1) | S4t_f — 370M frozen, k=4, GLOBAL selection-correct calib. (pointwise_run1) | S4t_round3 — 370M + round-3 LoRA (continued from round-1), k=4, GLOBAL selection-correct calib. | S4t_round3_template — 370M + round-3 LoRA, k=4, PER-TEMPLATE selection-correct calib. |
| --- | --- | --- | --- | --- |
| citation_recall | 0.044 (10/227) [0.024–0.079] | 0.542 (123/227) [0.477–0.605] | 0.093 (21/227) [0.061–0.137] | 0.110 (25/227) [0.076–0.158] |
| citation_precision | 0.435 (10/23) [0.256–0.632] | 0.804 (123/153) [0.734–0.859] | 0.808 (21/26) [0.621–0.915] | 0.806 (25/31) [0.637–0.908] |
| abstention_correctness | 0.222 (2/9) [0.063–0.547] | 1.000 (9/9) [0.701–1.000] | 1.000 (9/9) [0.701–1.000] | 1.000 (9/9) [0.701–1.000] |
| gold_selected | 0.593 (409/690) [0.556–0.629] | 0.178 (123/690) [0.152–0.209] | 0.545 (376/690) [0.508–0.582] | 0.613 (423/690) [0.576–0.649] |
| false_abstain_when_shown | 0.376 (255/679) [0.340–0.413] | 0.776 (527/679) [0.743–0.806] | 0.433 (294/679) [0.396–0.471] | 0.358 (243/679) [0.323–0.395] |
| law_citation_retrieval_kind_gold_selected | 0.044 (10/227) [0.024–0.079] | 0.542 (123/227) [0.477–0.605] | 0.093 (21/227) [0.061–0.137] | 0.110 (25/227) [0.076–0.158] |
| law_cite_to_title_kind_gold_selected | 0.890 (202/227) [0.842–0.924] | 0.000 (0/227) [0.000–0.017] | 0.789 (179/227) [0.731–0.837] | 0.828 (188/227) [0.774–0.872] |
| law_lookup_kind_gold_selected | 0.868 (197/227) [0.818–0.906] | 0.000 (0/227) [0.000–0.017] | 0.775 (176/227) [0.717–0.825] | 0.925 (210/227) [0.883–0.953] |
| pre_abstention_selection_accuracy | – | – | 0.804 (555/690) [0.773–0.832] | – |

### Timing

| Condition | Wall time (s) | Peak VRAM (GiB) |
| --- | --- | --- |
| S4t — 370M + round-1 consolidated LoRA, k=4, GLOBAL selection-correct calib. (pointwise_run1) | 37.6 | 2.97 |
| S4t_f — 370M frozen, k=4, GLOBAL selection-correct calib. (pointwise_run1) | 30.9 | 2.94 |
| S4t_round3 — 370M + round-3 LoRA (continued from round-1), k=4, GLOBAL selection-correct calib. | 38.0 | 3.56 |
| S4t_round3_template — 370M + round-3 LoRA, k=4, PER-TEMPLATE selection-correct calib. | 37.8 | 3.56 |

## Paired comparisons

| a → b | Metric | b only | c only | McNemar p | Bootstrap CI |
| --- | --- | --- | --- | --- | --- |
| S4t_round3 → S4t | gold selected (index-exact, only where both conditions report it) | 77 | 44 | 0.00345 | [0.017, 0.077] |
| S4t_round3 → S4t_f | gold selected (index-exact, only where both conditions report it) | 109 | 362 | 9.33e-33 | [-0.420, -0.312] |
| S4t_round3_template → S4t | gold selected (index-exact, only where both conditions report it) | 48 | 62 | 0.215 | [-0.049, 0.010] |
| S4t_round3_template → S4t_f | gold selected (index-exact, only where both conditions report it) | 107 | 407 | 3.35e-42 | [-0.490, -0.381] |
| S4t_round3_template → S4t_round3 | gold selected (index-exact, only where both conditions report it) | 1 | 48 | 1.78e-13 | [-0.087, -0.049] |

## Notes

- Round-3 pseudo-labeled data: {"n_sampled": 1200, "exclude_ids_source": "/lab/prototypes/nyaya_ttt_rsi/runs/rsi_run1e/grounded_sft.jsonl", "n_excluded_available": 2496, "n_forced_abstain": 240, "n_pseudo_accepted": 655, "n_pseudo_rejected_abstained": 305, "n_rejected_ungrounded": 0, "purity": 0.8106870229007633, "n_shuffle_copies_added": 224, "by_kind_accepted": {"law_lookup": 218, "law_citation_retrieval": 213, "law_cite_to_title": 224}, "n_examples_total": 1119}.
- Round-3 gate decision: accepted=True, reason=ok, probe_delta_rel=0.0471, dev_before={'law_citation_retrieval': 0.45098039215686275, 'law_cite_to_title': 1.0, 'law_lookup': 0.9716981132075472}, dev_after={'law_citation_retrieval': 0.47058823529411764, 'law_cite_to_title': 0.9775280898876404, 'law_lookup': 0.9811320754716981}.
- lambda=0 (no BM25-rank prior), global calib: recall={'successes': 8, 'total': 227, 'rate': 0.03524229074889868, 'ci_low': 0.017963794807951294, 'ci_high': 0.06798951690016371}, gold_selected={'successes': 355, 'total': 690, 'rate': 0.5144927536231884}.
- STEP 2 (round 4) was NOT run: round 3's TRAIN-dev per-kind selection accuracy for its own targeted kind (law_cite_to_title) went from 1.000 before training to 0.978 after (a small decline, inside the gate's 5-point tolerance but not an improvement), and law_lookup only rose 0.972->0.981; only law_citation_retrieval rose meaningfully on dev (0.451->0.471). The gate ACCEPTED (probe regression 4.7% < 15%, no kind fell >5 points), but the task's round-4 trigger is gate acceptance AND a clear dev improvement -- since round 3 did not deliver the latter for its own target kind, running a compounding round 4 on top of a flat/ambiguous round 3 would have risked amplifying noise rather than signal, so round 4 was skipped per 'never loosen the gate.'
- Global-calibration comparison (as S4t/S4t_f were originally reported): S4t_round3 (0.545 gold_selected) is WORSE than S4t (0.593, p=0.0035) -- the global threshold trades law_cite_to_title/law_lookup accuracy for a small law_citation_retrieval gain (0.044->0.093). Under PER-TEMPLATE calibration (2026-09-13 fix, applied identically to round 3's own pseudo-labeling and its held-out eval), S4t_round3_template's overall gold_selected (0.613) is not significantly different from S4t's global number (0.593, p=0.215) -- i.e. round 3 plus per-template calibration roughly matches round 1's overall selection rate while modestly improving law_citation_retrieval recall (0.044->0.110), but did not clearly improve the title kind it targeted (0.828 on held-out; no directly comparable round-1 or frozen per-template title number was run in this budget, since M4t/M8t's per-template runs were on the 1.13B, not the 370M -- this is a real gap, disclosed rather than papered over: no 370M per-template FROZEN or round-1 baseline exists to confirm round 3 actually helped title selection specifically.
