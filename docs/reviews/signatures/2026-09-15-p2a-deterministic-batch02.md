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

## Re-read @ `0447992` / `ecb3595` — CLEARED for scale-up with 10 wording fixes, 2026-09-15 16:01 BST

`ecb3595` differs from `0447992` only in the generator (dependency-table name assertion); records byte-identical (verified). Re-read by spikeD2 (report `batch02b-read.md`) over all 128 emitted omission slices plus my own check: Class A rewrites applied; the 12 dependency skips are the three ruled directions and are logged; 416 re-authored so both omission directions are coherent; distinctness unchanged (56 scenarios, 0 rephrasings); bns69 jobpromo/scholarship pair read by me: distinct (different inducement and parties).

Residual leaks, 10, all wording (no new dependency class):
1. `405m_maintfund_harish omit_0`: "instead of depositing them" restates entrustment-for-deposit → "spent a portion of the funds on his own personal debts".
2–5. `415p` all four `omit_0`: "done fraudulently" in the inducement fact. "fraudulently" lexically carries deceit; the element is "fraudulently OR dishonestly induces", so the omit_0 fact uses the other disjunct: "dishonestly, with intent to secure payment".
6–9. `46c` all four `omit_0`: narrative "plan involving <conspirator>" names the participation that is the dropped element → "This concerns a warehouse burglary at <place>" (no names of the plan's members in the narrative).
10. `416` (all four) `omit_0`: narrative "who sat a qualifying examination under Leela's admit card" entails that entry was permitted, i.e. the dropped cheating outcome → "This concerns a qualifying examination at <venue>"; same for the interview/courier/licensing variants.

Ruling: **CLEARED for scale-up.** The ten fixes go into the generator before the 84 helper scenarios are run (they are pattern fixes, so they apply to the new scenarios automatically); batch-02 is regenerated with them as part of the full set. I read the full scaled set (≈ 140 scenarios) once, for: distinctness (narrative-only Jaccard ≥ 0.40 reject / 0.30–0.39 my read), the leak table over every omission slice, ≥ 10 control scenarios per id, control ≥ 25 %, author provenance, dependency skips logged. That read is the D-tier merge gate.
