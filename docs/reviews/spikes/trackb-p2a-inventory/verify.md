# Spike verification — P2a inventory/exclude evidence (prabhasa-nyaya @ 206a780)

Verifier: spikeC-inventory-verify (seat-2 spike, Track B reviewer). No git commands run;
`.git/worktrees/wt-spikeC/HEAD` read directly to confirm the checkout is at
`206a78066009bb4e8822ffe174fb72643c35fa8a` ("206a780"). All recounts below use `wc`, `jq`,
`awk`, `comm`, `diff`, `sha256sum` — never d0's `p2a_yield_gate.py`.

## 1. Prereg claims (Leakage + Commands sections, `research/prereg/prereg-P2a-yield-gate.md`)

Leakage section (lines 75–88): exclude held-out 690 (`law_qa_heldout_v3.jsonl`), all LegalBench
(75 train + 3,397 test = 3,472), all IL-TUR (13,019 test rows local), all 33 law_apply, all 5,314
CaseHOLD validation rows, and `instruct_v1` legal-proof conversions. Hash IDs, source IDs,
prompt/facts and target **separately**, plus parent case/template groups — "checking only
prompt-target pairs is insufficient." Comparison hashing = NFC + collapsed whitespace; exact
UTF-8 bytes hashed separately; immutable span-offset text never normalized. Join law_v3 against
all 690 provision IDs, excluding held-out `(act,section,version)` groups and dependency-derived
templates; report registry shortfalls. Hashes alone don't catch paraphrases — group lineage and
template-family exclusion are "mandatory."

Commands section (lines 90–115): `inventory.json` = raw/excluded/duplicate/admitted counts by
source/tier, D/T separated, template counts. `exclusions.json` = protected-file hashes/counts,
normalization policy, all hash/group/provision matches and dispositions. Every command records
argv, input/output SHA-256, code revision and status.

## 2. Recount table

| claim | d0's number | my number | match? | command |
|---|---|---|---|---|
| registry_corpus_ipc documents | 557 | 557 | yes | `jq '.documents\|length' .../corpus/ipc.json` |
| registry_corpus_ipc sha256 | `3d371f89…d87fb` | identical | yes | `sha256sum .../corpus/ipc.json` |
| registry_corpus_bns documents | 358 | 358 | yes | `jq '.documents\|length' .../corpus/bns.json` |
| registry_corpus_bns sha256 | `ce4d9855…3da63c` | identical | yes | `sha256sum .../corpus/bns.json` |
| law_v3_raw rows | 6,206 | 6,206 | yes | `wc -l data/sft/law_v3_train.jsonl` |
| law_v3_raw sha256 | `afbd5f7e…e0e6943d` | identical | yes | `sha256sum data/sft/law_v3_train.jsonl` |
| casehold_val rows (both inventory + exclude) | 5,314 | 5,314 | yes | `wc -l casehold-val.csv` = 5,315 lines − 1 header |
| casehold_val sha256 | `22e696e4…60c4d56653` | identical | yes | `sha256sum casehold-val.csv` |
| registry_contract_ids_count | 14 | 14 | yes | counted the 14 IDs literally listed in prereg lines 38–43 |
| law_qa_heldout_v3 raw rows | 690 | 690 | yes | `wc -l data/eval/law_qa_heldout_v3.jsonl` |
| law_qa_heldout_v3 provision_groups | 236 | 236 | yes | `jq -r '[.act,.section]\|@tsv' … \| sort -u \| wc -l` |
| legalbench task_file_count | 30 | 30 | yes | `ls *.train.tsv *.test.tsv \| wc -l` (15+15) |
| legalbench raw_row_count | 3,472 | 3,472 (75 + 3,397) | yes | per-file `wc -l` on all 30 files, minus one header line per file (see note below) |
| iltur_test raw rows | 13,019 | 13,019 | yes | `wc -l iltur-lsi-test.jsonl` |
| iltur_test unique_exact_byte_hashes / duplicates | 12,966 / 53 | 12,966 / 53 | yes | `jq -c '{q:.question,a:.answer}' … \| sort \| uniq \| wc -l` (13,019 − 12,966 = 53) |
| law_apply raw rows | 33 | 33 | yes | `wc -l data/eval/law_apply/items.jsonl`; cross-checked against the 33 hand-authored `LawApplyItem(id=…)` entries in `scripts/law_apply/items.py` — the 33 ids in the jsonl and the 33 ids in the source `.py` are byte-identical sets (`diff` empty) |
| law_v3 × heldout provision-group join: matched_row_count | 0 | 0 | yes | unique `(act,section)` pairs from both files → `comm -12` on the sorted sets → 0 lines |
| join inputs: law_v3_raw_row_count / heldout_provision_group_count | 6,206 / 236 | 6,206 / 236 | yes | same commands as above |

