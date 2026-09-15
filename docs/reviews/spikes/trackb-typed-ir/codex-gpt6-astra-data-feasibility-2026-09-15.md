# Data feasibility: nyaya-law-v1

## Decision and inspection boundary

**The supplied data does not deliver 25,000 deterministic, grounded legal-reasoning SFT records. Current verified full-IR yield is 0 (0% of 25,000).** There are useful retrieval labels and source passages, but no supplied trusted legal-rule registry, semantic span contracts, or computed legal-rules-v1 checker reports. A teacher can propose annotations; it cannot make its own rules trusted.

This is offline data inspection, not legal advice or verification against current law. All statute examples use `stipulated_statute`, corpus version 1 built 2026-09-12. No network used. Source data was read only. Counts scan whole files without interpreting restricted records; content inspection is limited to IL-TUR lines 1–20, law_v3 lines 1–30, and the first 20 `nyaya_check` / 10 `reason_chain` matches. No semantic distribution is inferred for uninspected tails. Counts below are physical lines for JSONL, parsed data rows excluding headers for CSV/TSV, and document-array lengths for statute JSON.

### What “deterministic” means here

1. **Mechanical label/lookup conversion:** copy labels, split answers, attach whole-text spans and section IDs. Useful auxiliary supervision, but does not establish legal applicability.
2. **Authored-rule deterministic generation:** humans first supply reviewed rules, fact templates, exceptions and contracts; a program can then generate and check new examples. This is new supervision, not free conversion of the existing data.
3. **Teacher-assisted conversion:** extract events, parties, negation, evidentiary status and rule premises; reject or review proposals before deterministic checking. A teacher's fluent five-member trace is not gold.

`SPIKE.md` §2.2 is a proposed grammar, not an implemented parser. It also lacks a rule-free abstention spelling and native holding-selection / multi-label-identification task kinds. Examples below keep its line shapes, but identify the required registry entries explicitly. Null verdicts are intentional. No example is claimed to have a computed audit report.

## 1. Inventory, fields, licenses, feasibility

| Source | Count | Fields | License stated locally | Conversion assessment |
|---|---:|---|---|---|
| `casehold-val.csv` | 5,314 rows; 5,315 lines | `Unnamed: 0` source index; `0` citing prompt; `1`–`5` holdings; `6`–`10` numeric ancillary scores; `11` label | not stated | Label-to-candidate selection is deterministic once zero-based label convention is confirmed; graph semantics and legal justification need teacher/human annotation and authority retrieval. Scores are not proof premises. |
| LegalBench cache | 3,472 rows: 75 train + 3,397 test; 15 tasks, 30 TSVs | See table below | `LICENSE.md:2`: `cc-by-4.0`; lines 1248–1250 say tasks have different licenses. Individual task licenses not stated in this cache. | Diversity tasks have intermediate Boolean labels, allowing a narrow rule adapter after authoring a stipulated rule. Other tasks need teacher/human semantics, and missing task instructions/rules must be supplied. |
| `iltur-lsi-test.jsonl` | 13,019 lines | `id`, `question`, `answer`; answer uses `|`, although question asks commas | not stated; fetch metadata identifies Exploration-Lab/IL-TUR but gives no license | Section-label parsing and passage lookup deterministic; application proof is not. Teacher plus reviewed rules needed; some records should abstain. |
| `ipc.json` | 557 documents; 4,483 lines | root `version,built,source,documents,excluded_ambiguous`; document `id,act,section,title,text,part`; source page URL/hash/fetch date | not stated | Deterministic provision lookup and exact spans; no case facts, legal elements or application labels. Rules require human review before synthetic generation. |
| `bns.json` | 358 documents; 2,883 lines | Same as IPC | not stated | Same; 915 total statute documents are not 915 application proofs. |
| law_apply `items.jsonl` + `gold.jsonl` | 33 + 33 lines, joined into 33 items; 23 challenges + 10 controls | item `act,facts,id,kind,question,section`; gold `elements,id,is_control,missing_element,note` | Dataset license not stated. Manifest: team-authored, internal per-vendor gate only. Repository LICENSE says Apache-2.0; no separate data-rights statement established. | Element names/missing labels mechanically joinable; exact semantic evidence spans and trustworthy legal rules absent. Hand contracts can make these deterministic eval fixtures. Never training copies. |
| `law_v3_train.jsonl` | 6,206 lines; only first 30 inspected | `id,kind,prompt,target,source_id,act,section,provenance`; provenance `site,work,page_title,revid,sha256` | Dataset license not stated; repo Apache-2.0 does not independently establish rights for Wikisource text | Sample: 10 each `law_lookup`, `law_citation_retrieval`, `law_cite_to_title`, covering 10 Constitution articles. Deterministic lookup/citation auxiliaries; no application facts or proof supervision. Cannot extrapolate sample kind counts to 6,206. |
| `instruct_v1.jsonl` | 25,240 lines total; 20 + 10 requested matches inspected | `kind,prompt,target` | Dataset license not stated; repository LICENSE Apache-2.0 | Finite-world parsing / controlled-language role extraction partially mechanical, but incompatible with legal-rules-v1. Teacher conversion would invent a legal task. Exclude these targets from legal proof SFT. |

