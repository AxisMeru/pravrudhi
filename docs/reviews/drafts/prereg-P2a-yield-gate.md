# Pre-registration — P2a supervision yield gate (DRAFT, reviewer, 2026-09-15)

Status: DRAFT; blocked on the explicitly unsourced thresholds and checker wiring below. Freeze this document's
sha in `research/prereg/` before collecting the decision batch. No GPU minute for structure SFT or
verifier-filtered distillation until this gate passes; authoring and replay are offline CPU work.
House rules apply (`docs/reviews/runpod-house-rules-2026-09-15.md`); a pass does not authorize a pod.

## Sources and scope

`docs/reviews/track-b-adversarial-2026-09-15.md` §§8–10, especially §9a, supersedes §9's assumed training pool.
`docs/reviews/spikes/trackb-typed-ir/codex-gpt6-astra-data-feasibility-2026-09-15.md` §§1,3,5 supplies inventory
ceilings and exclusions. The `nyaya_ir_prototype/` README, schema and goldens supply structural requirements,
not proof yield. `docs/reviews/signatures/2026-09-15-t5b-tranche{1,2,3}.md` supplies the reviewed registry.
The task brief strengthens §9a's ≥200 *proposals measured* to ≥200 *teacher proposals accepted* and excludes
all LegalBench partitions. These stronger requirements govern here. The retired 25,000 target is not a quota.

## Training tiers — unique admitted records, after exclusions

| tier | admissible supervision | required before SFT | count source / present evidence |
|---|---|---|---|
| deterministic full-IR | controlled authored facts, reviewed rules, complete grounded IR and computed proof | D_min = **TBD (source needed)** | Feasibility §3: 0 verified; generator yield unmeasured. Its conditional 36 LegalBench diversity rows are excluded here. |
| partial-IR | grounded spans/roles/labels or typed abstentions, no certified application claim | P_min = **TBD (source needed)** | §9a permits auxiliaries but gives no quota or measured partial-IR yield; feasibility §3 reserves 5,314 CaseHOLD validation rows. |
| retrieval-only | provision lookup/citation targets, no application trace | R_min = **TBD (source needed)** | §9a / feasibility §§1,3: IPC 557 + BNS 358 = 915 documents; law_v3 6,206 raw rows, only 30 inspected. These are pre-exclusion ceilings, not required or admitted counts. |

Teacher-accepted full-IR is a separate provenance subtotal T within the full-IR tier, not deterministic
conversion yield D: **T ≥200** (task brief). Report D and T separately and full-IR total D+T after deduplication.
Each final record belongs to exactly one tier; rephrasings, option permutations and repeated teacher attempts
do not multiply its source-row/controlled-fact-instance count (feasibility §3). Report template-family counts too.
Do not fill a proof quota with refusals or partial records. Partial targets must mask unsupported proof fields;
retrieval targets supervise retrieval only. Keep `RECORD`/`AUDIT` out of model targets (`serialize_target`).
Resolve D_min, P_min, R_min and the floor below with cited sources before freeze; no implicit defaults.

## Registry and teacher acceptance

