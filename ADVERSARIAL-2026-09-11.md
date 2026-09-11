# Adversarial Review: Pravrudhi — 2026-09-11

**Reviewer role**: adversarial, external to the builder's reasoning. Ten independent finders on distinct attack
surfaces, one independent refuter per non-trivial finding (35 refutations, 10 findings killed), a completeness
critic, and every critical or high finding re-verified by hand against the code and the ledger before it was
written here. Evaluated against CLAUDE.md, CHARTER §6, the ADRs, `gates/*.json` and `research/ledger.jsonl`,
not against the project's own narrative.

**Inputs read first**: the "Project onboarding takeover" session (2026-09-10/11), HANDOFF.md top block,
RESTART.md, ADVERSARIAL.md (2026-09-06), ADR-0040 through ADR-0046.

**Not committed.** This file is a review, not a decision. Nothing below was fixed.

---

## Verdict at a glance

| Question | Verdict |
|---|---|
| Is any improvement claim supportable at any tier right now? | **No.** The casehold +0.1678 is disowned by HANDOFF and refuted externally; iltur has plateaued at +0.0131; the one clean external result (GSM8K +0.081, single seed) predates this period and is unchanged. |
| Does the loop compound? | **No, not through the ledger.** Every harness `night_start` row in the ledger records incumbent `c-0000`, including the nights after `c-0229`, `c-0240`, `c-0238` and `c-0251` were promoted. Inheritance works only when a human copies a recipe into a config-named baseline file. |
| Is the kernel sealed? | **Contested by the project's own artefacts.** 14 kernel commits since gate_P0 reached `status: pass`; gate_P0's sign-off layer is `pending` with no signer; CLAUDE.md still carries the exemption that "must not be restated". |
| Is authority delegation enforceable? | **No.** The `/inbox/sign` API route treats `agent-for-operator` as a human name and skips every delegation condition. |
| Is the public repo clean? | **No.** A live Telegram bot token and chat id are in the tracked, published `demo.json`. |

---

## Critical

### C1. Telegram bot token and chat id published in `app/frontend/public/demo.json`

`demo.json` (tracked, committed 17 times in two days as "Refresh the recorded snapshot", served on Pages)
contains a BotFather token of the form `8643311100:AA…` at line ~70451 and the paired chat id at lines 69554 and
70339. Root cause: the operator pasted the BotFather reply into a request; `demo_export.py` exports request
text unfiltered. The repository `AxisMeru/pravrudhi` answers an anonymous `git ls-remote`, so it is public.

*Failure*: anyone can message as `@prabhasa_bot`, including into the operator's own chat, and the doctor
`telegram` check will report it healthy.
*Next*: revoke and regenerate at BotFather; purge from history (BFG); add a secret scan to `demo_export.py`
before write (token regex, `sk-`, `DASHSCOPE`), and a test that the export refuses to write a matching string.

### C2. `/inbox/sign` treats `agent-for-operator` as a human and skips every delegation condition

`src/pravrudhi/api/server.py:114` defines its own `AGENT_IDENTITIES = {"pravrudhi-agent","agent","claude"}`;
`src/pravrudhi/application/delegation.py:31` defines the authoritative set **with** `agent-for-operator`;
`src/pravrudhi/application/gate.py:16` has a third copy without it. At `server.py:1169` the route enters the
delegation branch only when `who.lower() in AGENT_IDENTITIES`. A request with header
`X-Pravrudhi-Operator: agent-for-operator` therefore falls through to the human path: no `permits()`, no
`unmet_conditions()`, no badge check, and the ledger actor is recorded as `human:agent-for-operator`, which is
exactly the indistinguishability ADR-0040 forbids. No test covers the route with that identity
(`tests/test_delegated_signoff.py` tests the CLI path only). `gate.sign_gate` has the mirror defect: passing
`agent-for-operator` bypasses the "sign-off is a human act" refusal at `gate.py:70`.

*Next*: delete the two local copies, import from `delegation.py`, add the route test.

### C3. The sealed-kernel premise does not hold, and the project cannot say which reading is true

`git log --since=2026-09-05 -- pravrudhi_kernel` lists 14 commits after `gate_P0.json` reached `status: pass`
(eight on 2026-09-05, six on 2026-09-10, including ADR-0035/0036/0038/0039/0041/0042 and 7acc083's Actor-enum
widening). Meanwhile `gate_P0.json.closure.signoff.verdict` is `pending`, `signoff.by` is `null`, and the same
is true of L1–L5. gate_P0's own integrity layer says "epoch-0 exemption ends when this gate is signed";
ADR-0035 reinterprets that as `status: pass`; CLAUDE.md:17 still carries the exemption text that "every session
after that must not restate it". HANDOFF says "kernel sealed"; ADR-0046 stopped at the boundary on that basis
while ADR-0042 crossed it the same day under the delegation.