### LegalBench actually present

`index` is bookkeeping; successor_liability also contains an unnamed column. No task README or standalone rule passages are cached. `rule_qa.train.tsv` is header-only.

| Task | Train | Test | TSV fields |
|---|---:|---:|---|
| abercrombie | 5 | 95 | `index`, `answer`, `text` |
| diversity_1 | 6 | 300 | `index`, `text`, `answer`, `parties_are_diverse`, `aic_is_met` |
| diversity_2 | 6 | 300 | `index`, `text`, `answer`, `parties_are_diverse`, `aic_is_met` |
| diversity_3 | 6 | 300 | `index`, `text`, `answer`, `parties_are_diverse`, `aic_is_met` |
| diversity_4 | 6 | 300 | `index`, `text`, `answer`, `parties_are_diverse`, `aic_is_met` |
| diversity_5 | 6 | 300 | `index`, `text`, `answer`, `parties_are_diverse`, `aic_is_met` |
| diversity_6 | 6 | 300 | `index`, `text`, `answer`, `parties_are_diverse`, `aic_is_met` |
| hearsay | 5 | 94 | `index`, `answer`, `text`, `slice` |
| learned_hands_crime | 6 | 688 | `index`, `text`, `answer` |
| learned_hands_torts | 6 | 432 | `index`, `text`, `answer` |
| personal_jurisdiction | 4 | 50 | `index`, `answer`, `text`, `slice` |
| rule_qa | 0 | 50 | `index`, `text`, `answer`, `doctrine` |
| successor_liability | 3 | 47 | `(unnamed)`, `index`, `text`, `issue`, `answer` |
| telemarketing_sales_rule | 4 | 47 | `index`, `text`, `answer` |
| ucc_v_common_law | 6 | 94 | `index`, `contract`, `answer` |

### Quality findings that change yield

- CaseHOLD first row has label `3`; under zero-based indexing this selects CSV column `4`, not column `3`. It is a holding-completion label, not a finding that the guideline applies to Webster. The numeric columns have no supplied documentation; do not interpret them as calibrated confidence.
- IL-TUR metadata (`iltur-lsi-fetch.json`) reports 4,699 truncated cases out of 13,019, capped at 6,000 case characters. In the first 20, several questions have length 7,607; labels may require omitted text. Redactions replace distinct people and even words with `<ENTITY>`, preventing reliable coreference. Row 00001 includes label Section 438 in an anticipatory-bail narrative although the prompt calls every option IPC: act identity needs review, not blind joins.
- `nyaya_check` sample includes a prompt with no world at all, and worlds exposing just three loci. The stored full-world verdict cannot be recovered reliably from partial evidence. `reason_chain` examples all assert `valid` / “Yes” from mentioned→present, with no legal source. SPIKE §1.3 documents the generator problems; do not transplant these verdicts.
- law_apply labels are useful annotations, not authoritative element decompositions. IPC 405 has alternative branches; a contract-violation premise must not become a universal extra conjunct for every branch. BNS 69's “not rape” condition cannot be established merely from absence of force: §63 has additional circumstances. Some control facts omit age. “Missing element” does not automatically mean explicit negation in arbitrary English.