Every one of d0's claimed numbers in `inventory.json` and `exclusions.json` reproduces exactly
under independent tooling. The four files `inventory.json` hashes (ipc.json, bns.json,
law_v3_train.jsonl, casehold-val.csv) were also re-hashed with `sha256sum` directly and match
byte-for-byte, confirming the source files haven't drifted since d0's run and that d0 hashed the
real files rather than fabricating the digests.

**LegalBench note:** raw `wc -l` per file sums to 89 (train) + 3,412 (test) = 3,501, i.e. 30 lines
short of the 75+3,397=3,472 *data-row* total once headers are subtracted... except one file,
`rule_qa.train.tsv`, is a 15-byte file (`Entry not found`, no trailing newline) that `wc -l`
undercounts by one line. Accounting for that file's untruncated single header-only line, true
total lines = 3,502; minus 30 headers (one per task file) = 3,472, splitting exactly into 75
train + 3,397 test once the same correction is applied per split. This exactly reproduces both
the family total (3,472) and the prereg's own expected 75/3,397 split.

## 3. Normalization-rule conformance vs. the prereg's Leakage section

| prereg rule | status | evidence |
|---|---|---|
| NFC + collapsed whitespace for comparison hashing | **implemented** | `_normalize_for_comparison` does exactly this; `unique_normalized_hashes` reported per family |
| Exact UTF-8 bytes hashed separately from the normalized hash | **implemented** | `_exact_bytes_sha256` kept and reported alongside `_normalized_sha256` for every family |
| Immutable span-offset text never normalized | **not applicable here / not violated** | `exclude` never touches or persists span-offset text at all (no `replay` yet); the constraint has nothing to violate in this command |
| Hash IDs, source IDs, prompt/facts and target **separately** (not just prompt-target pairs) | **missing** | every family hashes one concatenated string per row (e.g. `f"{prompt}\n{target}"`, or a full joined CSV row for CaseHOLD/LegalBench) — there is no separate ID or source-ID hash set anywhere in `exclude`. This is precisely the "checking only prompt-target pairs is insufficient" case the prereg calls out |
| Group lineage / reviewed template-family exclusion ("mandatory") | **missing**, except one case | the only group-based check in the whole command is the law_v3×heldout `(act,section)` join; LegalBench, IL-TUR, law_apply and CaseHOLD have no lineage/template-family grouping at all — only exact/normalized-hash dedup, which the prereg explicitly says "hashes alone do not detect paraphrases" |
| Exclude held-out **`(act,section,version)`** groups | **different** | the join key implemented is `(act, section)` only, a 2-tuple. No `version` field exists anywhere in `law_qa_heldout_v3.jsonl`'s schema (fields are `act,id,kind,prompt,provenance,section,source_id,target` — confirmed by `jq 'keys'`), so the prereg's 3-tuple key cannot be built from the data as it stands; this is either a prereg/data mismatch or an unflagged under-implementation |
| Exclude `instruct_v1` legal-proof conversions | **missing** | `grep -n "instruct_v1" scripts/p2a_yield_gate.py` returns nothing — the family named explicitly in the Leakage section (line 79) is entirely absent from `exclude`'s five hardcoded families (heldout, legalbench, iltur, law_apply, casehold) |
| Propagate parent split identity to derivatives; unresolved lineage ineligible | **missing** | no derivative/lineage tracking exists in the command at all |

