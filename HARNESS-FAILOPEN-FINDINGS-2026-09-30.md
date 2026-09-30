# Nyaya harness fail-open audit, 2026-09-30

**Every claim in this file is Tag's claim, pending verification** by a second reviewer. Nothing here is evidence in the
repo's sense (constructed inputs only, scripted judges, no served model, no real `score` binary).

- Branch: `tag/night-harness-failopen` (base `origin/main` @ `b9f0435`). Tests and numbers below were measured at
  tip `7d0ed03` (tests only; `src/` untouched; later commits on the branch are a test rename, comment wording and these two documents, no logic change); this file was committed after it (see `git log` for the final tip). Nothing merged, nothing pushed to main.
- Scope: priority (3) of the operator's night brief. Constructed inputs only; no sealed material, no T3-T9 text,
  no kernel (`pravrudhi_kernel`) edit, no paid API, no `claude -p`, no RunPod, no judge or production traffic.
  `src/` is untouched: every defect has a committed reproducing test and a PROPOSED fix in this file, nothing more.
- **`element_first_harness.py` is not in this public repo.** It lives in prabhasa-nyaya (private) and was not opened.
  The pravrudhi mirror is `src/pravrudhi/application/nyaya_agent.py` (its docstring says "mirrored, never imported"),
  with `nyaya_judges.py`, `nyaya_quote.py` and `nyaya_lean_registry.py`. This audit covers the mirror only. In the mirror
  the judge never supplies offsets (the system computes them with `str.find`, `nyaya_quote.py`), so a `quote == facts[s:e]`
  check on judge-supplied offsets does not exist here; if the private harness has one it is unaudited.

## Headline

The quote check cannot be fooled on appearance, in either direction, on any input tried. The real fail-open is one layer
up: under config C the AND-gate throws away a defeater the primary found, and the contract proves (H-02). The lead has
confirmed it: filed as AxisMeru/pravrudhi #152 (P0). The NaN logprob defect (H-01)
is filed as #156. Neither is re-proposed in `DRAFT-ISSUES-HARNESS.md`.

## Results

