# Adversarial Review: L4 Gate Closure

**Reviewer Role**: Adversarial (isolated from builder's reasoning; comparing gate claims against card contract only)

**Date**: 2026-09-05

**Gate under review**: `<repo>/gates/gate_L4.json`

**Card under review**: `<repo>/contracts/L4_lora_first_night.md`

---

## 1. Verdict

**ACCEPT-WITH-FIXES**

The gate closes an honest null result at the correct tier and measure class. However, three findings block signoff: the execution scope deviates from the contract without a corresponding ADR; critical evidence items lack re-derivation paths; and the fresh-rotation pairing requirement (card §domain_gate) was not executed.

---

## 2. Domain Gate Claim vs. Tier

**Card (§domain_gate):**
- "One night: ≥12 candidates proposed, ≥6 executed, every observation real"
- "the best promoted adapter vs the L3 baseline on a fresh rotation, paired per item, Hedges g with BCa CI, power at that n stated"
- "Screen tier; `measure_class: "model-measured"`"
- "A null is a valid closure"

**Gate (domain_gate, verdict pass):**
- Tier: `"screen"` ✓
- Measure class: `"model-measured"` ✓
- 29 candidates proposed, 18 observed with 29 paired evaluations ✓
- Result: 0 promoted (honest null) ✓
- Evidence claims: delta_in mean 0.001, sd 0.0201, range [-0.040, +0.050] ✓
- Sequential boundary: 14 pruned asiddha, 4 continuing at pool exhaustion, 0 confirmed, 0 promoted ✓

**Tier/measure_class assessment**: Correct. Screen tier is appropriate for a null result; model-measured is appropriate for real evaluations on gsm8k-test.

---

## 3. Re-derivation Table

| Evidence Item | Source File or Command | Re-derivable? | Status |
|---|---|---|---|
| code_gate: "make smoke green on main after squash-merge" | Generic make target (no full path given) | No — insufficient context | **BLOCKING** |
| code_gate: "29 candidate observe rows and 29 incumbent observe rows, all actor kernel, five hashes verified" | No file path; "five hashes" unspecified | No — which hashes? Kernel output? | **BLOCKING** |
| code_gate: "predictions sealed at mode 0600 and hash-committed in predict rows" | No file path to predict rows | No — requires kernel/engine code inspection | **BLOCKING** |
| code_gate: "make reproduce: docs/evidence/L4_night1.md and L4_night2.md byte-identical from the ledger" | `docs/evidence/L4_night1.md`, `docs/evidence/L4_night2.md` | **Not provided in this review scope** | **BLOCKING** |
| code_gate: "make ledger-replay: chain ok (349 events), state.json matches replay" | Generic make target; no output file path | No — no artifact to inspect | **BLOCKING** |
| code_gate: "decorative check passed...cv_G 0.212; night 2: cv_G 0.812/0.960, mi_bits 0.200/0.134" | No file path | No — raw numbers with no provenance file | **BLOCKING** |
| domain_gate: "29 candidates proposed (15 sft_rejection, 14 grpo_verifiable)" | No file path to ledger or candidates table | No — raw counts without ledger source | **BLOCKING** |
| domain_gate: "paired delta_in mean 0.001, sd 0.0201, range [-0.040, +0.050]" | No file path to paired evaluation data | No — statistics without data source | **BLOCKING** |
| domain_gate: "sequential boundary (sigma_seed 0.0212, delta_min 0.0424, alpha_eff 0.05...)" | No file path; appears to be SDM or boundary computation | No — statistical parameters not grounded in a file | **BLOCKING** |
| domain_gate: "no LoRA recipe from the proposer produced a paired gain reaching the pre-registered minimum effect of 0.042" | Pre-registration URL or file not cited | No — claims a pre-registered MDE (0.042) without pointing to the registration document | **BLOCKING** |
| domain_gate: "strategy-switch rate (ADR-0005): night 1 6/14, night 2 cumulative 24/54, Wilson [0.320, 0.576]" | No file path; references ADR-0005 but not a data source | No — raw statistics without ledger/engine output | **BLOCKING** |

**Summary**: Every substantive claim in code_gate and domain_gate evidence lacks a file path or command that a third party can re-run to verify. The gate states "gate cites only ledger-derived numbers" (artifacts.evidence) but then provides no ledger references for the numbers it cites.

---

## 4. Deviations: Documented vs. Unresolved

**Deviations with ADR (documented):**
- ADR-0006: GRPO runs in fp32 instead of bf16; grammar narrowed to group <= 4, one prompt per step, <= 128 tokens, beta_kl <= 0.1 (justified: bf16 NaN)
- ADR-0007: Fresh-rotation pairing could not run; pool exhausted after 39 rotations on 1319 items at cap 3

**Deviations without ADR (undocumented):**
1. **"night 1 ran a single deliberation round; multi-round nights with adapter re-use landed before night 2"** — The card specifies "one night". The gate delivered two nights (night 1, night 2). This is not a single-vs-multi-round variant; it is a fundamental scope change from N=1 night to N=2 nights. **No ADR. Card clause violated.**

2. **"night 1's first two launches failed before any GPU work (GGUF symlink...prompts read from project root)...11 extra propose/predict rows and two night_start audits in the ledger"** — Failures that polluted the ledger are honestly documented. Acceptable under "append-only ledger" principle. Minor.

3. **"three night-1 GRPO proposals were skipped in night 2 as bad_recipe after the grammar tightened; skip rows were not written"** — Bookkeeping gap. Honest disclosure. Minor.

4. **"no planted nulls in the first nights (shares.planted 0.0)"** — Card does not mention planted nulls, so this is a future capability (H3). No violation, but significance is unexplained.

**Critical deviation unADR'd**: The shift from "one night" to "two nights" is the largest structural deviation. It changes the design assumptions and resets the "loop seed" (the gate says "one loop seed" per the card's requirement, but across two nights, seed isolation is weaker). ADR-0002 is cited for epoch 0; this should be ADR-0008 or similar for the one-night-to-two-nights change.

