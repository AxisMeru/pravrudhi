# P2a deterministic batch-02 — Track B content read, 2026-09-15 15:45 BST

`prabhasa-nyaya` `trackB/t5b-deterministic-records` @ `6d1d163`: 208 records, 56 scenarios (4 per id), 140 omission, 12 denial, 56 control; 208/208 REG. Read by spikeD-batch02-read (Sonnet, seat 2; report `docs/reviews/spikes/trackb-p2a-inventory/batch02-read.md`) plus my mechanical overlap check; ruling mine.

## Passes
- **Distinctness: PASS.** 0 rephrasing pairs in 56 scenarios; narrative-only Jaccard max 0.35 (4 pairs in the 0.30–0.39 human-read band, all read, all distinct: different relatives/conduct/motive, different crimes/cities). 0 mis-filed scenarios; `s_415d_invoice_tanya` is a close call accepted under `damaging_act` (payment released to a third party, not delivered to the deceiver).
- Chained-element fix for 405 use/disposal `omit_1`/`omit_2`: correct.

## HELD: 34 of 140 omission slices leak the dropped element (8 ids). Two classes, two different fixes.

**Class A, wording leaks (rewrite the fact or narrative; slice stays):**
- `405m_maintfund_harish omit_0`: narrative "as treasurer"; `405w_cashcounter_alka`, `405w_toolshed_prakash omit_0`: "managed by". Role tags presuppose entrustment; use the neutral scene-setter the other scenarios use.
- `415p` (tara, yash, devika) `omit_0`: "as though it were genuine / accident-free / a natural diamond" restates the falsity; write the inducement fact without the counterfactual ("Nikhil paid the full price for the watch").
- `bns85` all four `omit_0`: the narrative names the relationship ("her husband's brother", "her mother-in-law", "her husband"); use names only.
- `bns46_instigation` all four `omit_0`: the retained `F_el1` says "knowing the claim was false / forged / staged"; the abetted-is-offence fact must state the offence generically ("the arrest of an innocent man would be an offence if done by a capable person with the abettor's intention") without the falsity.

**Class B, definitional dependencies (no wording fixes it; the slice is incoherent and must not be emitted):** a retained element that by the statute's own structure presupposes the dropped one.
- `405 use/disposal`: "in violation of a direction/contract governing how the trust is discharged" presupposes the entrustment → `omit_0` is not emittable.
- `415 damaging act`: "which act or omission causes damage" presupposes the induced act → `omit_1` not emittable.
- `182 misdirected act`: "ought not to do if the true facts were known" presupposes that the information was false → `omit_0` not emittable.
- `416`: as authored, el0 ("deceived X into believing she was Y, thereby cheating") and el1 ("pretended to be Y") are one act from two angles, so both directions leak. This one is an authoring fix, not a skip: the `cheats` fact must state the cheating outcome (what X was induced to deliver or permit) and the `personation` fact the identity pretence; then both omissions are coherent.
Rule for the generator: each Contract carries a declared dependency list (`el2→el0` for 405u, `el2→el1` for 415d, `el2→el0` for 182a); an omission direction whose dropped element is a prerequisite of a retained element is skipped and recorded in provenance as `skipped: dependency(<retained>→<dropped>)`. Amendment (c)'s cap "≤ n_elements omission slices per scenario" reads "≤ omittable elements".

Ratio and floors: with amendment (d) (control ≥ 25 %, ≥ 10 control scenarios per id) batch-02 is on track (26.9 %; 4 scenarios per id so far).

**Status: HELD** pending the Class A rewrites, the 416 re-authoring, and the dependency-skip in the generator; regenerate batch-02 at the same scenarios. Then scale to ≥ 10 per id under the pace ruling (author provenance, narrative-only rephrasing guard ≥ 0.40 reject / 0.30–0.39 flag, ≤ 12 per id). I re-read the regenerated batch-02 leak table before scale-up.