## 2. Five hand renderings

These are inspection demonstrations, excluded from training. `@F1` / `@P1` mean the full immutable string assigned that ID. Production needs exact source IDs, Unicode half-open spans, passage SHA-256, and independently computed audit metadata. The outputs below use whole-segment spans; that is legal in the proposal but coarse. Predicates such as `applies_s85` must be registered by the adapter, as in SPIKE's `applies_s416` example.

For unavailable sources, `UNSUPPORTED` below is a **proposed refusal-only registry sentinel**, never a trusted inference rule. The parser must permit its empty passage list only when `abstain=true` and verdict=null. Without that small explicit extension, the §2 schema cannot faithfully encode missing-source refusal. No invented statute citation fills the gap.

### A. CaseHOLD holding selection — row index 42508

The reference selection is zero-based candidate 3 (CSV column `4`). Below is the exact prompt and all five options. Holding selection itself is outside §2.1's native task taxonomy: this rendering refuses to certify a legal inference and preserves the selection in the answer as a dataset label. It must not be scored as proved applicability.

```text
SCOPE act=US_case_law mode=stipulated_statute
Q Select the holding that fills <HOLDING>.
F1 "was acting to obtain a benefit on behalf of a charitable ... organization.” U.S.S.G. § 2B1.1 cmt. 8(B). As the district court saw it and as the government sees it, Webster deserves the enhancement. He pretended to “act[ ] on behalf of a charitable ... organization,” U.S.S.G. § 2Bl.l(b)(9)(A), when he solicited personal information from the victims on behalf of fake charities. As Webster sees it, the enhancement does not apply. In his view, the commentary limits the application of the charity enhancement, and he was not acting to obtain a benefit on behalf of a charitable organization (as the commentary seems to require). As a general matter, the text of a guideline trumps commentary about it. See Stinson v. United States, 508 U.S. 36, 38, 113 S.Ct. 1913, 123 L.Ed.2d 598 (1993) (<HOLDING>). But we need not resolve whether the"
F2 "recognizing the guidelines commentary is authoritative"
F3 "holding that a sentencing guideline prevails over its commentary if the two are inconsistent"
F4 "holding that sentencing guidelines commentary must be given controlling weight unless it violates the constitution or a federal statute or is plainly inconsistent with the guidelines itself"
F5 "holding that commentary is not authoritative if it is inconsistent with or a plainly erroneous reading of the guideline it interprets or explains"
F6 "holding that guidelines commentary is generally authoritative"
```

```text
NODE e KRIYA act @F1
NODE h SAMANYA holding @F5
NODE support GUNA element @F1
ELEMENT support e unknown @F1
PRATIJNA e establishes_h true
HETU support
UDAHARANA UNSUPPORTED example=null
UPANAYA x=e premises=[]
NIGAMANA not_proved
HETVABHASA legal-rules-v1 null satpratipaksa=null badhita=null
ABSTAIN true missing_source
ANSWER "Dataset selection: candidate 3 (zero-based), commentary is not authoritative if inconsistent with or a plainly erroneous reading of the guideline. The supplied data does not certify a legal application proof."
```

No ROLE is invented: a citation act is not the defendant's underlying conduct. No CITE line: an opinion citation is not a statute section.

### B. LegalBench diversity_1 — train index 0

The row provides answer `No`, `parties_are_diverse=False`, `aic_is_met=False`. The rule passage below is **hand-authored stipulated benchmark logic**, not a cached statute or a statement of all federal jurisdiction law. Citizenship and domicile should not be inferred from “from” outside this benchmark contract. This is the promising narrow no-LLM adapter after human rule/contract review.

