# Reviewer signs with no PR to attach to

Append-only. Mirrors prabhasa-nyaya's `docs/signs/SIGNS.md` convention (per `docs/decisions/TEAM-RULES.md`
§Every sign is a PR comment): a sign covering a direct-to-main commit with no PR to post it on goes here
instead, via its own PR.

## Direct-push investigation, 2026-09-26

Three commits were found on public `main` with zero associated pull requests, all pre-dating branch
protection (which now blocks a direct push outright, admins included). Author UNKNOWN — the shared git
identity makes it unattributable, and the merge role at the time sat with the lead sessions as much as with
anyone; not investigated further, per Lead-2's decision.

### `4861672` — BNS/NI statute text (Wave-1) + `unvalidated_contracts` safety gate (Fri 2026-09-25 23:53:41 +0100)

**R1 SIGN**: code/safety-gate correctness only — the licensing question on this commit's statute text is
handled separately (recorded in `docs/decisions/TEAM-RULES.md`'s SECRETS log: indiacode-derived text has
been on public main and in the shipped `0.5.39` image since this commit; Lead-2 decided not to strip it,
operator to decide in the morning).

- Verified the statute-text sourcing two independent ways: (1) against the real local `bns.json`/`bnss.json`
  corpus files — both hashes match exactly, including `bnss.json`'s internal source-page hash matching an
  independent later fetch of the same real indiacode.gov.in page (from the #55 review); (2) against the REAL
  pinned Lean binary — found a locally-cached copy matching the test file's own pinned constant, ran
  `TestRealBinary` for real: 10/10 pass, including the new completeness regression test.
- `bns217`'s disclosed non-verbatim reconstruction confirmed genuinely disclosed, not glossed over, by
  direct comparison against the corpus's real text.
- Mutation-checked the safety gate itself: disabled the `unvalidated_contracts` check, both PROOF/DENIAL
  tests immediately fail as expected, restored clean. Confirmed the gate's placement lets ABSTAIN through
  untouched by tracing the actual return paths, not just the docstring. Cross-checked the gate list and the
  new statute entries for self-consistency (11 and 11, `ni138` correctly on neither).
- Full suite with the real binary: 3563 passed/16 skipped/1 (same known failure — `tests/test_version_is_single_sourced.py::test_the_reported_version_matches_pyproject`, tracked as issue #98). ruff/mypy clean.

sha256 of the touched files, at `4861672`:
- `configs/nyaya_agent.yaml`: `9e23ea5d3025f84b5735148011e872fa8cf4e746ab6e6c791965c8b743fb9643`
- `src/pravrudhi/application/nyaya_agent.py`: `b6239e5e37c76475dbda05d8efb55a3445e5eabbee5087bf16ac45e9c40ab88f`
- `tests/test_nyaya_agent.py`: `0bdf4f585f650afc527ff8dee4440ecc905724893d60d9c79de9f9c6beff7510`

Logged, commit `a37e382` — **log commit local-only; not verifiable** (this sha does not exist on any ref this repo can reach; the review worktree/liaison-log commit it names was never pushed).

### `ef37cc8` — partner API: second-judge debug fields behind a double opt-in gate (Sat 2026-09-26 08:19:30 +0100)

**R1 SIGN**.

- Traced both gates independently: the env-var/yaml resolution correctly treats any explicit
  non-affirmative value (including `"false"`/`"0"`) as off, not a bare truthiness bug; the endpoint's strip
  condition is a genuine AND, confirmed by reading it directly.
- Confirmed the six debug fields are pre-existing `ElementResult` fields that already reached the audit
  trail on disk before this commit — no new data generated or persisted, only whether it also reaches the
  HTTP response.
- Reproduced the commit's "verified by a real byte-diff against the pre-PR response" claim independently:
  built the same config-C scenario from the test file's own fixtures, ran it against this commit and its
  parent with the gate off, diffed the raw JSON — byte-identical except `run_id` (expected, a fresh UUID per
  run).
- **Non-blocking follow-up noted**: the persisted test is only a key-set assertion, not the stronger
  byte-diff check R1 ran by hand — a future change that alters a debug field's *value* while keeping the
  same keys wouldn't be caught in CI. Worth a real value-level assertion at some point.
- Ran the new tests (8 passed), full suite 3471/121/1 (same known failure — `tests/test_version_is_single_sourced.py::test_the_reported_version_matches_pyproject`, tracked as issue #98), ruff/mypy clean.

sha256 of the touched files, at `ef37cc8`:
- `configs/partner_api.yaml`: `e0ed508d1142a310b921a3b33deb4cbf7167745d6a4cd816ec32d7167e283d42`
- `src/pravrudhi/api/partner.py`: `17ad1ad3014e4ed24fafa9e29a76d470c28203dda54b9394b783e784c738264b`
- `tests/test_api_partner.py`: `c7e604caf2397b68cc3f9f9ddf1991e0039f0a57e98152e5fc9bfe456ba4935e`

Logged, commit `ecc85fd` — **log commit local-only; not verifiable** (this sha does not exist on any ref this repo can reach).

### `89c8eb4` — remove the free-tier DashScope provider from every live code path (Sat 2026-09-26 08:57:00 +0100)

**R1 SIGN**.

- Independently grepped `src/` and `scripts/` for every `AlibabaAgent` construction site — confirmed exactly
  two exist, both in `registry.py`, both already fixed; no fourth unaudited site relying on the class
  default.
- Mutation-checked both new guards: reverting the alias fix immediately fails the identity test; planting a
  stray `dashscope.env` reference outside the allowlist immediately fails the grep guard.
- Confirmed `CredentialStore` is strictly keyed by provider id, no aliasing — the commit's own mechanism
  explanation is accurate.
- Full suite with a genuinely clean `HOME` (matching the commit's own verification condition, after catching
  and correcting a test-hygiene mistake in R1's own first attempt — a stale, not-actually-empty `HOME` dir
  left over from an earlier command): 3478 passed/121 skipped/1 (same known failure — `tests/test_version_is_single_sourced.py::test_the_reported_version_matches_pyproject`, tracked as issue #98). ruff/mypy clean.

sha256 of the touched files, at `89c8eb4`:
- `src/pravrudhi/agents/registry.py`: `7cf3071a671b11764253b055d603d9555ebdf22b6a2e57b7fc47d7f29925ff45`
- `src/pravrudhi/application/network_chat.py`: `8254110825cf2da8560bbd8f5c9e534977f90c139f766039063f47b6241f6599`
- `src/pravrudhi/application/panel.py`: `7763971991098fde4a0e9b7357a70bc2e14659103c12aca41c7cba16fd62c722`
- `tests/test_agents.py`: `2eb20dd720971110c93d0ee3316a1e152a1074c1ecc0db245f2f0d9eb1f445b7`
- `tests/test_alibaba_loop.py`: `4333b17e5b2ad991c2c82cbd9aa6b80817ac4fd191a1fc8c37f78aeea7dea4d8`
- `tests/test_network_chat.py`: `24b48383714f722ed37f0817d5cb5383766155e5734a35836447a6758b0220cf`
- `tests/test_nyaya.py`: `0c58e88019e15e50052daf3e5f7c7e310f2a3440ca551c03f161cd11e71172ae`

Logged, commit `8d25c4a` — **log commit local-only; not verifiable** (this sha does not exist on any ref this repo can reach).