Severity scale (Tag's own, stated so it can be argued with): **blocker** = false PROOF on the shipped default path with
ordinary input; **high** = false PROOF (or a disarmed safety control) on a production configuration that is in use, with
ordinary input; **medium** = fail-open on a realistic but non-default path, a malformed server reply, or a config edit;
**low** = fail-open that another layer currently catches, or a hardening gap. A grade stands only because a committed,
runnable test reproduces it (operator rule); anything without one is listed under "Unverified observations" ungraded.

| Severity | Count | Ids |
|---|---|---|
| blocker | 0 | |
| high | 1 | H-02 (filed: #152) |
| medium | 6 | H-01 (filed: #156), H-04, H-05, H-07, H-09, H-13 |
| low | 8 | H-03, H-06, H-08, H-10, H-11, H-12, H-14, H-15 |

15 defects, 43 strict-xfail reproductions (`tests/test_nyaya_failopen_audit.py` 36, `tests/test_nyaya_quote_adversarial.py` 7,
both @ `7d0ed03`). Suite at `7d0ed03`: `pytest tests/test_nyaya_failopen_audit.py tests/test_nyaya_quote_adversarial.py`
gives 183 passed, 43 xfailed; with `--runxfail` the same 43 fail and the 183 still pass. `pytest tests -k "nyaya or partner"`
gives 832 passed, 137 skipped, 47 xfailed (4 xfails pre-existing), nothing newly red. Ruff is clean on both new files.

How to read the tests: `test_DEFECT_H<nn>_*` asserts the DESIRED behaviour and is `xfail(strict=True)`; it fails today (the
failure is the reproduction) and turns into a hard failure when the code is fixed, prompting removal of the marker.
`test_characterise_*` pins today's behaviour to show a defect's consequence. Everything else pins a path that correctly
fails closed.

Run: `PYTHONPATH=src:pravrudhi_kernel/src python -m pytest tests/test_nyaya_failopen_audit.py tests/test_nyaya_quote_adversarial.py -q`
(add `--runxfail` to see each defect fail).

## The quote check: can it be fooled?

**On appearance: no.** `locate_quote` (`nyaya_quote.py:41`) is exact `str.find` on Python code points. There is no
normalisation on either side, so there is no asymmetry. Verified by construction and by test
(`tests/test_nyaya_quote_adversarial.py` @ `7d0ed03`):

- Normalisation symmetry, BOTH directions (operator rule), 12 transforms (NFC, NFD, NFKC, NFKD, casefold, upper, zero-width
  strip, whitespace collapse, Cyrillic homoglyphs, fullwidth, mid-string ZWSP, NBSP-for-space): `TestNormalisationSymmetry`.
  (a) transform applied to the quote only; (b) transform applied to the fact only; (c) both. In all three the result equals
  the literal-substring oracle `quote != "" and quote in fact`, and for every transform at least one changed input was
  rejected, so the tests are not vacuous.
- Hypothesis (400 and 300 examples, alphabet includes combining marks, ZW characters, bidi override, astral characters
  and lone surrogates): `valid <=> non-empty literal substring`; `fact[start:end] == quote`; `start == fact.find(quote)`;
  no hidden folding on either side. 4 properties, all pass.
- NFC quote vs NFD fact and the reverse: both rejected. Homoglyph, zero-width, soft hyphen, bidi override: rejected.
  Lone surrogate halves and the two halves as separate code points vs an astral character: rejected in both directions.
  A quote straddling two facts, or a real substring of F2 cited against F1: rejected (`quote_not_found`).
  Look-alike fact ids (`f1`, `F01`, ` F1`, `F1​`, fullwidth, combining): all `unknown_fact`, never a nearest match.
  Non-string quote or fact id: raises `TypeError` or is invalid, never valid. Judge-supplied `start`/`end`: ignored.
  `check_judgment` accepts only the exact string `established`.
- Byte vs code-point confusion: the check itself is consistent (code points). The hazard is downstream (H-12).

**On content: yes, weakly.** The check proves the quote appears, not that it says anything. A single space, a newline, one
letter, one comma, an invisible U+200B, or a lone combining mark is accepted as evidence (H-05). And for the house judge
the quote is the whole named fact (`quote_source: "whole_fact"`, `nyaya_judges.py:482`), so the check can only reject an
unknown fact id: grounding there rests on `p_established` alone (observation O-2, pinned by
`test_house_judge_quote_is_the_whole_fact_so_the_check_cannot_reject_a_real_id`).

## Audit table: every absent-value path

"Fails closed" means absence or a bad value ends in raise, ABSTAIN, REFER or not-established. Line numbers are
`nyaya_agent.py` / `nyaya_judges.py` / `nyaya_lean_registry.py` / `nyaya_quote.py` at `b9f0435`. Tests are in
`tests/test_nyaya_failopen_audit.py` unless stated.

| # | Path | Where | Verdict | Test |
|---|---|---|---|---|
| 1 | Judge raises on every attempt | `_judge_element` -> `error`, `_run_contract` :1353 | fails closed (ABSTAIN `judge_error`, no Lean call, `p_established` None) | `TestFailsClosed::test_judge_raising_on_every_attempt...` |
| 2 | Denial judge raises | same | fails closed | `test_denial_judge_error_abstains_not_proof` |
| 3 | Unreadable judge reply (`JudgeOutputError`) | `_judge_element` | fails closed | `test_judge_output_error_is_an_error_not_a_status` |
| 4 | `established` with no fact id / no quote | `locate_quote` `no_quote` | fails closed | `test_established_without_fact_id...` |
| 5 | `established` with unknown fact id | `locate_quote` `unknown_fact` | fails closed | `test_established_with_unknown_fact_id...` |
| 6 | Denial claimed but quote invalid | `_run_contract` :1384 | fails closed (REFER `denial_unquotable`) | `test_denial_claimed_but_unquotable_refers` |
| 7 | Denial p in refer band | `in_band`, :1369 | fails closed (REFER `uncertain`) | `test_denial_in_the_refer_band_refers` |
| 8 | Lean vs local outcome disagree | :1381 | fails closed (ABSTAIN) | `test_lean_and_local_disagreement_abstains` |
| 9 | Lean answer missing a key / unlicensed claim | `outcome_from_lean` :506 | fails closed (raises) | `test_outcome_from_lean_raises...` |
| 10 | Unrecognised Lean verdict string | `outcome_from_lean` | fails closed (ABSTAIN, never PROOF) | `test_unrecognised_lean_verdict_is_never_proof` |
| 11 | Malformed `score` output | `check_registry` :231 | fails closed (raises) | `test_malformed_score_output_raises` |
| 12 | `--describe-contract` with no elements | `parse_describe_output` :137 | fails closed (raises) | `test_describe_with_no_elements_raises` |
| 13 | No/empty/prose/low-mass logprobs | `p_established_from_top_logprobs` :204, `HouseJudge.judge` | fails closed (`JudgeOutputError`) | `test_house_judge_refuses_unreadable_logprobs` |
| 14 | **NaN or positive logprob** | same | **FAILS OPEN** (H-01) | `TestDefectH01InvalidLogprobs` |
| 15 | Frontier reply unreadable / bad status / non-str quote | `parse_frontier_reply` :504 | fails closed | `test_frontier_parse_refuses_unreadable_replies` |
| 16 | Frontier `established` without a quote | `locate_quote` | fails closed | `test_frontier_established_without_quote...` |
| 17 | **Frontier reply with prose around the JSON** | `_JSON_OBJECT.search` :505 | **FAILS OPEN** (H-07) | `test_DEFECT_H07_*` |
| 18 | **Frontier probability absent** | :516, :524 | **FAILS OPEN / invented number** (H-11) | `TestDefectH11InventedConfidence` |
| 19 | Second judge errors | `AndGateJudge.judge` :735 area | fails closed (REFER `second_judge_unavailable`) | `test_second_judge_unavailable_refers` |
| 20 | **Second judge says no to a DEFEATER the primary found** | `AndGateJudge.judge` :735, `_judge_element` :1183 | **FAILS OPEN** (H-02) | `TestDefectH02AndGateDropsDefeaters` |
| 21 | Gate 1 model errors (both modes) | `Gate1Judge.judge` | fails closed (REFER `gate1_unavailable`) | `test_gate1_model_error_refers_in_both_modes` |
| 22 | **Gate 1 NaN contradiction score** | `gate1_contradiction_check` :899 | **FAILS OPEN** (H-03); entailment mode fails closed by accident of `nan >= t` being False | `TestDefectH03H04...` |
| 23 | **Gate 1 model without the NLI label names** | `score_contradiction_one` :963, `score_one` :955 | **FAILS OPEN** (H-04) in contradiction mode; closed by coincidence in entailment mode | same |
| 24 | **Gate 1 unresolvable fact id** | `Gate1Judge.judge` :1033 | **FAILS OPEN at the judge layer** (H-15); the agent catches it downstream | `TestDefectH15...` |
| 25 | Empty / whitespace-only fact (Unicode whitespace) | `ingest_facts` :394 | fails closed (raises) | `test_ingest_refuses_empty_and_unicode_whitespace_only_facts` |
| 26 | **Invisible-only fact (ZWSP, U+2060, U+FEFF, soft hyphen, RLO)** | `ingest_facts` | **FAILS OPEN** (H-06) | `TestDefectH05H06InvisibleEvidence` |
| 27 | Lone-surrogate fact | `_sha` | fails closed (raises `UnicodeEncodeError`, a `ValueError`, API maps to 422) | `test_lone_surrogate_fact_raises...` |
| 28 | **Degenerate quote (space, newline, 1 letter, ZWSP, combining mark)** | `locate_quote` | **FAILS OPEN** (H-05) | `tests/test_nyaya_quote_adversarial.py::TestDegenerateQuotes` |
| 29 | Empty quote | `locate_quote` | fails closed (`empty_quote`) | `test_empty_quote_is_still_refused` |
| 30 | Contract id / section not registered (IPC 318, 316, 85, 498A, BNSS 482, look-alikes) | `select_contracts` :456 | fails closed (raises) | `TestMixedIpcBnsRouting` |
| 31 | **Empty selection / empty `--list-contracts`** | `select_contracts` | **FAILS OPEN** (H-08): a run over zero contracts succeeds | `TestDefectH08EmptySelection` |
| 32 | `sections=[]` | `select_contracts` | fails closed (raises) | `test_control_empty_sections_already_raises` |
| 33 | **`refer_band` high < `tau`** | `AgentConfig.__post_init__` :167 | **FAILS OPEN** (H-09) for defeaters scored in [high, tau) | `TestDefectH09BandGap` |
| 34 | **No `pinned_score_sha256` in config** | `load_agent_config` :353, `BinaryRegistry` :440 | **FAILS OPEN** (H-13): absent pin loads as "accept any binary" | `TestDefectH13UnpinnedBinary` |
| 35 | **Missing denial / element verdict, empty element list, duplicate element names** | `assemble_assertions` :485, `expected_outcome` :496, `_run_contract` | **FAILS OPEN / defaults** (H-10); unreachable through `run()` with the real binary's output | `TestDefectH10AssemblyDefaults` |
| 36 | `validated_contracts` absent | `load_agent_config` | fails closed (empty allowlist -> REFER) | `test_unvalidated_contract_never_reaches_proof` |
| 37 | Missing `refer_band` / `tau` / `max_retries` / `house_judge` keys | `load_agent_config` | fails closed (bare subscripts raise) | reading only, not separately tested |
| 38 | Offsets returned without unit or base | `ElementResultOut` | **hazard** (H-12) | `TestDefectH12OffsetUnit` |
| 39 | Fact text forging a fact header or `Answer:` in the prompt | `build_house_prompt` :190 | **hardening gap** (H-14); quote check still uses the real fact map | `TestInjection` |

## Named adversarial inputs, one by one

- **Unicode and offset tricks against the quote check**: see the section above. Cannot be fooled on appearance. H-05, H-12.
- **Prompt-injection text in facts**:
  - House judge: the verdict is read from first-token logprobs, not from free text, so a fact cannot write the verdict;
    `parse_house_fact_id` ignores a forged `F_narrative`. Not testable without the served model (not run, see below).
  - Fact text CAN forge `[F2]` headers and the `Answer:` terminator in the prompt, unescaped (H-14). Bounded: a judge that
    cites a forged id gets `unknown_fact` (`test_the_injected_text_cannot_add_a_fact...`), and a quote lifted from injected
    text is valid only against the fact that holds it (`test_a_quote_lifted_from_injected_text...`).
  - Frontier judge: the parser takes the first `{` to the last `}` and accepts surrounding prose, so a model that echoes an
    injected JSON verdict while refusing it is read as `established` (H-07). Two separate objects in one reply are refused.
- **Empty facts**: refused by `ingest_facts` for every Unicode whitespace, but not for invisible characters (H-06).
- **Oversize facts**: the engine has no size limit (the 4000-char / 8-fact cap is only in `api/partner.py`
  `AnalyseFactsRequest`); recorded, `test_characterise_engine_has_no_size_limit_only_the_api_does`. A fact of about 280,000 characters still
  quote-checks exactly (`test_oversize_fact_quote_check_still_exact`). `_count_occurrences` is quadratic in the worst case
  (repeated-character fact and quote); bounded to about 16M comparisons by the API cap. Not graded: no timing test (flaky).
- **Duplicate fact ids**: `ingest_facts` cannot produce them (numbered `F1..Fn`; identical texts get distinct ids,
  `test_ingest_ids_are_unique_even_for_identical_texts`). A hand-built `JudgeRequest.facts` can: the house judge and the
  agent both take the LAST row silently (`test_characterise_hand_built_duplicate_fact_ids...`); unreachable via `run()`, not graded.
- **Mixed IPC/BNS citations** (RELAYED claims, verified against the repo, not against the statutes):
  `KNOWN_CONTRACT_IDS` has `bns316_*` and `bns318_*` (BNS sources) and `ipc405_*`, `ipc415_*` (IPC sources) and `bns85`
  (sources "BNS section 85; 86"); it has no `ipc316`, `ipc318`, `ipc85`, `ipc498a`, `bnss482` key, and has `bnss528`
  (`test_the_registry_has_no_ipc85_ipc316...`). `select_contracts` with `sections=["Indian Penal Code 318"]` (or 316, 85,
  498A, "Bharatiya Nagarik Suraksha Sanhita 482") raises `UnknownContractError`; the BNS/BNSS spelling selects only its own
  contract; prefixes, double spaces, case changes and a `str` where a list belongs all raise
  (`TestMixedIpcBnsRouting`, 29 cases). One note: a list mixing a valid and a bogus section silently serves the valid one
  (N-3, `test_a_mixed_request_with_one_bad_section_is_not_partially_served`). The claim that IPC 318/316 differ from BNS
  318/316 in meaning is legal, not something this repo can verify; what is verified is that the router never maps across.

## Defects

Each entry: severity, location, reproducing test, proposed fix (PROPOSAL, not applied). Tag's claim, pending verification.

### H-02 (high): the AND-gate drops a defeater the primary judge found, and the contract PROVES
- **Status: CONFIRMED by the lead, filed as AxisMeru/pravrudhi #152 (P0).** The lead read the
  same code, agrees there is no `is_denial` branch, and says a defeater being vetoed into a PROOF is not the intent. Repro credited
  to this branch at `c9bddb5`.
- **We did not change production behaviour.** `src/` is untouched on this branch. The fix is owned elsewhere. The test below is the PLANTED
  TEST FOR WEB'S FIX: it is `xfail(strict=True)` and expresses the intended behaviour, so it turns into a hard failure (XPASS)
  when the fix lands, which is the signal to remove the marker.
- Where: `AndGateJudge.judge` (`nyaya_judges.py:735`) ANDs the two statuses for every element, denials included;
  `_judge_element` (`nyaya_agent.py:1183`) sets `claimed = anchor.status == "established"` from that ANDed judgment;
  `assemble_assertions` (`:485`) puts a defeater on the wire only when established.
- Mechanism: primary establishes the defeater (p 0.95 >= tau 0.74), second says no (p below its tau 0.97). The ANDed status is
  `not_established`, so `claimed` is False, the defeater is not on the Lean wire, and with every element established the
  contract returns `PROOF` / `all_elements_established`. For required elements the AND is conservative (a veto means
  ABSTAIN). For defeaters the polarity is inverted: a veto REMOVES a refutation.
- Why no referral catches it (proved, not asserted; the lead asked for this), all in
  `test_H02_the_denial_unquotable_referral_does_not_fire_because_claimed_is_the_anded_verdict` and its neighbours @ `7d0ed03`:
  - `denial_unquotable` (`:1384`, `r.is_denial and r.claimed and r.status != "established"`): needs `claimed` True; it is
    False. The test asserts `claimed is False`, `status == "not_confirmed"`, `binding_leg == "second"`, `reason ==
    "all_elements_established"` and `reason != "denial_unquotable"`.
  - `uncertain` (`:1369`): reads the PRIMARY's p only; an established defeater has p >= tau, never inside `[0.5, tau)`. Empty.
  - `uncertain_second_judge`: fires only when `refer_logit_delta` is configured (default None, off) AND the second's p is
    within `delta` of its tau in logit distance. With delta 5.0 and second p 0.60 it does fire
    (`test_H02_only_mitigation_is_the_optional_logit_band`, asserts `uncertain_second == [DENY]`). It does not fire for a
    second p that is clearly "no": (p 0.60, delta 1.0), (p 0.05, delta 5.0), (p 0.30, delta 2.0) all still PROVE
    (`test_H02_the_logit_band_is_only_a_window_around_tau_second...`). So even when configured it is a partial mitigation.
    (A first draft of this test passed for the wrong reason: the second's ELEMENT scores were inside the band. The committed
    tests set element p to 0.999999 so only the defeater can drive the band.)
  - `second_judge_unavailable`: only when the second errors. Gate 1 referrals: only when Gate 1 is on. `contract_not_validated`: only for
    unvalidated contracts, and those skip the second judge anyway. All lists empty in the test.
  - The information existed: the audit trail records `second_status: not_established` and the primary's `p_established: 0.95`
    for the defeater; the element result keeps `p_established 0.95`. It was discarded by the AND.
- Planted test for the fix: `test_DEFECT_H02_planted_for_fix_a_defeater_disagreement_must_refer` (3 cases, second p 0.60 / 0.30 /
  0.05; asserts outcome `REFER_TO_LAWYER`, reason string deliberately left open, and that the defeater's element record still carries
  the primary's p 0.95). Currently PROOF. Controls that must keep passing after the fix:
  `test_H02_controls_for_the_planted_test_unanimous_defeater_calls_keep_their_outcomes` (both judges find the defeater: still
  DENIAL; primary says no: still PROOF).
- Accepted fix direction (the lead's, adopted here so there is one design): a second-judge disagreement on a defeater means
  REFER. Tag's earlier alternative (OR the two judges for defeaters) is withdrawn as a competing design.
- One scope note for the fix owner, pinned by the control above: when the PRIMARY says a defeater is absent, the second judge is never asked
  (the AND-gate's cost-saving skip), so the converse split (primary no, second would say yes) cannot be detected at all. Whether
  that is in scope for #152 is the fix owner's call; Tag only records it.
- Likelihood (unchanged): requires config C on (`second_judge` set by env in production per the 0.5.41 ledger row cited in code
  comments) and a validated contract with at least one defeater. The measured "false-prove 0/225" comes from a set this audit did
  not open, so whether it contained defeater-present cases is unknown to Tag.

### H-01 (medium): a NaN or positive logprob is accepted and the element is called established (found independently twice)
- **Status: already filed as #156** (from the typed-layer workstream's corroborating test). This entry keeps this branch's repro and the
  reachability question, which is the open part. Also found independently by the typed-layer workstream as its F5b (`tag/night-typed-133`,
  `TYPED-LAYER-FINDINGS-2026-09-30.md`, test `test_f5b_house_judge_refuses_a_nan_label_logprob`). Two independent discoveries;
  it was filed once, as #156, and removed from the typed-layer draft list (coordinator's instruction). Fix B below
  (per-token validity check) is this branch's addition to their one-liner.
- Where: `p_established_from_top_logprobs` (`nyaya_judges.py:204`). `est = max(...)` is NaN, `est == -inf` is False, the top-1
  check passes, `label_mass = exp(nan) = nan`, `nan < floor` is False, so the guard passes; `p = nan`; `nan < tau` is False, so
  `HouseJudge.judge` returns `established`. `in_band(nan)` is False, so no refer band catches it: the whole agent returns PROOF.
- Reproductions: `TestDefectH01InvalidLogprobs` (4 unit cases: NaN est, NaN not, est = +5.0, est = +0.001; 1 agent-level case).
- **Is it reachable from a served judge? Tag's answer: not shown, and not with a correctly behaving stock server.**
  - Shown by test: the production client does NOT block it. `ChatClient.complete` parses with stdlib `json.loads`, which
    accepts the bare literal `NaN`, then `float()` (`test_characterise_H01_a_NaN_literal_on_the_wire_survives_the_real_client_and_is_established`
    drives the REAL `ChatClient` with only the socket replaced; result `established`, `p = nan`).
  - Shown by test: a server that writes `null` for NaN (what pydantic's default `ser_json_inf_nan="null"` does) is refused
    (`float(None)` raises, `HouseJudge` re-raises as `RuntimeError`, an element judge error):
    `test_H01_control_a_null_logprob...`.
  - NOT shown: whether the real served stack (vLLM on the 5090, the RunPod endpoint) can emit a NaN literal or a positive logprob.
    No judge traffic was allowed, so this is unverified. From code reading only: a positive logprob is impossible from a correct
    server, and NaN would need NaN logits (an fp16/bf16 numerical fault) that also survive that server's JSON serialiser.
    So it needs a malformed or faulty reply: hence **medium**, not high. The consequence if it does happen is a PROOF, which is
    why it is worth a two-line fix. The test doubles are in tests only.
- Fixes compared (both applied temporarily to a scratch copy, then reverted; `src/` is untouched on the branch; run against the
  five H-01 repros with `--runxfail` and against `tests/test_nyaya_judges.py`, `test_nyaya_agent.py`, `test_nyaya_and_gate.py`,
  `test_nyaya_judge_fallback.py`, `test_nyaya_agent_typed_flag.py`):
  - **A** (typed-layer workstream's one-liner): `if not math.isfinite(label_mass) or not label_mass >= label_mass_floor: raise`.
    Closes the NaN cases (3 of 5 repros) and `+inf`. It does NOT close positive logprobs: est = +5.0 gives mass 148, finite and
    above the floor; est = +0.001 gives mass 1.019. 2 of 5 repros still fail.
  - **B** (kept): A plus a per-token validity check before anything else:
    `if any(math.isnan(v) or v > 0.0 for v in top.values()): raise JudgeOutputError(...)`. All 5 repros pass (the two
    `test_characterise_H01_*` tests, which pin the old behaviour, then fail as intended); 239 existing tests still pass, 10 skip.
    Reason for keeping B: a log-probability above 0 is impossible, so a mass above 1 or a token above 0 is evidence the reply
    is corrupt, and the same validity check costs nothing. Rounding cannot produce a value above 0.0, so no tolerance is needed.
  - Same hole, same file: a NaN `label_mass_floor` disables the guard (typed-layer F6 covers `TypedHouseJudge`; `HouseJudge.__init__`
    has no bounds check either). Not separately tested here; same one-line class of fix (`0 <= floor <= 1`).

### H-04 (medium): a Gate 1 model without the NLI label names is scored 0.0, and contradiction mode never vetoes
- Where: `Gate1NLIModel.score_contradiction_one` (`nyaya_judges.py:963`) returns `float(p.get("contradiction", 0.0))`; `score_one`
  (`:955`) defaults `entailment` and `contradiction` to 0.0. `NYAYA_GATE1_MODEL` can swap the model without touching the pinned
  revision's label expectations. A label set such as `label_0/1/2` yields 0.0, i.e. "no contradiction", silently: the gate is disarmed.
  In entailment mode the same 0.0 is below the 0.0407 threshold, so it vetoes: closed by coincidence only.
- Reproductions: `test_DEFECT_H04_label_set_without_the_expected_labels_must_raise` (2 cases); consequence pinned by
  `test_characterise_H04_a_relabelled_model_disarms_contradiction_veto`.
- Likelihood: Gate 1 is off by default (`NYAYA_GATE1_ENABLED`) and in no shipped config; needs a model override.
- Proposed fix: index `p["contradiction"]` / `p["entailment"]` with bare subscripts (KeyError), or check
  `{"entailment","neutral","contradiction"} <= set(labels)` once in `_ensure_loaded` and raise.

### H-05 (medium): degenerate quotes are accepted as evidence
- Where: `locate_quote` (`nyaya_quote.py:41`) accepts any non-empty substring.
- Reproductions: `tests/test_nyaya_quote_adversarial.py::TestDegenerateQuotes::test_DEFECT_degenerate_quote_is_accepted_as_evidence`
  (7 cases): single space, bare newline, one letter, one comma, an invisible U+200B, a lone combining mark, a run of spaces.
- Why it matters: with the frontier judge (p is always 1.0) a claim that quotes `" "` or an invisible character is
  `established` with `quote_check: ok`. Combined with H-06 a fact made only of invisible characters can be cited. The house judge is
  unaffected (whole-fact quotes).
- Proposed fix: reject a quote whose visible content is empty or below a minimum: for example `quote.strip()` empty, or no character in
  Unicode categories L or N, or `len(quote.strip()) < N`. `N` is a threshold and so a PROPOSAL needing a constructed set to fit;
  Tag has not measured one and does not recommend a number.

### H-07 (medium): the frontier parser accepts prose around the JSON, so an echoed injection becomes the verdict
- Where: `parse_frontier_reply` (`nyaya_judges.py:504`): `_JSON_OBJECT = \{.*\}` (first `{` to last `}`), the prompt's "ONE JSON
  object and nothing else" is not enforced.
- Reproductions: `test_DEFECT_H07_frontier_reply_with_surrounding_prose_is_accepted` (3 cases). Two separate JSON objects in one
  reply are already refused (`test_frontier_two_objects_in_one_reply_is_refused`).
- Not shown: that a real model echoes an injected verdict. The parser's acceptance is what is reproduced.
  Bounded by the quote check (the quote must be verbatim in the named fact) but the fact is the attacker's text.
- Proposed fix: require the whole reply (after stripping a single optional ```json fence) to be one JSON object.
  `test_frontier_pure_json_and_fenced_json_still_parse` pins that normal and fenced replies keep working.

### H-09 (medium): `refer_band` is not validated against `tau`
- Where: `AgentConfig.__post_init__` (`nyaya_agent.py:167`) checks the band and tau independently.
- Mechanism: with `high < tau` a score in `[high, tau)` is neither established nor in the refer band. For a DEFEATER that means
  "absent", so the contract can PROVE. Reproduction: `test_DEFECT_H09_config_must_refuse_a_band_that_stops_short_of_tau`;
  consequence pinned by `test_characterise_H09_consequence_of_a_gapped_band_is_proof_over_a_defeater_scored_0_70`
  (the gap is forced with `object.__setattr__` to bypass `__post_init__`).
- The shipped config is fine: `configs/nyaya_agent.yaml` has `tau: 0.74`, `refer_band: [0.5, 0.74]`
  (`test_control_the_shipped_yaml_band_is_not_gapped`). This is a latent config-edit hazard.
- Proposed fix: in `__post_init__`, `if high < self.tau: raise ValueError(...)`. No threshold changes.

### H-13 (medium): a config with no `pinned_score_sha256` loads as "accept any binary"
- Where: `load_agent_config` (`nyaya_agent.py:353`) `body.get("pinned_score_sha256")` -> None; `BinaryRegistry` (`:440`) skips the
  check when None. Reproduction: `test_DEFECT_H13_load_agent_config_must_refuse_a_config_with_no_pin`; current behaviour pinned by
  `test_characterise_H13_registry_accepts_any_binary_when_the_pin_is_none`.
- The shipped config has a pin (`test_control_the_shipped_config_names_a_pin`, and `tests/test_config_files.py` guards it).
  Many existing tests construct an unpinned registry on purpose, so the fix must keep an explicit opt-out.
- Proposed fix: `load_agent_config` raises when the key is absent; an explicit `pinned_score_sha256: null` plus an
  `allow_unpinned: true` key (or an env var) is the opt-out. `BinaryRegistry(pinned_sha256=None)` stays valid for tests.

### H-03 (low): a NaN Gate 1 contradiction score is not a veto and not "unavailable"
- Where: `gate1_contradiction_check` (`nyaya_judges.py:899`) `vetoed=score >= tau_c`; NaN >= x is False. Entailment mode
  (`passed = score >= threshold`) fails closed for the same NaN, by accident of comparison direction.
- Reproduction: `test_DEFECT_H03_nan_contradiction_score_must_not_leave_the_element_established`; control
  `test_H03_control_nan_entailment_score_fails_closed_today`. Low: Gate 1 is off by default and a CPU fp32 model producing NaN is unlikely.
- Proposed fix: `if not math.isfinite(score): raise` inside both check functions, so the existing `except Exception` turns it into
  `gate1_unavailable` (REFER).

### H-06 (low): a fact made only of invisible characters is ingested
- Where: `ingest_facts` (`nyaya_agent.py:394`) uses `str.strip()`, which does not remove zero-width or format characters; the API's
  `f.strip()` check is the same.
- Reproductions: `test_DEFECT_H06_invisible_only_fact_must_be_refused` (8 cases); consequence
  `test_characterise_H06_an_invisible_fact_can_be_cited_and_quoted`. Unicode whitespace (NBSP, ideographic space, line separator) is
  already refused.
- Proposed fix: after strip, require at least one character whose category is not `Z*` / `C*` / `Mn`; raise `ValueError("fact N is empty")`.

### H-08 (low): an empty selection produces a successful, empty run
- Where: `select_contracts` (`nyaya_agent.py:456`). An empty `--list-contracts` with no selector, or `contract_ids=[]`, returns `[]`;
  `run` then yields an `AgentRun` with no contracts. `sections=[]` already raises, so it is also inconsistent.
- Reproductions: `TestDefectH08EmptySelection` (3 xfail; `test_characterise_H08_the_empty_run_carries_no_contracts_and_no_warning`).
- Low: through the API `contract_ids` has `min_length=1` and an id must be listed, so the empty run is not reachable there.
- Proposed fix: raise `UnknownContractError` when the chosen list is empty.

### H-10 (low): assembly defaults an absent verdict instead of raising
- Where: `assemble_assertions` (`:485`, `established.get(e, False)`, `.get(d, False)`), `expected_outcome` (`:496`, `all([])` is True).
- Reproductions: `TestDefectH10AssemblyDefaults` (4): (a) a denial with no verdict is read as "defeater absent" and the wire is
  PROOF-shaped (this direction is the fail-open one); (b) a required element with no verdict is silently False (safe direction, but
  against the house rule); (c) a contract with no required elements is a vacuous PROOF; (d) duplicate element names collapse and
  the last verdict wins.
- Unreachable through `run()` with a real binary: every task yields a result, `parse_describe_output` refuses empty element lists.
  Low because the caller is internal, and these are public functions.
- Proposed fix: `assemble_assertions` raises `KeyError` for any element or denial name missing from `established`; `expected_outcome`
  raises on an empty `elements`; `_run_contract` refuses duplicate names in a described contract.

### H-11 (low): the frontier judge reports a probability it was never given
- Where: `parse_frontier_reply` (`nyaya_judges.py:516`, `:524`) returns `p_established` 1.0 or 0.0. House rule: a missing confidence is
  None, excluded and counted. Effect: a frontier claim can never fall in the refer band (`test_characterise_H11_...`).
- Reproductions: `TestDefectH11InventedConfidence` (2). Documented in the module docstring, so this is a design deviation, not a slip.
- Proposed fix: `p_established: float | None` on `ElementJudgment`; frontier sets None; `_run_contract` counts elements with no p
  (`uncertain_unscored`) and decides whether to REFER them. That touches the type used everywhere, so it is a design decision.

### H-12 (low): offsets are returned with no unit and no base
- Where: `ElementResultOut.start/end` (`api/partner.py`). They are code-point indices into the STRIPPED fact text
  (`ingest_facts` strips, and the response lists only `{id, sha256}`). A UTF-16 (JavaScript) or UTF-8 byte consumer, or one slicing
  its original un-stripped text, selects a different span.
- Reproductions: `test_DEFECT_H12_response_model_declares_the_offset_unit` (xfail);
  `test_the_three_offset_units_disagree_after_an_astral_character` and `test_leading_whitespace_offsets_are_relative_to_the_stripped_fact`
  (quote file, pin the hazard: `(3, 5, 9)` code-point / UTF-16 / UTF-8 for the same position). No in-repo consumer slices with them
  (`app/frontend` renders the quote text), so low.
- Proposed fix: add `offset_unit: "codepoint"` and either the stripped fact text or `leading_trim` to the response; document both.

### H-14 (low): fact text can forge fact headers and the `Answer:` terminator in the judge prompt
- Where: `build_house_prompt` (`nyaya_judges.py:190`) interpolates fact text raw. Reproduction:
  `test_DEFECT_H14_fact_text_must_not_forge_a_fact_header` (xfail); the prompt structure is pinned by
  `test_characterise_fact_text_can_forge_fact_headers_and_the_answer_delimiter`.
- Bounded (see "Named adversarial inputs"): the quote check and the real fact map stand between a forged header and a verdict.
  The house prompt is "byte for byte the training prompt", so a fix must not change any normal prompt
  (`test_H14_control_a_normal_prompt_is_unchanged_by_any_fix`).
- Proposed fix: reject or neutralise fact lines that begin `[F<digits>] ` or equal `Answer:` at `ingest_facts`, rather than in the prompt builder.

### H-15 (low): `Gate1Judge` passes an unresolvable fact id through as established
- Where: `Gate1Judge.judge` (`nyaya_judges.py:1033`) returns the judgment unchanged when its fact id does not resolve, on the comment that
  the quote check will reject it. The agent does (`test_control_the_agent_still_rejects_it_downstream`); another caller would not.
- Reproduction: `test_DEFECT_H15_unresolvable_fact_id_must_not_stay_established` (xfail).
- Proposed fix: return `status="not_established"`, `vetoed_by="gate1"`, `gate1_skip_reason="gate1_unavailable: unresolvable fact id"`.

## Unverified observations (ungraded, no severity)

- O-1 (by design, documented in the module docstring, pinned by `test_O1_retry_uses_attempt_ones_status...`): a retry is used only for the
  quote, so an element can be established on attempt 1's `p` with attempt 2's evidence, possibly a different fact.
- O-2: the house judge's quote is the whole fact, so the quote check is tautological for it (see above).
- O-3: in select-all mode a listed-but-unknown contract id is silently not selected (`test_O3_...`), which is documented; a stale binary
  is caught only by the pin.
- O-4: `_count_occurrences` worst case is quadratic (see "Oversize"); no timing test, so no grade.
- O-5 (not checked): the typed judge path (`TypedHouseJudge`) is the typed-layer workstream's scope; this audit did not test it.
- O-6 (not checked): the real `score` binary was not run (none on this host); Lean-side behaviour is taken from the scripted registry
  and the `requires_score_bin` tests were skipped.