```text
SCOPE act=LB_DIVERSITY mode=stipulated_statute
Q Is diversity jurisdiction available under the stipulated benchmark rule?
F1 "Evelyn is from Hawaii. Charlotte is from Hawaii. Evelyn sues Charlotte for negligence for $20,000."
P1 act=LB_DIVERSITY section=diversity_1 version=authored-v1
"For this benchmark, diversity jurisdiction requires both parties_are_diverse and aic_is_met. A false value for either defeats the benchmark conclusion."
```

```text
NODE a DRAVYA party @F1
NODE b DRAVYA party @F1
NODE e KRIYA act @F1
NODE rule SAMANYA provision @P1
NODE diverse GUNA element @P1
NODE amount GUNA element @P1
ROLE e KARTR a
ROLE e KARMAN b
ELEMENT diverse e negated @F1
ELEMENT amount e negated @F1
CLAIM c1 absent diverse e @F1
CLAIM c2 absent rule e BY LB_DIVERSITY_NEG_V1 c1
PRATIJNA e applies_rule false
HETU diverse
UDAHARANA LB_DIVERSITY_NEG_V1 @P1 example=null
UPANAYA x=e premises=diverse
NIGAMANA proved c2
HETVABHASA legal-rules-v1 valid satpratipaksa=null badhita=null
CITE P1 LB_DIVERSITY diversity_1
ABSTAIN false
ANSWER "No under the stipulated benchmark rule: the parties are not diverse."
```

`valid` is the expected result after registering/checking this authored contract, not an executed checker result. The benchmark citation must never be represented as a US statutory citation.

### C. IL-TUR statute identification — iltur-lsi-test-00007

Exact case segment from the original question; the original candidate list includes Section 379. Gold answer is `Section 379`. The task requests applicability, while the excerpt reports allegations and procedure. Missing ownership, consent and mental-state evidence prevents certifying theft from this label alone.

```text
SCOPE act=IPC mode=stipulated_statute
Q Identify applicable IPC sections from the original candidate list.
F1 "<ENTITY>, J. <ENTITY>, <ENTITY>, <ENTITY> and <ENTITY> were prosecuted on the complaint of <ENTITY> under <SECTION>, <ACT>, on the allegation that they had entered a grove situated in Ajitpur on 29-4-1949 and cut mangoes worth about Rs. 35/- from the grove. The case was tried and disposed of by Shri <ENTITY>, Judicial Magistrate, Kaisarganj at Bahraich."
P1 act=IPC section=379 version=corpus-v1
"Whoever commits theft shall be punished with imprisonment of either description for a term which may extend to three years, or with fine, or with both. STATE AMENDMENT Gujarat.— In the Indian Penal Code, 1860 (XLV of 1860), after section 379, the following sections shall be inserted, namely:—"
```

```text
NODE e KRIYA act @F1
NODE s379 SAMANYA provision @P1
NODE theft GUNA element @P1
ELEMENT theft e unknown @F1
PRATIJNA e applies_s379 true
HETU theft
UDAHARANA IPC_379_POS_V1 @P1 example=null
UPANAYA x=e premises=[]
NIGAMANA not_proved
HETVABHASA legal-rules-v1 null satpratipaksa=null badhita=null
CITE P1 IPC 379
ABSTAIN true missing_fact
ANSWER "Dataset identification: Section 379. I cannot certify its application from this excerpt: the elements of theft are not established."
```

`IPC_379_POS_V1` would map established theft to §379 and require the §378 definition upstream; it is not supplied by the dataset. Repeated `<ENTITY>` tokens do not license distinct named party nodes. The answer records the label separately from the refused proof.

### D. law_apply — law_apply:BNS:Section 85:v2

Exact held-out facts. Explicit lack of a family/marital relationship defeats the relationship prerequisite without resolving cruelty. Proposed rule `BNS_85_NEG_REL_V1` binds to §85 as listed below.

```text
SCOPE act=BNS mode=stipulated_statute
Q Given these facts, does Bharatiya Nyaya Sanhita Section 85 apply? If not, which element is missing?
F1 "A and B are next-door neighbours with no family or marital relationship to each other. Over the course of a year, A repeatedly harasses B, damages B's property, and threatens B, causing B lasting distress."
P1 act=BNS section=85 version=corpus-v1
"Whoever, being the husband or the relative of the husband of a woman, subjects such woman to cruelty shall be punished with imprisonment for a term which may extend to three years and shall also be liable to fine."
```

