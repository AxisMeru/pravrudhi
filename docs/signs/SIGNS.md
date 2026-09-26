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
- Full suite with the real binary: 3563 passed/16 skipped/1 (same known failure). ruff/mypy clean.

Logged, commit `a37e382`.

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
- Ran the new tests (8 passed), full suite 3471/121/1 (same known failure), ruff/mypy clean.

Logged, commit `ecc85fd`.

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
  left over from an earlier command): 3478 passed/121 skipped/1 (same known failure). ruff/mypy clean.

Logged, commit `8d25c4a`.