Either (a) P0 is closed, the kernel was sealed, and 14 commits violated the Sākṣī third rule; or (b) P0 was
never signed, the exemption never ended, and every post-P0 claim rests on unsigned gates. Both cannot be
avoided at once. The delegation since 2026-09-10 permits signing them and nobody has.

*Next*: decide (b) or (a) in an ADR, sign L1–L5/P0 as `agent-for-operator` if (a), remove CLAUDE.md:17–18, and
record every kernel commit since as an accepted ADR or a reverted one.

---

## High

### H1. Harness compounding through the ledger is disabled; every night starts from `c-0000`

`HarnessRecipe.harness_json()` (`src/pravrudhi/targets/harness_grammar.py:55`) excludes `strategy` and
`execution_family`, both required. The `promote` payload written to the ledger is that JSON. At
`harness_track.py:766–775` the night loads its incumbent by `parse_harness(ev.payload["harness"])`, which
returns an error string for every promote row, is silently discarded, and `ctx.incumbent_id` stays `c-0000`.

Ledger evidence (harness `night_start` rows): seq 2666, 2760, 2923, 2943, 2999, 3058, 3156, 3273, 3397, 3480,
3567 all record `incumbent: c-0000`, straddling promotions at 2861 (`c-0206`), 3111 (`c-0229`), 3342 (`c-0240`),
3347 (`c-0238`), 3467 (`c-0251`). ADR-0044 "inheritance" works only through `baseline_recipe:` in the prereg
pointing at a hand-copied file (`harness/agent/baselines/iltur-lsi-dev-c0251.json`), so the ledger then labels
candidates' `lineage` as `[c-0000]` while they are actually paired against `c-0251`'s recipe. HANDOFF records
this defect and defers it as "kernel business"; it is engine code, not T0.

*Next*: write `strategy`/`execution_family` into the promote payload (or stop excluding them); add a
round-trip test `parse_harness(rec.harness_json())`; emit an `incumbent_load_failed` audit row when parsing fails
instead of falling back silently; re-label lineage when a config baseline is a promoted recipe.

### H2. ADR-0046 misattributes the scorer defect; the taint reaches back to ADR-0035, not ADR-0042

Reproduced: `score_item('D. holding that absent an injury','D')` → 0, `'D) the claim fails'` → 0. But
`_disqualified` (`mmlu.py:67–69`) fires **only** for the letter `I`. The real cause is `_PATTERNS`
(`mmlu.py:30–37`): every pattern requires the word "answer", "option", "choice" or `\boxed`, and `_ALONE` only
matches a letter standing alone on the last line. A labelled option followed by its text matches nothing and
has matched nothing since the choice scorer was added by ADR-0035. Consequences: (1) ADR-0046's preferred fix
(option 2, an explicit labelled-answer pattern) is right, its reasoning and its "narrow ADR-0042" option are
wrong; (2) every casehold number since the pool was sealed, including night 23's floor (0.3322) and the
promotions of `c-0229` and `c-0240`, was produced by a parser that cannot read this reply shape, not only the
`c-0238` measurement; (3) the internal 0.5000 for `c-0238` and the kernel's 0.0040 for the same recipe on the
test split are not yet reconciled by anyone; the refuter's sample count found 0% bare letters in the `c-0238`
external completions, which contradicts the "learned to emit a bare letter" story in HANDOFF. Something else
explains the internal number, and until it is found the casehold track has no usable measurement at all.

*Next*: amend ADR-0046's cause; re-score the stored val-pool completions of both arms with both parsers to find
where 0.5000 came from; hold casehold as already ordered.

### H3. A crashing completion gate is re-run every beat with no budget and starves the backlog

`heartbeat.py:498` catches a gate exception and returns with the request still `delivered`;
`requests.py:404–415` then returns the same `verify_request` obligation on the next beat because bfd46de only
skips `parked_request` rows. Each retry builds a new review agent (`heartbeat.py:474`). Unlike criteria, gates
have no `MAX_*_ATTEMPTS`. This is the same livelock shape bfd46de fixed, one state further along, with a
per-hour dispatch cost attached.

### H4. Criterion attempts are consumed before dispatch succeeds

`heartbeat.py:665` records the attempt, then `swarm.run_wave` may return `accepted=False` for a dispatch-level
reason (workspace race, validation) and return at 674 before any judge runs. Three transient failures park a
solvable criterion permanently, with no operator-visible reset.

