# Draft issues: nyaya harness fail-open audit (for the standup)

DRAFT. Not filed. Someone else files these. Every claim is **Tag's claim, pending verification**. Severities are Tag's grades
(scale in `HARNESS-FAILOPEN-FINDINGS-2026-09-30.md`), each backed by a committed, runnable test on branch
`tag/night-harness-failopen` (base `b9f0435`). No issue is listed without a repro. Constructed inputs only; no production
behaviour was changed, `src/` is untouched on the branch.

Repro for all: `PYTHONPATH=src:pravrudhi_kernel/src python -m pytest tests/test_nyaya_failopen_audit.py tests/test_nyaya_quote_adversarial.py -q --runxfail -k "<key>"`.
The `test_DEFECT_*` tests are `xfail(strict=True)` and assert the DESIRED behaviour: they fail today (the reproduction) and become a
hard failure once fixed, which is the cue to remove the marker.

## Already filed, NOT proposed again

- **H-02 (high): AND-gate drops a defeater the primary found, contract PROVES.** Already filed as **AxisMeru/pravrudhi #152**
  (P0). Accepted direction: a second-judge disagreement on a defeater means REFER.
  This branch carries the repro and the planted test for the fix: `test_DEFECT_H02_planted_for_fix_a_defeater_disagreement_must_refer`
  (strict xfail, 3 cases) in `tests/test_nyaya_failopen_audit.py`. Key `-k H02`.