```text
NODE a DRAVYA party @F1
NODE b DRAVYA party @F1
NODE e KRIYA act @F1
NODE s85 SAMANYA provision @P1
NODE relationship GUNA element @P1
ROLE e KARTR a
ROLE e KARMAN b
ELEMENT relationship e negated @F1
CLAIM c1 absent relationship e @F1
CLAIM c2 absent s85 e BY BNS_85_NEG_REL_V1 c1
PRATIJNA e applies_s85 false
HETU relationship
UDAHARANA BNS_85_NEG_REL_V1 @P1 example=null
UPANAYA x=e premises=relationship
NIGAMANA proved c2
HETVABHASA legal-rules-v1 valid satpratipaksa=null badhita=null
CITE P1 BNS 85
ABSTAIN false
ANSWER "Section 85 does not apply on these stipulated facts: A is neither B's husband nor a relative of her husband."
```

Again this is a hand-authored expected trace awaiting a registered checker, not an already-certified record.

### E. New missing-fact abstention (not copied from evaluation)

```text
SCOPE act=BNS mode=stipulated_statute
Q Does BNS section 85 apply to the reported harassment?
F1 "M repeatedly harasses N. The account gives no information about their family or marital relationship."
P1 act=BNS section=85 version=corpus-v1
"Whoever, being the husband or the relative of the husband of a woman, subjects such woman to cruelty shall be punished with imprisonment for a term which may extend to three years and shall also be liable to fine."
```

```text
NODE m DRAVYA party @F1
NODE n DRAVYA party @F1
NODE e KRIYA act @F1
NODE s85 SAMANYA provision @P1
NODE relationship GUNA element @P1
ROLE e KARTR m
ROLE e KARMAN n
ELEMENT relationship e unknown @F1
PRATIJNA e applies_s85 true
HETU relationship
UDAHARANA BNS_85_POS_V1 @P1 example=null
UPANAYA x=e premises=[]
NIGAMANA not_proved
HETVABHASA legal-rules-v1 null satpratipaksa=null badhita=null
CITE P1 BNS 85
ABSTAIN true missing_fact
ANSWER "I cannot determine whether section 85 applies: the required relationship is unknown."
```

No absence claim follows from silence. Cruelty under §86 is also unestablished; one blocking unknown suffices for refusal. A full positive trace would cite §86 and establish all premises.

## 3. Honest SFT yield estimates

Counts are **one record per source row/document**, without option permutations, paraphrase inflation or teacher-generated variants. Teacher numbers are candidate ceilings, not measured acceptance rates. No teacher was run.

| Source | Mechanical auxiliary ceiling | Full legal IR with no new semantic annotation | Teacher/human-assisted candidate ceiling before exclusions | Training-eligible position |
|---|---:|---:|---:|---|
| CaseHOLD validation | 5,314 selections | 0 | 5,314, only after authority retrieval; some outside statute schema | 0: reserve validation; no case-group contamination |
| LegalBench train | 75 labels; 36 diversity rows have premise labels | 0 as supplied; up to 36 with reviewed diversity adapter | up to 75 total (includes the 36), acceptance unknown | up to 75 after license, rule and dedup checks |
| LegalBench test | 3,397 labels | 0 train | 3,397 eval-only candidates | 0 |
| IL-TUR test | 13,019 section-label lists | 0 train | up to 13,019 eval candidates, not all groundable; only 20 inspected | 0 |
| IPC | 557 lookups | 0 | 557 seed provisions for rule authoring, not existing case records | 0 proved applications without new facts/rules |
| BNS | 358 lookups | 0 | 358 seed provisions, same limitation | 0 proved applications without new facts/rules |
| law_apply | 33 joined annotations | 0 train | 33 hand-reviewed eval traces; 20 concern IPC/BNS directly | 0 |
| law_v3_train | 30 observed mechanical auxiliaries; absolute file ceiling 6,206 unverified | 0 application records | up to 6,206 rewrites, but new case facts would be synthesis, not conversion | unknown until split-group audit; 0 confirmed full IR |
| instruct_v1 | 30 inspected nonlegal examples, total 25,240 mixed rows | 0 | 0 defensible legal conversions; new legal tasks would replace originals | 0 legal proof records |