### H5. Paired observations do not record the incumbent adapter's hash

`execute.py:485–488`: the incumbent arm's `harness` hash is the base snapshot, never
`model_dir_hash(ctx.incumbent_adapter)`, and `harness_parent` is never populated. Replay's rebase detection
therefore cannot notice when a candidate's seeds were measured against two different incumbents, which is the
HANDOFF-noted "pooled across comparators into one n=2 confirm" defect made permanent.

### H6. Statistical guards that exist only in config

* `Variance.min_n_confirm` defaults to 1 (`sequential.py:31`); every prereg sets 2, nothing prevents a config
  that omits it from confirming on one seed.
* The floor completeness check (`harness_track.py:661–668`) accepts `--rotations 1 --seeds 1` and writes
  `sigma_seed 0.0`, making `delta_min` pure configuration. HANDOFF names this; it is unfixed.
* No family-wise correction: all candidates in a night share `lineage=[incumbent]` and surface, each is tested at
  `alpha_eff=0.05`, and the ledger records `holm:{m:1}`. The CHARTER's Holm-at-family-closure is not implemented.
* `delta_min` is derived from `sigma_seed` alone; on prabhasa-nyaya `sigma_rot` 0.0312 sits 0.0028 under the
  0.034 boundary and nothing validates the relation if `exposure_cap` changes.

### H7. Pre-registration is not under version control anywhere

`.gitignore` excludes `research/`, `gates/`, `contracts/`, `HANDOFF.md`. Every prereg YAML, every gate JSON
and the ledger live only on this machine. The ledger-kernel finder also found `variance.json`'s hash no longer
matches the attestation in `gate_L3.json`. "Pre-registered" is therefore not a checkable claim to anyone but
the operator, which was recommendation 1 of the 2026-09-06 review and is unaddressed.

---

## Medium

* **M1** Credential isolation (`PRAVRUDHI_CLAUDE_CONFIG_DIR`) exists only in systemd drop-ins outside the repo.
  `deploy/systemd/install.sh` does not set it; a fresh `pravrudhi init` reproduces the shared-credential hazard.
* **M2** `interp_claim: false` in `configs/delegation.yaml` is enforced nowhere; the route hardcodes
  `scope="promote_T2"` for every pack.
* **M3** `gate_signoff` conditions in `delegation.yaml` are defined and never read (no autonomous gate close
  path uses them).
* **M4** Model-track `night_plan.py` does not check that the floor was measured on the inherited adapter, while
  the harness track does (`harness_track.py:156–166`).
* **M5** Desktop app work proceeds under ADR-0031 without a card or gate; PRD/ROADMAP say "after the multi-user
  layer" and M3 is still open.
* **M6** ADVERSARIAL.md (2026-09-06) recommendations 1 (version-controlled evidence) and 4 (head-to-head
  contract) remain unaddressed; 2 (L4 blocking findings) is partially closed; 3 was acted on.
* **M7** RESTART.md says "expect 1964 passed"; collection now yields 2268. Hand-off docs drift within a day,
  as memory already warns.

## Refuted (recorded so they are not re-raised)

* "ADR-0042 disqualifies every letter" — false, see H2.
* "c-0238 is the incumbent future nights inherit" — false, nothing is inherited from the ledger (H1);
  c-0251 is the hand-adopted iltur baseline.
* "The +0.1678 is published" — the number appears only in local `variance_*.json`, `scripts/ext_casehold.sh`
  and two tests; `demo.json` mentions 0.5000 only inside a chat transcript that argues against it.
* "Codex bypasses credential isolation" — deliberate, tested, its own store.
* "CLAUDE.md's second-failure TRIZ rule is violated by gate retries" — that rule is about card gates.
* "Proposer schema lacks numeric bounds" — intentional and tested.
* API surface: localguard, JWT, roles, path validation and update signatures held up under a dedicated finder;
  no vulnerability found beyond C2.

## Sound

Ledger chain and lock; sublations with reasons; replay of withdrawn rows; sandbox bounds on every container
path (ADR-0039); test doubles confined to tests; routing weights actually learned from `routing.jsonl`;
bfd46de's parked-row fix is correct for the case it addressed.

## Order of work

1. C1 today: revoke the token, purge, add the export filter.
2. C2: one import, one test.
3. C3: one ADR deciding which reading is true, then sign or revert.
4. H1 + H2 together: they are the two halves of "the loop does not compound and cannot measure casehold".
5. H3/H4, then H5–H7.