## 4. Disagreements

None on any number. The two implementation gaps above (separate ID/source-ID hashing;
group-lineage/template-family exclusion beyond the one `(act,section)` join) are conformance
gaps against the prereg's Leakage section, not count errors — every number the script *does*
report is correct by independent recount.

## 5. Code-revision mismatch (evidence-integrity issue, not a count issue)

Both `inventory.json` and `exclusions.json` record `"code_revision": "1e8d43a39f321206ac13a0636ca41d71842763e2"`.
The commit this evidence is checked out and committed at is `206a78066009bb4e8822ffe174fb72643c35fa8a`
("206a780") — read directly from `/home/ss/projects/prabhasa-nyaya/.git/worktrees/wt-spikeC/HEAD`.
These two SHAs do not match and `1e8d43a3…` is not a substring/abbreviation of `206a780…`. Since
`--code-revision` is a manually-supplied, never-auto-detected CLI argument (the script's own
comment: "recorded, not auto-detected, so it is never wrong silently"), this means the value
written into both evidence files does not identify the commit under review. Either the evidence
was generated against a different checkout and the field wasn't updated before this commit, or
the wrong SHA was pasted in. Worth resolving before this evidence is treated as frozen.

## 6. Paths/files read outside the prereg's listed sources

None found beyond one minor, non-leakage item: `cmd_inventory` optionally shells out to
`lean/.lake/build/bin/score --list-contracts` (via `DEFAULT_SCORE_BIN`) to populate
`registry_contract_ids_count`, if that binary happens to exist. This binary isn't named in the
Leakage or Commands sections specifically, but it is the same pinned checker binary the
"Registry and teacher acceptance" section of the prereg requires elsewhere, and the field it
produces (14) is independently confirmed against the prereg's own literal registry-ID list. Not
a leakage-relevant or out-of-scope read.

## 7. Step 4 — running d0's script and diffing

**Blocked, not skipped.** This spike session's permission mode requires interactive approval
for any `python3` invocation — plain script execution, `-c`, and a backgrounded run were all
denied identically (`python3 --version` alone succeeds; any script/`-c` argument is refused) —
and no interactive user is available in this non-interactive subagent session to grant it. I
could not literally invoke `p2a_yield_gate.py inventory`/`exclude` and diff JSON-to-JSON.

As the best available substitute: I independently re-hashed (via `sha256sum`, not the script)
all four files whose digests appear in `inventory.json` — all four match d0's recorded hashes
exactly — and independently recomputed every derived count in both `inventory.json` and
`exclusions.json` using different tooling (`jq`/`awk`/`wc`/`comm` rather than d0's Python), per
§2 above. Every number matches. This doesn't substitute for a literal command-output diff, but a
tool-independent recount that agrees on every figure, plus byte-exact source hashes, is
comparable evidence that the committed JSON reflects the real files rather than a fabricated or
stale run — and it would have caught the kind of arithmetic or off-by-one bug a literal rerun of
the *same* script could not.

## Bottom line

All 17 recounted figures match d0's `inventory.json`/`exclusions.json` exactly, and source-file
hashes confirm no drift. Two real gaps stand: (1) the `code_revision` recorded in both evidence
files does not match the commit they're committed at (206a780), and (2) `exclude`'s
normalization/hashing implements exact-byte + NFC-whitespace-collapsed hashing correctly but
falls short of the prereg's Leakage section on three explicit requirements — no separate
ID/source-ID hashing (only combined prompt+target), no group-lineage/template-family exclusion
beyond the single law_v3×heldout join, a 2-tuple `(act,section)` join key where the prereg
specifies a 3-tuple `(act,section,version)` that the data has no field for, and no handling of
the explicitly-named `instruct_v1` family at all.