**Current deterministic full-IR coverage: 0 / 25,000 = 0%.** Conditional on a human-authored diversity rule/contract adapter, at most 36 / 25,000 = **0.144%**, leaving 24,964. This is a planning ceiling, not 36 tested outputs. If all 75 LegalBench train rows were eventually accepted via annotation, that is only 0.30% and overlaps the 36.

For a deliberately looser *auxiliary-only* count, 915 statute lookups + 6,206 unverified law_v3 rows + 75 LegalBench train labels = 7,196 (28.784%). This is a gross upper bound before duplication/leakage/license filtering, **not** 28.784% coverage of the specified proof target. The 30 inspected law_v3 rows cover only ten source articles, demonstrating why row counts and independent coverage differ.

Generating 25k records deterministically becomes possible only after authoring rule families and controlled fact generators. Neither number of templates nor checker acceptance is measured here, so there is no honest numerical claim for that future yield. Missing-source abstentions can mechanically fill arbitrary quotas but would train refusal rather than legal reasoning and are not counted as target coverage.

## 4. Required rule registry and exact source bindings

**No trusted rule IDs were found in the supplied records.** The following are proposed IDs that would need independent review, typed premises/conclusions, exception handling and registration. “NEG” means an explicit false necessary premise defeats this section's application; it never means failure to prove a premise. “POS” requires all relevant branch premises and resolved defeaters.

Direct IPC/BNS law_apply scope is six sections and 20 records: IPC 405/416/182 (3 each), BNS 85 (3), 69 (4), 47 (4). Other 13 records need Evidence 113A, Contract 142, Constitution 23 and BNSS 46 sources outside the two supplied corpora. IPC 498A is an additional dependency of Evidence 113A.

Offsets below are Unicode code-point `[start,end)` into the **decoded document `text`**, not file bytes or title+text. SHA-256 is over that exact UTF-8 text, independently computed here; corpus page hashes are different objects. Preserve extraction artifacts and bind versions/hashes before cleaning. Full source text remains necessary even when a shorter operative span is displayed.

### IPC §405

- IDs: `IPC_405_POS_V1; IPC_405_NEG_ENTRUST_V1; IPC_405_NEG_DISHONEST_CONDUCT_V1`
- Meaning: Branch-aware entrustment plus dishonest conduct; retain wilfully-suffers branch and statutory deemed entrustment explanations. Do not require contract violation universally.
- Source: `ipc.json`, document `Indian Penal Code/Section 405`, text span `[0,492)`.
- Text SHA-256: `cb6a89bd17b0aeda974ba5e7149d7518481744082bb47426e63cb5b685ea2223`

> Whoever, being in any manner entrusted with property, or with any dominion over property, dishonestly misappropriates or converts to his own use that property, or dishonestly uses or disposes of that property in violation of any direction of law prescribing the mode in which such trust is to be discharged, or of any legal contract, express or implied, which he has made touching the discharge of such trust, or wilfully suffers any other person so to do, commits “criminal breach of trust”.

### IPC §416

- IDs: `IPC_416_POS_V1; IPC_416_NEG_CHEAT_V1; IPC_416_NEG_PERSONATION_V1`
- Meaning: Cheating AND at least one personation alternative; no-personation negation must defeat every alternative; real or imaginary identity.
- Source: `ipc.json`, document `Indian Penal Code/Section 416`, text span `[0,355)`.
- Text SHA-256: `48842b98ea3d233fb80dcc0117363acb2ef7f0db0a7d920341c7f3074e43209d`

> A person is said to “cheat by personation” if he cheats by pretending to be some other person, or by knowingly substituting one person for or another, or representing that he or any other person is a person other than he or such other person really is. Explanation.—The offence is committed whether the individual personated is a real or imaginary person.

### IPC §182