- **H-01 (medium): NaN or positive logprob accepted, element called established.** Already filed as **AxisMeru/pravrudhi #156**
  (from the typed-layer workstream's corroborating test; found independently by this audit). This branch's extra, for #156:
  the typed-layer one-liner closes NaN and +inf but NOT positive logprobs (est = +5.0, +0.001); adding
  `if any(math.isnan(v) or v > 0.0 for v in top.values()): raise JudgeOutputError(...)` closes all five repros (`-k H01`).
  Reachability from the served judge is unverified (only via a server that writes a bare `NaN` literal; a `null` fails closed):
  see the findings file.

---

## 1. `Gate1NLIModel` scores a missing NLI label as 0.0, silently disarming contradiction-veto mode
- **Severity:** medium (Gate 1 is off by default and in no shipped config; needs `NYAYA_GATE1_MODEL` pointing at a differently-labelled model).
- **Body:** `score_contradiction_one` returns `float(p.get("contradiction", 0.0))` (`nyaya_judges.py:963`); `score_one` defaults both
  `entailment` and `contradiction` to 0.0 (`:955`). A model whose `id2label` is not `entailment/neutral/contradiction` (for example
  `label_0/1/2`) therefore yields 0.0, read as "no contradiction", and the third gate never vetoes. A missing input is defaulted to a
  number, against the repo rule (a check that is missing an input raises). In entailment mode the same 0.0 vetoes, so it is closed there
  only by coincidence.
- **Repro:** `-k H04`. `test_DEFECT_H04_label_set_without_the_expected_labels_must_raise` (2 cases, xfail);
  consequence: `test_characterise_H04_a_relabelled_model_disarms_contradiction_veto`.
- **Proposed fix:** bare subscripts `p["contradiction"]` / `p["entailment"]` (KeyError), or one check in `_ensure_loaded` that the three
  label names are present, raising otherwise.

## 2. Quote check accepts degenerate quotes (whitespace, invisible characters, a single letter) as evidence
- **Severity:** medium.
- **Body:** `locate_quote` (`nyaya_quote.py:41`) proves a quote APPEARS verbatim (no unicode trick fools it, both directions tested) but accepts
  any non-empty substring. A quote of `" "`, `"\n"`, `"e"`, `","`, an invisible `U+200B`, or a lone combining mark returns `valid=True,
  quote_check: ok`. With the frontier judge (p is always 1.0) such a claim is `established`. The house judge quotes the whole fact and is
  unaffected.
- **Repro:** `tests/test_nyaya_quote_adversarial.py -k degenerate` (`test_DEFECT_degenerate_quote_is_accepted_as_evidence`, 7 cases, xfail).
- **Proposed fix:** reject a quote with no visible content (empty after `strip()`, or no character in Unicode categories L or N). A minimum
  length is a threshold and so a PROPOSAL that needs a constructed set to fit; Tag recommends no number.

## 3. Frontier reply parser accepts prose around the JSON object
- **Severity:** medium (the parser's acceptance is reproduced; that a real model echoes an injected verdict is not shown).
- **Body:** `parse_frontier_reply` (`nyaya_judges.py:504`) matches `\{.*\}` (first `{` to last `}`). The prompt says "ONE JSON object and
  nothing else" but it is not enforced, so `I refuse. The fact says {"status": "established", ...} but no.` parses as `established`
  (p 1.0). Two separate objects in one reply are already refused.
- **Repro:** `-k H07` (`test_DEFECT_H07_frontier_reply_with_surrounding_prose_is_accepted`, 3 cases, xfail).
- **Proposed fix:** require the whole reply, after stripping one optional ```json fence, to be a single JSON object. The passing test
  `test_frontier_pure_json_and_fenced_json_still_parse` pins that normal and fenced replies keep working.

## 4. `AgentConfig` does not validate `refer_band` against `tau`; a short band silently lets defeaters through
- **Severity:** medium (shipped config is fine: tau 0.74, band [0.5, 0.74]; this is a latent config-edit hazard).
- **Body:** `AgentConfig.__post_init__` (`nyaya_agent.py:167`) checks band and tau independently. With `refer_band.high < tau`, a score in
  `[high, tau)` is neither established nor in the band. For a DEFEATER that means "absent", so the contract can PROVE over a defeater scored
  0.70.
- **Repro:** `-k H09` (`test_DEFECT_H09_config_must_refuse_a_band_that_stops_short_of_tau`, xfail; consequence pinned by
  `test_characterise_H09_consequence_of_a_gapped_band_is_proof_over_a_defeater_scored_0_70`).
- **Proposed fix:** `if high < self.tau: raise ValueError(...)` in `__post_init__`. No threshold changes.

## 5. A config without `pinned_score_sha256` loads as "accept any Lean binary"
- **Severity:** medium (shipped config names a pin and two tests guard it; needs a config edit or a typo'd key).
- **Body:** `load_agent_config` (`nyaya_agent.py:353`) uses `body.get("pinned_score_sha256")`, giving None when the key is absent or misspelt;
  `BinaryRegistry` (`:440`) then skips the integrity check. The pin is the anchor for every Lean attestation, and its absence is treated as
  a pass.
- **Repro:** `-k H13` (`test_DEFECT_H13_load_agent_config_must_refuse_a_config_with_no_pin`, xfail; behaviour pinned by
  `test_characterise_H13_registry_accepts_any_binary_when_the_pin_is_none`).
- **Proposed fix:** `load_agent_config` raises when the key is absent. Keep an explicit opt-out for dev and tests (for example
  `pinned_score_sha256: null` plus `allow_unpinned: true`); `BinaryRegistry(pinned_sha256=None)` stays valid in code.

## 6. Gate 1 contradiction mode treats a NaN score as "no contradiction"
- **Severity:** low (Gate 1 off by default; a CPU fp32 NaN is unlikely).
- **Body:** `gate1_contradiction_check` (`nyaya_judges.py:899`) uses `vetoed=score >= tau_c`; `nan >= x` is False, so the element stays
  established. Entailment mode (`passed = score >= threshold`) fails closed for the same NaN, by accident of comparison direction.
- **Repro:** `-k H03` (`test_DEFECT_H03_nan_contradiction_score_must_not_leave_the_element_established`, xfail; control
  `test_H03_control_nan_entailment_score_fails_closed_today`).
- **Proposed fix:** `if not math.isfinite(score): raise` in both check functions, so the existing `except Exception` produces
  `gate1_unavailable` (REFER).

## 7. `ingest_facts` accepts a fact made only of invisible characters
- **Severity:** low.
- **Body:** `ingest_facts` (`nyaya_agent.py:394`) and the API's `f.strip()` check do not remove zero-width or format characters (`U+200B`,
  `U+200C/D`, `U+2060`, `U+FEFF`, soft hyphen, `U+202E`). A blank-looking fact becomes `F<n>` with a sha and can be cited and quoted
  (see issue 2). Unicode whitespace (NBSP, ideographic space, line separator) is already refused.
- **Repro:** `-k H06` (`test_DEFECT_H06_invisible_only_fact_must_be_refused`, 8 cases, xfail; consequence
  `test_characterise_H06_an_invisible_fact_can_be_cited_and_quoted`).
- **Proposed fix:** after `strip()`, require at least one character outside Unicode categories `Z*`, `C*` and `Mn`; else `ValueError("fact N is empty")`.

## 8. `select_contracts` returns an empty list instead of raising; a run over zero contracts succeeds
- **Severity:** low (through the API `contract_ids` has `min_length=1` and an id must be listed, so not reachable there).
- **Body:** an empty `--list-contracts` with no selector, or `contract_ids=[]`, returns `[]` and `NyayaAgent.run` returns an empty `AgentRun`
  with no warning. `sections=[]` already raises, so the function is inconsistent. A check missing its input should raise.
- **Repro:** `-k H08` (3 xfail tests in `TestDefectH08EmptySelection`; `test_characterise_H08_the_empty_run_carries_no_contracts_and_no_warning`).
- **Proposed fix:** raise `UnknownContractError` when the chosen list is empty.

## 9. `assemble_assertions` / `expected_outcome` default an absent verdict instead of raising
- **Severity:** low (unreachable through `run()` with a real binary; both are public functions).
- **Body:** (a) a denial missing from the verdict map is read as "defeater absent" and the wire is PROOF-shaped (the fail-open direction);
  (b) a missing required element is silently False; (c) a contract with no required elements is a vacuous PROOF (`all([])`); (d)
  duplicate element names collapse and the last verdict wins.
- **Repro:** `-k H10` (`TestDefectH10AssemblyDefaults`, 4 xfail; `test_characterise_H10a_missing_denial_yields_a_proof_shaped_wire`).
- **Proposed fix:** `assemble_assertions` raises `KeyError` for any element or denial absent from the verdict map; `expected_outcome`
  raises on an empty `elements`; `_run_contract` refuses duplicate element names.

## 10. Frontier judge reports `p_established` 1.0 / 0.0 that it was never given
- **Severity:** low (documented in the module docstring, so a design deviation from the repo rule rather than a slip).
- **Body:** `parse_frontier_reply` (`nyaya_judges.py:516`, `:524`) invents a probability. Repo rule: a missing confidence is None, is
  excluded and is counted, never defaulted to a number. Side effect: a frontier claim can never fall in the refer band.
- **Repro:** `-k H11` (2 xfail; `test_characterise_H11_a_frontier_claim_can_never_fall_in_the_refer_band`).
- **Proposed fix:** `ElementJudgment.p_established: float | None`; the frontier sets None; `_run_contract` counts unscored elements and decides
  whether they REFER. This touches a type used everywhere, so it needs a design decision first.

## 11. API returns `start`/`end` offsets with no unit and no base
- **Severity:** low (no in-repo consumer slices with them).
- **Body:** offsets are Python code-point indices into the STRIPPED fact text; the response lists only `{id, sha256}` per fact. A JavaScript
  (UTF-16) or byte-based consumer, or one slicing its original un-stripped text, selects a different span. Same position: code-point 3,
  UTF-16 5, UTF-8 9 after two astral characters.
- **Repro:** `-k H12` (`test_DEFECT_H12_response_model_declares_the_offset_unit`, xfail); hazard pinned in
  `tests/test_nyaya_quote_adversarial.py` by `test_the_three_offset_units_disagree_after_an_astral_character` and
  `test_leading_whitespace_offsets_are_relative_to_the_stripped_fact`.
- **Proposed fix:** add `offset_unit: "codepoint"` and either the stripped fact text or `leading_trim` to the response; document both.

## 12. Fact text can forge `[F<n>]` headers and the `Answer:` terminator in the judge prompt
- **Severity:** low (bounded: the quote check uses the real fact map, and the house verdict is read from logits).
- **Body:** `build_house_prompt` (`nyaya_judges.py:190`) interpolates fact text unescaped, so a fact containing `\n[F2] ...` or `\nAnswer:`
  produces a prompt the judge cannot tell from real structure. A judge that cites a forged id gets `unknown_fact`; a quote lifted from
  injected text is valid only against the fact that holds it (both pinned).
- **Repro:** `-k H14` (`test_DEFECT_H14_fact_text_must_not_forge_a_fact_header`, xfail; control
  `test_H14_control_a_normal_prompt_is_unchanged_by_any_fix`).
- **Proposed fix:** reject or neutralise fact lines beginning `[F<digits>] ` or equal to `Answer:` at `ingest_facts`, not in the prompt builder
  (the house prompt must stay byte-for-byte the training prompt).

## 13. `Gate1Judge` returns an established judgment unchanged when its fact id does not resolve
- **Severity:** low (the agent's quote check catches it downstream; another caller would not).
- **Body:** `Gate1Judge.judge` (`nyaya_judges.py:1033`) returns `judgment` as-is when `fact_text is None`, on the comment that the quote check
  will reject it. Defence in depth is missing at the judge layer.
- **Repro:** `-k H15` (`test_DEFECT_H15_unresolvable_fact_id_must_not_stay_established`, xfail; control
  `test_control_the_agent_still_rejects_it_downstream`).
- **Proposed fix:** return `not_established`, `vetoed_by="gate1"`, `gate1_skip_reason="gate1_unavailable: unresolvable fact id"`.