The required 14 contract IDs (task brief; tranche 3's final registry count) are:

```text
ipc405_misappropriation ipc405_use_or_disposal ipc405_wilfully_suffers
ipc415_property ipc415_damaging_act ipc416
ipc182_misdirected_act ipc182_abuse_of_power
bns69 bns47 bns85
bns46_instigation bns46_conspiracy bns46_intentional_aid
```

Pin the registry, corpus, adapter and scorer hashes, and both shape/content signatures at their matching
revisions. Content anchors: tranche 1 `ae71a9a`, tranche 2 `a86ae9e`, tranche 3 `db5c5cd`.
Before sampling, record one passing control and one correctly diagnosed omission through the actual
`check(answer, contract_id)` path per ID (tranche 2 sequencing ruling). Unknown IDs must refuse.
The local `src/pravrudhi/application/nyaya_lean.py` currently exposes only seed IDs `0` and `5`;
its citation-oriented `licensed` result cannot certify these IR records. Wiring is a blocking prerequisite.

Freeze teacher revision, prompt, sampling settings, seed list, source families and per-ID proposal allocation.
Batch size / attempt cap = **TBD (source needed)**; ≥200 proposals is the sourced lower bound (§9a).
All attempts, malformed outputs, refusals and checker errors remain in the denominator; no selective logging.
Let N_i be attempted proposals and A_i unique accepted records for ID i; report A_i/N_i and total A/N,
alongside raw successful-attempt counts and duplicate exclusions. A zero denominator is reported as undefined.
Acceptance-rate threshold = **TBD (source needed)**; §9a requires measurement but supplies no rate cutoff.
Minimum accepted per-ID floor F = **TBD (source needed)**, a positive integer fixed before sampling.
Require A_i ≥ F for every listed ID and sum(A_i) ≥200; no cross-ID double counting or quota substitution.
An ID below F fails the entire gate. Record its rejection causes and return to fact/contract authoring;
do not drop, merge or rename that limb to pass. Any scope change needs a new prereg and new decision batch.

An accepted proposal must parse/round-trip, pass independent span/hash/event/typed-rule validation, and receive
computed `grounded` plus `valid` certification from the pinned Lean/Z3 path via `check(answer, contract_id)`.
Here `answer` is the complete emitted IR target bound to its immutable prompt, not just its prose ANSWER line.
Freeze the adapter's exact verdict-field mapping and backend in the manifest; unknown/missing results fail closed.
Never trust a teacher's AUDIT or verdict. The prototype's `validate_record` is structural only; its 20 goldens
use fixture sources and explicitly uncertified reports, so they contribute no accepted training records.
Check trusted rule IDs, premises, explicit negation and event bindings; teacher-supplied rules are forbidden.
Retain `notFormalisable` residue. IPC 416/BNS 47 dependency predicates do not establish cross-contract composition;
unproved application claims must be partial-IR. Follow the signed BNS 69 denial encoding, not the older spike.

## Leakage — every training tier, before teacher generation and again before release

Exclude the held-out 690 (`data/eval/law_qa_heldout_v3.jsonl`), all LegalBench (75 train + 3,397 test),
all IL-TUR (13,019 test rows locally), all 33 law_apply and all 5,314 CaseHOLD validation rows.
Sources: task brief, §9a, feasibility §§1,5. Exclude their labels, traces, paraphrases, crops, controls and
negation siblings. Any additional benchmark partitions discovered locally inherit the same exclusion.
Exclude instruct_v1 legal-proof conversions (semantic mismatch, feasibility §3).
Build a SHA-256 exclusion index over IDs, source IDs, prompt/facts and target separately, plus parent case/template
groups; checking only prompt-target pairs is insufficient. For comparison only, use Unicode NFC and collapsed
whitespace; hash exact UTF-8 bytes separately and never normalize the immutable text used for span offsets.
Propagate parent split identity to derivatives; unresolved lineage is ineligible. Hashes alone do not detect
paraphrases, so group lineage and reviewed template-family exclusion are mandatory.
Join law_v3 against all 690 provision IDs before use. Exclude held-out `(act,section,version)` groups and their
dependency-derived templates from source-disjoint training (feasibility §5); report resulting registry shortfalls.
Shared statute retrieval context may remain evaluation context where allowed; it supplies no training exemption.
Permitted excluded-record/group intersections = none (binding exclusion rule); record removals by source and tier.

## Commands and evidence contract

The following is the exact proposed offline interface, **to be implemented**, not an assertion that these scripts
or artifacts exist. Run from the prabhasa-nyaya root after pinning its adapter to the reviewed checker API.
No script implementation or evidence generation is part of this draft. Missing tooling/artifacts blocks the gate.

```sh
python scripts/p2a_yield_gate.py inventory --manifest research/prereg/P2a-manifest.json
python scripts/p2a_yield_gate.py exclude --manifest research/prereg/P2a-manifest.json
python scripts/p2a_yield_gate.py reachability --manifest research/prereg/P2a-manifest.json
python scripts/p2a_yield_gate.py replay --manifest research/prereg/P2a-manifest.json
python scripts/p2a_yield_gate.py decide --manifest research/prereg/P2a-manifest.json
```

All outputs go to `research/gates/P2a/`; each command records argv, input/output SHA-256, code revision and status.
`P2a-manifest.json` pins thresholds and their sources, prereg hash, all local input paths/hashes, split policy,
teacher configuration, complete proposal-batch hash, registry/signatures, schema, checker adapter and binary hashes.
`inventory.json`: raw, excluded, duplicate and admitted counts by source/tier; D/T separated; template counts.
`exclusions.json`: protected-file hashes/counts, normalization policy, all hash/group/provision matches and dispositions.
`reachability.json`: per-ID control/omission inputs, expected/actual outcomes, unknown-ID refusal and raw checker reports.
`proposals.jsonl`: immutable complete teacher batch with attempt ID, parent lineage, contract ID and target.
`checks.jsonl`: every replay's structural report, backend input/output, verdict, residue and rejection reason.
`accepted.jsonl`: final unique records with source, tier, provenance, lineage and independently computed audit.
`yield.json`: N_i, A_i, rates, dedup counts and threshold comparisons; `gate.json`: pass/fail, reasons and evidence hashes.
Replay consumes pre-existing local proposals only; it calls the checker for each record and never calls a teacher
or GPU. Decision recomputes counts from linked records; edited metadata or hash mismatches invalidate the evidence.

## Decision rule and failure action

**PASS iff** every threshold is sourced and frozen, all required evidence replays, every ID is reachable,
D ≥ D_min, P ≥ P_min, R ≥ R_min, T ≥200, every A_i ≥ F, and all leakage/acceptance requirements hold.
If a rate cutoff is adopted, freeze it before sampling and require it too; otherwise explicitly freeze measurement-only.
Any TBD, missing artifact, checker error, unresolved provenance or unmet condition is **FAIL / no GPU**.
Return to supervision, adapter or contract authoring; preserve the failed batch and reasons. Re-freeze changed
inputs/thresholds before a new batch; never retrospectively lower thresholds to admit an observed yield.

## What this gate does not decide

It does not select the base model (P0/P1), certify legal truth beyond the stipulated contracts, demonstrate
generalisation, set SFT hyperparameters/cost caps, prove a distillation lift, or authorize promotion/public claims.
P2b/P3 need their own preregs and model-measured gates. Operator kick-off, written per-job go, measured preflight,
one-pod guard, checkpoint durability and spend/kill conditions remain independently binding house rules.

## Reviewer amendment — thresholds set (Track B reviewer, 2026-09-15 11:44 BST)

Draft authored by the spikeB-yieldgate Codex agent; read and amended by the reviewer. The TBD thresholds above are
resolved here as reviewer decisions (source: this section, under the operator's delegation of 2026-09-15). They are
deliberately modest floors, not targets; the gate exists to prove the pipeline yields real records, not to size the SFT.

| symbol | value | rationale |
|---|---|---|
| D_min (deterministic full-IR, unique) | 300 | ≥ 20 per registered limb on average; below this the deterministic generator is not working |
| T (teacher-accepted full-IR) | ≥ 200 | task brief / §9a |
| F (per-id accepted floor) | 5 | 14 × 5 = 70 ≤ 200; proves every limb is reachable by the teacher, not that it is common |
| P_min (partial-IR) | 1,000 | auxiliaries are cheap; below this partial supervision cannot carry the abstention/typed-refusal classes |
| R_min (retrieval-only) | 2,000 | ≈ 2 per provision across IPC + BNS plus held-out-disjoint templates; sourced from the 915-document ceiling in feasibility §1 |
| attempt cap (teacher proposals) | 2,000 | ≥ 200 accepted at ≥ 10 % yield; the batch stops at the cap or at 2 × T, whichever first |
| acceptance rate | measurement-only | reported as A/N and per id; no cutoff at this gate |

Blocking prerequisites restated: the pravrudhi `check(answer, contract_id)` path must reach all 14 ids (d0's wire
pass, in progress), with the per-id reachability run recorded before proposals are sampled. Freeze happens when trackB
copies this file to `research/prereg/` in prabhasa-nyaya and the reviewer signs the sha.