- IDs: `IPC_182_POS_V1; IPC_182_NEG_PUBLIC_SERVANT_V1; IPC_182_NEG_KNOW_FALSE_V1; IPC_182_NEG_INTENT_V1`
- Meaning: Information to public servant AND knows/believes false AND requisite intended/likely consequence, preserving (a)/(b) alternatives.
- Source: `ipc.json`, document `Indian Penal Code/Section 182`, text span `[0,457)`.
- Text SHA-256: `98dd9ad769a7f4e0bbab4c04afc3d5d4d4de44524cf9346aea44e3f00e7f2ea0`

> Whoever gives to any public servant any information which he knows or believes to be false, intending thereby to cause, or knowing it to be likely that he will thereby cause, such public servant— (a) to do or omit anything which such public servant ought not to do or omit if the true state of facts respecting which such information is given were known by him, or (b) to use the lawful power of such public servant to the injury or annoyance of any person,

### BNS §85

- IDs: `BNS_85_POS_V1; BNS_85_NEG_REL_V1; BNS_85_NEG_CRUELTY_V1`
- Meaning: Relationship AND cruelty under §86; relation false alone suffices for negative.
- Source: `bns.json`, document `Bharatiya Nyaya Sanhita/Section 85`, text span `[0,100)`.
- Text SHA-256: `195d092d7ed6d3fd622567b9d6eb9d157362a9b00179f53f0da0a0c2f6a2076f`

> Whoever, being the husband or the relative of the husband of a woman, subjects such woman to cruelty

### BNS §69

- IDs: `BNS_69_POS_V1; BNS_69_NEG_DECEIT_V1; BNS_69_NEG_INTERCOURSE_V1; BNS_69_NEG_NOT_RAPE_V1`
- Meaning: Deceitful means OR promise with no intention; intercourse caused thereby; not rape. Preserve explanation and resolve §63, not just force/threat.
- Source: `bns.json`, document `Bharatiya Nyaya Sanhita/Section 69`, text span `[0,486)`.
- Text SHA-256: `dda8af7abdfdb33ba827b418b20ff2cf4bf0295bc78ce55c0888317ae94db7f8`

> Whoever, by deceitful means or by making promise to marry to a woman without any intention of fulfilling the same, has sexual intercourse with her, such sexual intercourse not amounting to the offence of rape, shall be punished with imprisonment of either description for a term which may extend to ten years and shall also be liable to fine. Explanation.— “deceitful means” shall include inducement for, or false promise of employment or promotion, or marrying by suppressing identity.

### BNS §47

- IDs: `BNS_47_POS_V1; BNS_47_NEG_IN_INDIA_V1; BNS_47_NEG_OUTSIDE_V1; BNS_47_NEG_OFFENCE_V1`
- Meaning: Abetment in India; act outside India; counterfactual offence in India. Requires abetment definition and act-specific offence rule; do not use foreign legality alone.
- Source: `bns.json`, document `Bharatiya Nyaya Sanhita/Section 47`, text span `[0,189)`.
- Text SHA-256: `692799d114c1c3d96148b13c7a5957ad520845de488d43bdda4888ccdb889f97`

> A person abets an offence within the meaning of this Sanhita who, in India, abets the commission of any act without and beyond India which would constitute an offence if committed in India.

### Supporting rules (must be resolved before positive certification)

The full decoded text spans below bind definitions; a rule compiler must preserve their internal alternatives/exceptions rather than treating each entire section as a Boolean atom. These sources are available locally; their legal formalizations are not.