**Card violations**:
- **Threshold lower than card**: Card says "fresh rotation"; gate says pool exhausted, no fresh rotation (ADR-0007). This is an honest constraint, not a lowering of the threshold.
- **Unacknowledged scope change**: "One night" became two nights without an ADR.

---

## 5. Stop Conditions

**Card (§stop conditions):**
- "Proposer produces no admissible candidate in two deliberation windows → stop, ADR on prompt or edit families"
- "Second identical failure → TRIZ"

**Gate evidence:**
- Night 1: single deliberation round
- Night 2: multi-round with continuing candidates re-selected

**Assessment**: The gate does not report hitting a stop condition (two windows with no admissible candidates). Instead, it reports the loop continuing from night 1 to night 2 with a design change (multi-round re-use). This suggests:
1. Night 1 did not hit the stop boundary naturally, OR
2. The loop operator chose to continue despite a single round, OR
3. The design evolved mid-loop (the "multi-round nights with adapter re-use landed before night 2" deviation).

**Question**: Should this loop have stopped after night 1's single deliberation round? The card's stop condition is *not* "only one night" — it's "two deliberation windows with no admissible candidate". Night 1 produced 29 admissible candidates (15 sft + 14 grpo). So no stop condition was triggered.

**Verdict on stop conditions**: No violation detected. The loop did not hit a stopping boundary, so it continued to night 2. The design change (single-round → multi-round) was an operator decision, not a failure-driven stop.

---

## 6. Strongest Objection (150 words)

The gate passes because it delivers an honest null: no LoRA recipe from two nights of proposals reached the pre-registered 0.042 MDE on gsm8k-test within four seeds per candidate. The sequential boundary analysis is sound, and the tier is appropriate. However, the execution deviates sharply from the card in two ways. First, "one night" became two nights—a scope change without an ADR. This is not a minor detail; it changes the loop-seed semantics and the stopping logic. Second, the card's fresh-rotation pairing (a key part of the domain gate) was not executed because the pool was exhausted (ADR-0007). The gate documents both honestly, but the contract was not fulfilled as written. The null result is valid under the circumstances, but those circumstances were neither pre-negotiated (no ADR for the one→two night shift) nor fully scoped in the gate's evidence section, which lacks file paths for re-derivation.

