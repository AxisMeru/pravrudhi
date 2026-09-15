# P2a inventory/exclude evidence — Track B content read, 2026-09-15 14:58 BST

`prabhasa-nyaya` `assistant/trackA/p2a-inventory-exclude` @ `206a780` (`scripts/p2a_yield_gate.py` inventory + exclude, `research/gates/P2a/{inventory,exclusions}.json`) against the frozen prereg `4aca28d`.

## Numbers: all confirmed
Independent recount by spikeC-inventory-verify (Sonnet, seat 2; `wc`/`jq`/`comm`/`sha256sum`, never d0's script), report at `docs/reviews/spikes/trackb-p2a-inventory/verify.md`: all 17 figures match (IPC 557, BNS 358, law_v3 6,206, CaseHOLD 5,314, held-out 690 / 236 provision groups, LegalBench 3,472 over 30 files = 75 + 3,397 after the `rule_qa.train.tsv` header quirk, IL-TUR 13,019 with 53 exact duplicates, law_apply 33, join 0 matches); the four hashed source files re-hash identically. Reviewer: re-ran both subcommands at `206a780` with the two root flags and diffed: `exclusions.json` identical; `inventory.json` identical except the checker-id count (None in my unbuilt checkout). The zero-match join is genuine: both files use the same `(act, section)` format and the law_v3 split is provision-disjoint (checked by me).

## Conformance against the prereg's Leakage section: HELD on four items
1. **`code_revision` wrong** in both JSON files: records `1e8d43a` (main) while the evidence is committed at `206a780`. The field is hand-supplied; regenerate at the final branch SHA and record that SHA, or record the script tree's own hash. Evidence with a revision that does not identify its code cannot be frozen.
2. **Separate hashing missing**: every family hashes one concatenated `prompt\ntarget` (or a joined CSV row). The prereg requires ids, source ids, prompt/facts and target hashed separately, "checking only prompt-target pairs is insufficient". Add per-field hash sets (id, source_id, prompt, target) for every family that has the fields, and per-column for CSV/TSV families.
3. **`instruct_v1` family absent** from `exclude` though named in the Leakage section. Add it (path in `prabhasa-samskrutam/data/instruct/` per the asset inventory) or record it as MISSING with the path tried, never silently omit.
4. **Lineage/template-family groups**: only the law_v3 × held-out provision join exists. Now: add `source_id`/`provenance` group sets for held-out and law_v3 (the fields exist). Template-family exclusion is applied at `replay` when candidate training records exist; record that deferral explicitly in `exclusions.json` (`template_family_exclusion: "deferred to replay"`), not implied.

## Reviewer amendments to the prereg (decisions, recorded here, folded into the prereg at its next freeze)
- **(act, section, version)**: the data carries no `version` field; version is the corpus file's sha256 (already pinned in `inventory.json`). The `(act, section)` join with the corpus hash pinned satisfies the rule.
- **`law_v3_train_refit.jsonl`** (6,206 rows, sibling of the inventoried file) must be inventoried as a source or declared out of scope with the reason; unlisted files beside a listed one are a leakage path.

Signature: **HELD** pending items 1–4 (numbers need no rework). Re-check: regenerate at the revised SHA; I diff against my scratch run and sign.

## SIGNED — 2026-09-15 15:03 BST, code `413bf55`, evidence `d7ce664`

Re-check by the reviewer from a throwaway worktree at `d7ce664`: both subcommands re-run with the root flags; `exclusions.json` identical to the committed file apart from timestamp; `inventory.json` identical apart from timestamp and the checker-id count (None in my unbuilt checkout). All four held items are in: (1) `code_revision` = `413bf55`, the commit of the code that produced the evidence (two-commit pattern accepted); (2) per-field hashing (`_hash_fields_jsonl` for id/source_id/prompt-or-facts/target on held-out, IL-TUR, law_apply, instruct_v1; per-column per task file for LegalBench and CaseHOLD); (3) `instruct_v1` family present, 25,240 + 515 val rows, absent id/source_id fields reported as count 0 with a note; (4) `lineage_groups` for held-out (228 source_id / 24 provenance / 236 act-section) and law_v3 (2,043 / 30), `template_family_exclusion` recorded as DEFERRED TO replay with reason. Amendment (b) in: `law_v3_raw_refit` inventoried as a distinct source (6,206 rows, distinct sha256). Amendment (a) (version = corpus sha) stands as recorded above.

**Content signature: SIGNED at `d7ce664`.** Lead merges on Track A's shape signature at the same SHA. Next in the gate: trackB's deterministic full-IR batch (D_min 300) and the teacher batch (T ≥ 200, F = 5 per id); `replay` and `decide` remain to be built.

## D_min methodology ruling (prereg amendment (c)), 2026-09-15 15:05 BST

Questions from trackB via the lead. Rulings:

1. **What counts toward D (deterministic full-IR).** A record counts toward D iff it has controlled authored facts, a complete typed IR in the `nyaya-law-v1` line grammar, and a **checker-computed verdict** through `check(answer, contract_id)`. The verdict may be `grounded` (control slice), `flagged` with the named omitted element (omission slice), or refuted (denial slice where the Contract carries one). All three are full-IR: the model must learn to emit the typed omission and the refutation as first-class outputs, not only the happy path. What does NOT count toward D: typed abstentions with no contract application (retrieval-only or unknown-provision), partial records with masked proof fields, and any record whose verdict was not recomputed by the checker at replay. Those go to P or R. **Mix constraint:** control records ≥ 40 % of D, and every id has ≥ 10 control records; omission/denial slices are capped at the number of elements (+1 for a denial) per scenario.

2. **Distinctness.** The unit of counting is the **scenario** (a fact pattern with its own parties, conduct, object/amount, setting), not the record. Rephrasings, option permutations, name swaps and re-orderings of one scenario are one scenario (lineage group = scenario id, recorded per the leakage rules). Subset-of-elements variants are slices of one scenario, not new scenarios. So the per-id floor is **≥ 10 materially distinct control scenarios per id** (14 × 10 = 140 controls), each yielding its control record plus up to n_elements omission slices and a denial slice where the Contract has one; D ≥ 300 then follows from ≈ 2–3 records per scenario without padding. A generator that reaches 300 by permuting a handful of scenarios fails the gate.

3. **Provenance per record**: `scenario_id`, `contract_id`, `slice` (`control` | `omit:<element>` | `deny:<denial>`), `assertions` (element → Met), `expected_verdict`, the REG raw output and the binary sha256; the batch file is immutable and replayed by `replay`.

Yes to trackB's read on both, with the mix constraint and the scenario-floor made explicit. Release trackB to build the generator and a first per-id batch; I read the first batch for scenario distinctness before it scales.

## Amendment (d) — mix rule and rephrasing guard, 2026-09-15 15:40 BST

Amendment (c)'s "control ≥ 40 % of D" conflicts with its own instruction to emit one omission slice per element: with n elements plus an optional denial, a scenario yields 1 control in (n + 2) records, so the ratio is structurally ≈ 25–30 % (batch-02: 56 controls in 208 = 26.9 %). The floor is replaced: **control ≥ 25 % of D, ≥ 10 distinct control scenarios per id, omission slices ≤ n_elements per scenario, denial slices ≤ 1 per scenario.** The scenario floor, not the ratio, is what guarantees coverage.

Rephrasing guard (from the pace ruling): measured on batch-02, Jaccard over content tokens of narrative + element facts runs 0.45–0.59 between distinct scenarios because element facts share the element wording, so that variant is unusable. The guard is **narrative-only** Jaccard (content tokens, stop-words removed) against every existing scenario of the same contract_id: **≥ 0.40 rejected as a rephrasing; 0.30–0.39 flagged for the reviewer's read; < 0.30 accepted.** Batch-02's maximum is 0.35 (4 pairs in the flagged band, 0 rejected). The generator records the maximum overlap and the nearest scenario_id in provenance.