| Proposed rule ID | Source document | Text span | Binding / purpose |
|---|---|---|---|
| `IPC_415_CHEAT_V1` | `ipc.json`: Section 415 | `[0,2772)` | Cheating alternatives and dishonest concealment; dependency of 416 |
| `IPC_498A_CRUELTY_V1` | `ipc.json`: Section 498A | `[0,769)` | Cruelty definition referenced by Evidence 113A; not itself a proof of suicide/temporal premises |
| `BNS_86_CRUELTY_V1` | `bns.json`: Section 86 | `[0,513)` | Alternative cruelty definitions for 85 |
| `BNS_45_ABET_V1` | `bns.json`: Section 45 | `[0,1168)` | Instigation / conspiracy with act or omission / intentional aid for 47 |
| `BNS_63_RAPE_V1` | `bns.json`: Section 63 | `[0,2334)` | Circumstances, consent, age and exceptions needed for 69 |
| `BNS_101_MURDER_V1` | `bns.json`: Section 101 | `[0,6421)` | Murder definition and exceptions for the 47 murder control |
| `BNS_82_BIGAMY_V1` | `bns.json`: Section 82 | `[0,1317)` | Void-marriage prerequisite and exceptions for 47:v4; foreign legality alone does not settle counterfactual offence |
| `IPC_378_THEFT_V1` | `ipc.json`: Section 378 | `[0,5415)` | Theft definition for IL-TUR demonstration |
| `IPC_379_POS_V1` | `ipc.json`: Section 379 | `[0,293)` | Punishment of established theft; not standalone fact extraction |

Supporting bindings are exact full-section spans, not paraphrases. For deeper criminal-law formalization, general definitions (e.g. dishonest intention, abettor), exceptions and act-specific dependencies must also be reviewed; the listed six direct rule families are not a complete criminal code. No rule here licenses copying the law_apply gold note into an axiom.

## 5. Leakage and required exclusions

| Source | Overlap evidence / uncertainty | Required treatment |
|---|---|---|
| law_apply items + gold | Exact named evaluation set, 33/33; `scripts/law_apply/items.py:13` says section text is from `law_qa_heldout_v3.jsonl` | Exclude all 33, their paraphrases, controls and negation siblings from SFT. The examples in this report are eval analysis only. |
| LegalBench `*.test.tsv` | Exact benchmark test partitions, 3,397 rows | Exclude every row and all teacher traces/augmentations derived from it. Train examples require duplicate/template-family checks against test. |
| IL-TUR test | Exact test split, 13,019 rows, fetch manifest confirms | Exclude all, including labels, cropped versions and generated explanations. Redacted/truncated variants retain parent group identity. |
| CaseHOLD validation | 5,314 validation examples. Exact overlap with the four named eval sets not established; it is independently a held-out partition | Conservatively reserve the entire validation file. Split by underlying case/citation group, not merely row or option order. No CaseHOLD train file was inspected or counted. |
| IPC/BNS corpora | Contain all six direct law_apply sections and IPC 498A dependency; source text overlaps held-out legal subject matter. Exact overlap count with all 690 not measured | Statutes may remain retrieval context for evaluation if the protocol permits it. For source-disjoint SFT, exclude all held-out `(act,section,version)` groups and dependency-derived case templates; never treat shared retrieval text alone as a leaked answer. |
| law_v3_train | 6,206 named training rows; sample contains Constitution Article 1, Article 2, Article 3, Article 4, Article 6, Article 7, Article 8, Article 9, Article 10, Article 11. “train” filename does not prove disjointness at provision level | Require joins against the 690 IDs/source IDs and normalized prompt/target texts, plus section-group split checks. Full-file semantic scan forbidden by this spike's 30-line limit, so exact overlap is unknown; do not claim it passed. |
| instruct_v1 | No legal overlap demonstrated in the 30 sampled matches; mixed-file tail uninspected | Exclude from legal proof SFT for semantic mismatch. Any auxiliary use needs independent prompt/target and group dedup audit. |

The held-out 690 is identified in local measurement scripts as `data/eval/law_qa_heldout_v3.jsonl`. Its content was not among the supplied inspection inputs; this report does not claim an exact 690-way intersection. A proper release audit must compare record IDs, normalized text hashes, source provision IDs, and parent case/template groups **before** teacher generation. Keep derivatives in their parent's split. Do not deduplicate only `(prompt,target)`, which leaves contradictory targets or paraphrased evaluation copies.

The broad overlap already established is enough to reject a claim that the provided raw row counts supply 25k clean training proofs. The remaining uncertainty is the number of admissible provision groups and annotated train examples, not whether test data can be relabeled into training.