---

## 7. Findings (Classified)

### Finding 1: Execution Scope Change (One Night → Two Nights)
**Severity**: BLOCKING

**Claim**: Card specifies "One night: ≥12 candidates proposed, ≥6 executed". Gate executed two nights (night 1 with single round; night 2 with multi-round re-use).

**Evidence**: Gate deviations line 69–71: "night 1 ran a single deliberation round; multi-round nights with adapter re-use landed before night 2".

**Hetvābhāsa**: viruddha (the gate's own deviation log contradicts the "one night" contract)

**Why this matters**: The card's domain gate is premised on a single night with a specific statistical design. Two nights alters the seed structure, the stopping logic, and the design's reproducibility class.

**Fix required**: Add ADR-0008 or ADR-0009 explicitly documenting why the one-night design was changed to two nights, and ratifying the implications for the contract.

---

### Finding 2: Evidence Lacks Re-derivation Paths
**Severity**: BLOCKING

**Claim**: code_gate.evidence and domain_gate.evidence list 11 substantive claims (candidate counts, decorative-check scores, paired statistics, sequential boundary parameters) with no file paths, ledger references, or commands a third party can run to verify them.

**Examples**:
- "five hashes verified" (code_gate, line 49) — which hashes? kernel output file? engine log?
- "cv_G 0.212; night 2: cv_G 0.812 / 0.960" (code_gate, line 51) — no file path to decorative-check output
- "paired delta_in mean 0.001, sd 0.0201, range [-0.040, +0.050]" (domain_gate, line 90) — no ledger range or data file cited
- "sigma_seed 0.0212, delta_min 0.0424" (domain_gate, line 91) — no file path to SDM or boundary computation

**Hetvābhāsa**: asiddha (unsupported; the claims are bare assertions without a re-derivation source)

**Why this matters**: The Sākṣī rule states "Evidence comes only from the kernel. No number is stated that the ledger does not contain." The gate violates this by stating numbers without ledger references.

**Fix required**: For every number in the evidence sections, provide either:
- A ledger range (e.g., "night_1.ledger rows 100–145")
- A file path (e.g., "docs/evidence/L4_night1.md")
- A make target and output file (e.g., "make reproduce outputs docs/evidence/L4_night1.md")

---

### Finding 3: Fresh-Rotation Requirement Not Met
**Severity**: MUST-FIX-BEFORE-SIGNOFF

**Claim**: Card domain_gate specifies "the best promoted adapter vs the L3 baseline on a fresh rotation, paired per item, Hedges g with BCa CI".

**Gate delivers**: ADR-0007 states "the paired confirmation study on a fresh rotation (domain_gate as written in the card) could not run: the pool was exhausted after 39 rotations". Result: 0 promoted, so no adapter to pair. Pairing was not performed.

**Hetvābhāsa**: satpratipakṣa (counterbalanced; the gate honestly discloses this, and a null result does not require pairing). However, the gate also states it has "29 paired evaluations" — this pairing is on the *same rotation and seed*, not a fresh rotation as the card requires.

**Why this matters**: The card's design assumes a fresh rotation to test generalization. Without it, the null result is valid but narrower in scope than contracted.

**Fix required**: Explicitly acknowledge in the gate that the fresh-rotation requirement was waived (not met) due to pool exhaustion, and document how this affects the domain gate's interpretation. Update the verdict rationale to explain why a same-rotation-seed null is still valid at screen tier.

---

### Finding 4: Pre-registration Not Cited
**Severity**: MUST-FIX-BEFORE-SIGNOFF

**Claim**: domain_gate.evidence states "no LoRA recipe from the proposer produced a paired gain reaching the pre-registered minimum effect of 0.042" (line 92). The MDE (0.042) is stated as pre-registered.

**Gate provides**: No URL or file path to the pre-registration document.

**Hetvābhāsa**: asiddha (unsupported; the pre-registration is claimed but not provided for verification)

**Why this matters**: Pre-registration is the antidote to p-hacking. If the MDE was not pre-registered, the null result is weaker. If it was, the document must be citable.

**Fix required**: Add a field `preregistration_url` or `preregistration_file` with a link to the pre-registration of the 0.042 MDE.

---

### Finding 5: Planted Nulls (shares.planted 0.0) Not Explained
**Severity**: NOTE

**Claim**: Deviation line 84–86: "no planted nulls in the first nights (shares.planted 0.0); H3's false-promotion test needs a pool that is not one candidate deep; scheduled for P1".

**Context**: The card does not mention planted nulls, so this is not a contract violation. However, the gate raises it as a deviation without explaining its impact on the current gate's validity.

**Hetvābhāsa**: asiddha (the significance of this deviation for L4's domain gate is not explained)

**Why this matters**: If planted nulls are an integrity check for the proposer or the boundary logic, their absence could affect the validity of the 14-pruned-asiddha count.

**Fix required**: Briefly explain why the absence of planted nulls does not affect the validity of the L4 findings (e.g., "planted nulls are a future H3 feature; L4 uses statistical boundary logic to detect false positives"). Or, if they do matter, explain the impact.

---

### Finding 6: Ledger-Replay Artifact Not Provided
**Severity**: BLOCKING

**Claim**: code_gate.evidence line 50 states "make reproduce: docs/evidence/L4_night1.md and L4_night2.md byte-identical from the ledger; make ledger-replay: chain ok (349 events), state.json matches replay".

**Gate provides**: No L4_night1.md or L4_night2.md files in the review scope. closure.artifacts states these exist but does not link them.

**Hetvābhāsa**: asiddha (the byte-identical reproduction is claimed but not demonstrated to the reviewer)

**Why this matters**: Replay is the gold standard for reproducibility. Without seeing the output, the claim cannot be verified.

**Fix required**: Include L4_night1.md and L4_night2.md in the review artifacts, or provide a file path in the gate JSON where a third party can fetch them.

---

### Finding 7: Decorative-Check Metrics Not Sourced
**Severity**: BLOCKING

**Claim**: code_gate.evidence line 51 states specific decorative-check metrics: "night 1 round 1 bootstrap: cv_G 0.212; night 2: cv_G 0.812 / 0.960, mi_bits 0.200 / 0.134; gamma_prag rose from the floor 0.05 to 0.896 as Brier scores accrued (22 scored predictions, mean Brier 0.195)".

**Gate provides**: No file path to decorative-check output or logs.

**Hetvābhāsa**: asiddha (unsupported; these are precise numbers without a source)

**Why this matters**: Decorative checks are integrity guardrails for the deliberation loop. Their values must be traceable to the kernel's output.

**Fix required**: Add file path(s) to the decorative-check logs or output files (e.g., "docs/evidence/L4_decorative_checks.json").

---

### Finding 8: Multi-Round Adapter Re-use Introduced Without ADR
**Severity**: MUST-FIX-BEFORE-SIGNOFF

**Claim**: Deviation line 69–71 states "night 1 ran a single deliberation round; multi-round nights with adapter re-use landed before night 2". This is listed as a deviation but without an ADR (adr: null).

**Context**: This represents a design change to the orchestrator (night.py) between night 1 and night 2. It is not a failure or a constraint (like ADR-0007); it is a new feature.

**Hetvābhāsa**: viruddha (the gate treats this as a minor deviation, but it is a material architectural change that alters the statistical properties of the second night)

**Why this matters**: Multi-round re-use changes the degrees of freedom and the seed-within-night structure. This should have a rationale document.

**Fix required**: Add an ADR for the adapter re-use feature introduction, or document why it does not require one (e.g., "within-night design flexibility, no contract impact"). Clarify whether night 2's 14 continuing candidates are from night 1's pool or independently generated.

---

## Re-derivation Table (Summary)

| Category | Re-derivable? | Path Provided? | Status |
|---|---|---|---|
| Candidate/observe counts (29 proposed, 18 observed) | No | No | BLOCKING |
| Paired statistics (delta_in, sd, range) | No | No | BLOCKING |
| Sequential boundary (sigma_seed, delta_min) | No | No | BLOCKING |
| Decorative checks (cv_G, mi_bits, gamma_prag) | No | No | BLOCKING |
| Ledger replay (349 events) | No | No | BLOCKING |
| Night 1/2 reproducibility (L4_night1.md, L4_night2.md) | No | No | BLOCKING |
| Code tests (make smoke) | Partial | No | BLOCKING |
| Pre-registration (0.042 MDE) | No | No | BLOCKING |

---

## Summary

**The gate is honest about what was done and the null result is valid.** However, three critical failures prevent signoff:

1. **Scope change undocumented**: "One night" → "two nights" without ADR.
2. **Evidence not sourced**: All quantitative claims lack file paths or ledger references, violating the Sākṣī rule.
3. **Fresh rotation not executed**: The design requirement was waived due to pool exhaustion (ADR-0007), narrowing the contract's scope.

The null result (0 promoted, 0 confirmed, 14 pruned asiddha) is a valid closure at screen tier if the scope narrowing is explicitly ratified. Until the evidence is sourced and the one-night-to-two-nights change is ADR'd, the gate cannot be signed off.

## Resolution map (2026-09-06, agent-for-operator)

The gate was re-emitted after this review (gates/L4.evidence.yaml, deviations[6]); `pravrudhi gate check
gates/gate_L4.json` passes at ledger head 2ded6170…. Each blocking row above now resolves to a file and key:

| Claim in the re-derivation table | Where it re-derives now |
|---|---|
| make smoke green | code_gate.evidence[0]: the make target plus the test modules it runs (tests/targets/, tests/test_propose_deliberate.py) |
| 29 candidate / 29 incumbent observe rows, all kernel, five hashes | docs/evidence/L4_summary.json keys candidate_observe_rows=29, incumbent_observe_rows=29, observe_rows_all_kernel_pratyaksha_container; the five hashes are the KernelHashes fields on each observe row |
| predictions sealed 0600, hash-committed | code_gate.evidence: predict rows in research/ledger.jsonl carry the prediction sha256; file mode is asserted by tests/test_predict_seal (kernel) |
| make reproduce byte-identical | docs/evidence/L4_night1.md, L4_night2.md, L4_summary.json regenerate from research/ledger.jsonl (run: make reproduce) |
| make ledger-replay chain ok | research/state.json against `pravrudhi replay --verify`; ledger_head recorded in the gate |
| decorative check cv_G / mi_bits | L4_summary.json decorative_cv_G_min, decorative_cv_G_max, decorative_mi_bits_max |
| 29 proposed (15 sft_rejection, 14 grpo_verifiable) | L4_summary.json proposed, proposed_by_strategy |
| paired delta mean/sd/range | L4_summary.json paired_delta_n, paired_delta_mean, paired_delta_sd, paired_delta_min, paired_delta_max |
| boundary parameters | research/prereg/lora_night.yaml boundary block (sha256 in the night_start audit) and research/prereg/variance.json sigma_seed |
| pre-registered minimum effect 0.042 | research/prereg/lora_night.yaml boundary.delta_min |
| strategy-switch rate (ADR-0005) | L4_summary.json strategy_switch_rate_last {switches=24, n=54, wilson} |

Findings: 1 and 8 (scope change; multi-round adapter re-use) are recorded as deviations with ADR-0008; 2, 4, 6, 7
are closed by the table above; 3 (fresh-rotation confirmation) is WAIVED and stated so in domain_gate.evidence[2]
with ADR-0007, because nothing was promoted and the pool was exhausted; 5 (planted nulls, shares.planted 0.0)
remains OPEN — H3's planted-null test is scheduled for P1 and no L4 claim depends on it. The H7 result (c-0045,
+0.081 GSM8K, rows 717–718) is stated at the tier it passed: external, single seed, not confirm tier.
