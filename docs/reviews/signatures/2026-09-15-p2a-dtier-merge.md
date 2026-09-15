# P2a deterministic full-IR tier — merge-gate read (Track B content), 2026-09-15 16:24 BST

`prabhasa-nyaya` `trackB/t5b-deterministic-records` @ `e8a996f`, `research/gates/P2a/deterministic_records/batch-03/` (490 records, 140 scenarios, 520 provenance rows incl. 30 dependency skips).

## Mechanical gates (mine): all PASS
REG re-verified independently for all 490 records against a fresh build at `e8a996f` (control → grounded; omission → flagged naming one element; denial → one denied): 490/490. 140 control scenarios, exactly 10 per id; control ratio 28.6 % (floor 25 %); D = 490 ≥ 300. Author + draft_batch on every emitted row (trackB 56, seat2-helper-1..4 24/24/18/18); jaccard fields on every row; 30 skips = the three declared dependency directions, logged; 0 rephrasing rejections; all audits `certified: true`. Narrative-only Jaccard: max 0.32, none ≥ 0.40, two pairs in the read band (bns69 jobpromo/scholarship; 46a getawaycar/disguisesupplier), both distinct on my read.

## Distinctness: PASS under amendment (c)
Both spike agents (A: BNS ids + 182b; B: IPC ids; reports `dtier-read-{A,B}.md`) flag "clusters" (405u move-for-space ×4, 405w passive-witness ×4, 415p retail-goods ×6, 182b false-report ×10, 416 exam ×3). Ruling: these differ in parties and in object or setting and are separate fact patterns sharing the offence's own frame; a false-report offence cannot be instantiated without a false report to an official. Not rephrasings. **Advisory for the next tranche (not a gate):** cap identical narrative frames at 4 per id so the tier does not over-represent one template.

## HELD on three items
**1. Two mis-filed scenarios (replace; the per-id floor of 10 must be restored):**
- `s_415d_insurance_overstatement`: the deceived insurer delivers property (an inflated payout) to the deceiver; that is `ipc415_property`, not the damaging-act limb; the el2 harm is pinned on unrelated creditors through an attenuated chain. Remove from `ipc415_damaging_act`; re-author under `ipc415_property` only if still needed there, else drop.
- `s_416_lottery_ticket_substitution`: Nikhil presents an altered ticket as himself; no pretence of being another person, real or imaginary. Forgery, not personation. Remove; replace with a personation scenario.

**2. Wording leaks (Class A, rewrite the fact; 22 slices):**
- bns69 helper set, six `omit_1`: el0 reads "induces X to sexual intercourse by falsely promising…", which asserts the dropped intercourse element. el0 = "induced X by deceitful means, a false promise of …" only.
- bns47, three `omit_2` (goods_trafficking_conspiracy, document_fraud_aid, extortion_assistance): el0 names the foreign destination or "overseas targets/residents", asserting the abroad element. el0 states the abetted act without location.
- ipc182_misdirected_act helper set (`s_182m_*`), six `omit_2`: el1 names the specific official act ("intends to cause the officer to deny the variance"), which is el2's content. el1 = "intended, or knew it likely, that the false information would lead the officer to act on it" (generic, as the trackB set already does).
- ipc415_property, two `omit_1` (used_laptop, expired_cosmetics): el0 says "dishonestly", the dropped mental element's word. Remove the adverb from el0. (The four "when she knew it was…" facts are NOT leaks: knowledge of falsity is part of deception, not of the inducement element.)
- ipc405_use_or_disposal, two `omit_2` (research_specimen, wedding_textiles): el1 says "outside the company's oversight and control" / "outside the bride's oversight and authorization", asserting a governing term. Describe the transfer/loan only. (The other eight flagged `omit_2` rows, "unrefrigerated", "shared box", "mixed", "commercial propagation", "different filmmaker", are conduct descriptions that entail no term: NOT leaks.)
- ipc416, three `omit_1` (examhall_bina, licensingexam_juhi, exam_hall_substitution): el0 "credit the sitting to Leela's name and registration" presupposes substitution. el0 = "the invigilator permitted entry and accepted the sitting".

**3. New dependency (Class B):** `ipc405_wilfully_suffers` el1 → el0. Wilfully suffering another to misappropriate presupposes being in a position to permit it; every helper el1 (granting access, exempting from audit, providing credentials, disabling surveillance, overlooking discrepancies) restates dominion, and no wording of permission avoids it. Declare `el1→el0`, skip `omit_0` (10 slices, logged), keep `omit_1`. The six `s_405s_*` scenarios otherwise fit the limb: "deliberately failing / knowingly allowing" is wilful sufferance, not negligence, as authored.

Not upheld: 405u `omit_2` conduct descriptions (8), 415p "when she knew" (4), all rephrasing-cluster flags.

After the fixes D ≈ 490 − 10 (skips) − (2 removed scenarios' slices) + replacements ≥ 300; ratio unchanged within a point. **Status: HELD.** Regenerate batch-03 at the same scenarios plus the two replacements; I re-read only the changed rows (the 22 rewritten facts, the two new scenarios' slices, the new skips) and sign; Track A's shape signature at the same SHA; lead merges the D-tier.
