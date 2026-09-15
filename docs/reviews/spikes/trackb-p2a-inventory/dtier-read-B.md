# D-tier full merge-gate read — batch B (seat-2 spike)

Source: `dtier-flat-B.json`, 240 records, 7 contract_ids × 10 scenarios each.

**Counts**
- Scenarios per contract_id: 10 (×7 = 70 scenarios total)
- Rephrasing clusters/pairs flagged: 6 clusters (2 four-way, one six-way, one three-way, one two-way, one three-pair-set) spanning 5 of 7 contract_ids
- Mis-filed scenarios flagged: 2 (weak-fit concerns, not clean errors)
- Leaking omission slices: 31 of 150 omission slices read (across 5 of 7 contract_ids)

---

## ipc405_misappropriation (10 scenarios)

### A. Distinctness
| scenario_id | F_narrative |
|---|---|
| s_405m_warehouse_ravi | Ravi, warehouse keeper, 200 sacks rice from Meena under storage agreement; sold to 3rd party, kept proceeds |
| s_405m_jeweller_kunal | Kunal, jeweller, gold bangles from Priya for repair; pawned, kept pawn money |
| s_405m_maintfund_harish | Harish, treasurer, housing-society funds; spent portion on personal debts |
| s_405m_sewingmachine_neha | Rakesh, tailor, antique sewing machine from Neha to service; sold to collector, kept price |
| s_405m_textile_logistics | Supriya, logistics manager, fabric shipment; diverted to cousin's workshop, sold |
| s_405m_vaccine_pharmacy | Dr. Vikram, hospital pharmacist, vaccines; sold to private clinic for cash |
| s_405m_construction_steel | Mohan, site supervisor, steel rods; extracted, sold to scrap dealer |
| s_405m_sponsorship_events | Priya, event organizer, sponsorship funds; spent half on sister's wedding |
| s_405m_library_manuscripts | Anil, librarian, donated manuscripts; sold 3 to dealer, kept proceeds |
| s_405m_jewelry_workshop | Nasrin, apprentice, raw gold/gemstones; incorporated into own pieces, sold |

**Verdict:** All 10 materially distinct — different parties, objects, settings, and mode of conversion (sale to third party / pawn / spending / diversion to relative / sale to clinic / scrap sale / wedding spend / dealer sale / self-incorporation). No rephrasing pairs. All correctly fit the misappropriation limb (entrustment + dishonest conversion to own use); none look mis-filed.

### B. Entailment leak (omit_0 = el0 dropped, omit_1 = el1 dropped; 20 slices read)
None leak. In every scenario the retained conversion fact (e.g. "sold the rice to a third party and kept the proceeds") does not presuppose the specific entrustment relationship, and the retained entrustment fact does not presuppose the conversion occurred. **0/20 omission slices leak.**

---

## ipc405_use_or_disposal (10 scenarios)

### A. Distinctness
| scenario_id | F_narrative |
|---|---|
| s_405u_cold_storage_asha | Farida deposits potatoes with Asha's cold-storage under contract requiring refrigeration; Asha moves them to unrefrigerated room to free space |
| s_405u_artgallery_leela | Leela deposits painting with Devraj's gallery under contract requiring climate control; Devraj moves it to ordinary room to make space |
| s_405u_grainsilo_baldev | Cooperative deposits wheat with Baldev's silo under contract requiring segregation; Baldev mixes it with another farmer's batch to save space |
| s_405u_vault_pooja | Pooja deposits gold with Yusuf's vault under contract requiring individual sealed box; Yusuf moves it to a shared box to consolidate space |
| s_405u_orchid_conservation | Kamal, conservationist, entrusted orchids; uses for commercial propagation, deed prohibits commercial use |
| s_405u_charity_warehouse | Bhavna, coordinator, donated furniture/warehouse; lets political campaign stage events for cash, agreement prohibits it |
| s_405u_film_equipment | Ravi, production assistant, camera equipment; sub-rents to another filmmaker, contract prohibits secondary rental |
| s_405u_pesticide_dilution | Harinder, farm manager, pesticides; dilutes and sells to neighbors, contract mandates exclusive on-field use |
| s_405u_research_specimen | Dr. Ananya, lab technician, bacterial cultures; transfers to external collaborator, protocol prohibits it |
| s_405u_wedding_textiles | Sanjana, wardrobe custodian, bride's saris; loans to a friend, agreement prohibits external loans |

**Verdict — flag:** `s_405u_cold_storage_asha`, `s_405u_artgallery_leela`, `s_405u_grainsilo_baldev`, `s_405u_vault_pooja` are rephrasings of one fact pattern: depositor hands an item to a named custodian under a written contract with an explicit storage/segregation condition; custodian moves/mixes the item into the wrong location **"to free up/make/save/consolidate space"**; contract expressly required the violated condition. Only the object (potatoes/painting/wheat/gold) and profession (cold-store keeper/gallery owner/silo operator/vault keeper) change — same parties-role structure, same conduct (relocate-for-space), same setting (storage facility), near-identical causal motive phrasing across all four. The remaining 6 scenarios each add a distinguishing conduct type (commercial propagation, political-event rental, equipment sub-rental, dilution-and-sale, unauthorized data transfer, loan to a friend) and are materially distinct from each other and from the "move for space" cluster.

All 10 correctly fit the use-or-disposal limb (contract-violating use/disposal, not full conversion); no mis-filings.

### B. Entailment leak (omit_1 = el1 dropped, omit_2 = el2 dropped; 20 slices read)

**omit_1 (conduct dropped, el0+el2 retained): clean, 0/10 leak.** Neither the entrustment fact nor the bare contract term implies the violating act occurred.

**omit_2 (contract term dropped, el0+el1 retained): all 10 leak.**

| scenario_id | slice | dropped | leaking fact | quoted phrase | why |
|---|---|---|---|---|---|
| s_405u_cold_storage_asha | omit_2 | el2 (refrigeration required) | F_el1 | "moved the potatoes to a different, **unrefrigerated** storage room" | "unrefrigerated" only makes sense as a violation if refrigeration was the required condition — restates el2 |
| s_405u_artgallery_leela | omit_2 | el2 (climate-control required) | F_el1 | "moved the painting to an **ordinary** storage room" | "ordinary" (vs. the required climate-controlled room) implies the dropped condition |
| s_405u_grainsilo_baldev | omit_2 | el2 (segregation required) | F_el1 | "**mixed** the deposited wheat with another farmer's batch" | mixing is only wrongful if per-farmer segregation was required — presupposes el2 |
| s_405u_vault_pooja | omit_2 | el2 (individual sealed box required) | F_el1 | "moved the gold into a **shared box** with other clients' items" | "shared" implies the box was required to be individual/sealed — restates el2 |
| s_405u_orchid_conservation | omit_2 | el2 (commercial use prohibited) | F_el1 | "engaging in **commercial propagation** and cultivation to support his private commercial nursery" | "commercial propagation" is lifted almost verbatim from el2's prohibited-use wording |
| s_405u_charity_warehouse | omit_2 | el2 (political/for-profit activity prohibited) | F_el1 | "permitting a **political campaign** to stage events ... in exchange for cash payment" | names the exact prohibited category ("political"/"for-profit") from el2 |
| s_405u_film_equipment | omit_2 | el2 (secondary rental to third parties prohibited) | F_el1 | "renting it out to a **different filmmaker**" | describes exactly the prohibited act (secondary rental to a third party) named in el2 |
| s_405u_pesticide_dilution | omit_2 | el2 (exclusive use on contracted fields) | F_el1 | "sale to neighboring farmers **outside the intended crop application**" | "outside the intended crop application" paraphrases el2's "used exclusively on the company's contracted fields" |
| s_405u_research_specimen | omit_2 | el2 (internal custody / no external transfer) | F_el1 | "transferring portions ... **outside the company's oversight and control**" | paraphrases el2's "remain under strict internal custody ... prohibited external transfer" |
| s_405u_wedding_textiles | omit_2 | el2 (must stay in custodian's custody / no external loans) | F_el1 | "loaning them to a friend ... **outside the bride's oversight and authorization**" | paraphrases el2's "must remain in Sanjana's direct custody ... prohibited external loans" |

**10/10 omit_2 slices leak** (varying strength: cold_storage/vault/grainsilo are the strongest, near-lexical; orchid/pesticide/research/wedding/charity/film echo el2's specific prohibited-conduct language).

---

## ipc405_wilfully_suffers (10 scenarios)

### A. Distinctness
| scenario_id | F_narrative |
|---|---|
| s_405w_locker_vikram | Vikram, locker manager, dominion over Sunita's locker keys; sees colleague Deepak try unauthorized opening, looks away |
| s_405w_safedeposit_irfan | Irfan, safe-deposit manager, dominion over Fatima's box; sees nephew take an item, lets him proceed |
| s_405w_cashcounter_alka | Alka, till manager, dominion over day's cash; sees co-worker pocket cash, pretends not to notice |
| s_405w_toolshed_prakash | Prakash, toolshed manager, dominion over rented equipment; sees subcontractor remove tools unsigned, lets it happen |
| s_405s_warehouse_access | Arjun, warehouse manager, dominion over facility/goods; disables surveillance + grants after-hours access for friend Devendra to remove electronics |
| s_405s_construction_nephew | Satish, site engineer, dominion over materials; overlooks inventory discrepancies, fails to report nephew Ashok's copper theft |
| s_405s_pet_grooming | Meera, facility owner, dominion over client belongings; fails to inspect returns, ignores gaps re: employee Lakshmi's jewelry theft |
| s_405s_laboratory_data | Prof. Rajesh, dept head, dominion over data drives; grants unrestricted access, no audit, re: student Nisha's data theft |
| s_405s_retail_cashier | Vikram, store manager, dominion over till; neglects reconciliation, exempts family from audit, re: brother-in-law Rohit's cash theft |
| s_405s_digital_archive | Neha, digital asset manager, dominion over film archive; provides credentials, doesn't flag, re: freelancer Sameer's film theft |

**Verdict — flag:** `s_405w_locker_vikram`, `s_405w_safedeposit_irfan`, `s_405w_cashcounter_alka`, `s_405w_toolshed_prakash` are rephrasings of one fact pattern: custodian with dominion over X, **seeing** a named third party commit an unauthorized act against X **in the moment**, and passively **"looked away"/"let him proceed"/"pretended not to notice"/"let it happen."** No distinguishing facilitating conduct — same bare-bones passive-witness template, only the prop (locker/box/till/tools) and observed relationship (colleague/nephew/co-worker/subcontractor) swapped. By contrast the six `s_405s_*` scenarios each add a genuinely distinct affirmative facilitating mechanism (disabling surveillance, ignoring inventory discrepancies, skipping inspections, granting unrestricted access, exempting from audits, handing out credentials) and are materially distinct from each other.

All 10 correctly fit the wilfully-suffers limb (defendant never personally converts; a third party takes the property while the custodian deliberately enables/ignores it). No mis-filings.

### B. Entailment leak (omit_0 = el0 dropped, omit_1 = el1 dropped; 20 slices read)

**omit_1 (facilitation dropped, el0 retained): clean, 0/10 leak** — the bare dominion fact never implies the specific facilitating act occurred.

**omit_0 (dominion dropped, el1 retained): 6/10 leak**, specifically the `s_405s_*` batch, because their el1 always names an affirmative access/oversight-control action that only a person holding dominion could perform:

| scenario_id | slice | dropped | leaking fact | quoted phrase | why |
|---|---|---|---|---|---|
| s_405s_warehouse_access | omit_0 | el0 (dominion over facility/goods) | F_el1 | "**disabling surveillance systems and granting unauthorized after-hours access**" | only someone with control over the facility's security/access systems (i.e. holding dominion) could disable surveillance or grant access — restates el0 |
| s_405s_construction_nephew | omit_0 | el0 (dominion over materials) | F_el1 | "**overlooking inventory discrepancies**" | presupposes an inventory-oversight role over the materials, i.e. dominion |
| s_405s_pet_grooming | omit_0 | el0 (dominion over client belongings) | F_el1 | "**failing to inspect returned items**" | presupposes a custodial inspection duty over the belongings |
| s_405s_laboratory_data | omit_0 | el0 (dominion over data drives) | F_el1 | "**granting unrestricted access** and ... failing to audit" | only the person with controlling authority over the drives can "grant access" |
| s_405s_retail_cashier | omit_0 | el0 (dominion over till/registers) | F_el1 | "**exempting his family from audit procedures**" | presupposes authority/responsibility over the till's audit process |
| s_405s_digital_archive | omit_0 | el0 (dominion over archive access) | F_el1 | "**providing unauthorized access credentials**" | only the person who controls access credentials (i.e. holds dominion) can hand them out |

The four passive-witness scenarios (locker/safedeposit/cashcounter/toolshed) do **not** leak on omit_0 — their el1 ("seeing X, looked away") carries no access-granting act, so it does not presuppose formal dominion.

---

## ipc415_property (10 scenarios)

### A. Distinctness
| scenario_id | F_narrative |
|---|---|
| s_415p_showroom_tara | Tara sells watch, false "genuine imported brand" claim, Nikhil pays full price |
| s_415p_carshowroom_yash | Yash sells car, false "never in accident" claim, Meher pays full price |
| s_415p_ringseller_devika | Devika sells ring, false "natural diamond" claim, Farid pays premium |
| s_415p_landdeal_ismail | Ismail sells land, false "clear title" claim, Radha pays deposit/signs deed |
| s_415p_used_laptop | Ashok sells laptop, conceals damage, false "refurbished" claim, Priti pays |
| s_415p_tuition_scholarship | Govind, agent, false "scholarship reserved" claim, Amira pays fee |
| s_415p_antique_jewelry | Mohit, dealer, false "century-old/18k gold" claim, Sunita pays |
| s_415p_expired_cosmetics | Divya conceals expiry, false "new stock" claim, Chandrika pays |
| s_415p_rental_misrepresentation | Bhupesh misrepresents apartment via staged photos, Divya signs lease/pays rent |
| s_415p_coaching_guarantee | Varun hides a guarantee condition in fine print, Preeti pays fee |

**Verdict — flag:** `s_415p_showroom_tara`, `s_415p_carshowroom_yash`, `s_415p_ringseller_devika`, `s_415p_used_laptop`, `s_415p_antique_jewelry`, `s_415p_expired_cosmetics` are rephrasings of one fact pattern: seller falsely represents a quality/authenticity/condition attribute of retail goods to induce full/premium payment; buyer pays and takes delivery/possession. The el0/el1/el2 sentence templates are near-identical across all six ("X falsely represented [item] as [attribute], when [s/he knew / it was actually] Y" / "done dishonestly, aimed at securing [buyer]'s payment" / "[Buyer] paid/delivered [price] and took delivery/possession of the [item]"), with only the item and specific false claim swapped. `s_415p_landdeal_ismail`, `s_415p_tuition_scholarship`, `s_415p_rental_misrepresentation`, `s_415p_coaching_guarantee` each involve a different property type (deposit+deed, service fee, lease, enrollment) and a different mode of deception (fabricated legal status, fabricated service, staged listing, buried condition) and are materially distinct.

All 10 correctly fit the property-inducement limb of cheating. No mis-filings within this group.

### B. Entailment leak (omit_0 = el0, omit_1 = el1, omit_2 = el2 dropped; 30 slices read)

**omit_0 and omit_2: clean, 0/20 leak.** The el1/el2 phrasing is deliberately generic ("Whatever passed between them was calculated dishonestly...") and never restates el0's specific false claim; el1's stated *intent* to secure payment never entails el2's *completed* payment fact.

**omit_1 (dishonest-intent element dropped, el0+el2 retained): 6/10 leak**, wherever el0 itself already asserts scienter/dishonesty:

| scenario_id | slice | dropped | leaking fact | quoted phrase | why |
|---|---|---|---|---|---|
| s_415p_showroom_tara | omit_1 | el1 (dishonest intent) | F_el0 | "**when she knew** it was a cheap replica" | el0 already states Tara's guilty knowledge, which is the substance of the dropped mental-element fact |
| s_415p_carshowroom_yash | omit_1 | el1 | F_el0 | "**when he knew** it had been badly damaged and repaired" | same — knowledge-of-falsity restates el1 |
| s_415p_ringseller_devika | omit_1 | el1 | F_el0 | "**when she knew** it was a lab-grown one" | same |
| s_415p_landdeal_ismail | omit_1 | el1 | F_el0 | "**when he knew** it was under a pending inheritance dispute" | same |
| s_415p_used_laptop | omit_1 | el1 | F_el0 | "Ashok **dishonestly** concealed the extensive damage" | el0 uses the word "dishonestly" outright — direct restatement of the dropped mental element |
| s_415p_expired_cosmetics | omit_1 | el1 | F_el0 | "**dishonestly** represented the skincare creams as newly stocked" | same, explicit "dishonestly" in el0 |

Clean on omit_1: `s_415p_tuition_scholarship`, `s_415p_antique_jewelry`, `s_415p_rental_misrepresentation`, `s_415p_coaching_guarantee` — their el0 states only objective falsity ("falsely represented...", "it was actually...") without asserting the seller's own knowledge or dishonesty.

---

## ipc415_damaging_act (10 scenarios)

### A. Distinctness
| scenario_id | F_narrative |
|---|---|
| s_415d_signature_omar | Omar lies to Priya about partner's loan approval; induces co-signature; default causes financial harm |
| s_415d_drugtest_kabir | Kabir lies to employer about Renu's drug test; induces suspension; lost wages + reputational harm |
| s_415d_invoice_tanya | Tanya lies to partner about invoice verification; induces payment release; financial harm |
| s_415d_tenancy_rustam | Rustam lies to landlord about Salma's payment history; induces cancellation; lost deposit + reputational harm |
| s_415d_medical_credentials | Prateek forges diploma; induces hiring; causes patient injury |
| s_415d_insurance_overstatement | Anjali submits false damage photos; induces inflated payout; she absconds, creditors unable to recover |
| s_415d_school_enrollment_sibling | Rohit falsely claims sibling relationship; induces enrollment; displaces a qualified applicant |
| s_415d_property_foundation_concealment | Deepti conceals foundation cracks; induces purchase w/o inspection; repair costs |
| s_415d_research_fellowship_fraud | Nehal forges publications; induces fellowship award; reputational harm to institution |
| s_415d_building_safety_certification | Sunita falsely certifies safety inspections; induces occupancy w/o inspectors; collapse causes injury |

**Verdict — flag two pairs:**
- `s_415d_signature_omar` + `s_415d_invoice_tanya`: identical template — X falsely tells a **business partner** that a prior approval/verification had occurred (loan sign-off / invoice verification) when it had not, inducing the partner to make a financial commitment they "would not otherwise have done," causing the business/partner financial harm.
- `s_415d_drugtest_kabir` + `s_415d_tenancy_rustam`: identical template — X falsely tells an **authority figure** (employer / landlord) a negative fact about an uninvolved third party ("failed a drug test" / "poor payment history") that has no basis, inducing an adverse action against that third party, causing them combined **financial and reputational** harm — el2 is nearly interchangeable between the two ("financial harm through lost wages and reputational harm among colleagues" vs. "financial harm (a lost deposit) and reputational harm with other landlords").

`s_415d_medical_credentials`, `s_415d_school_enrollment_sibling`, `s_415d_property_foundation_concealment`, `s_415d_research_fellowship_fraud`, `s_415d_building_safety_certification` are materially distinct from each other and from the above pairs (different harm types: physical injury, opportunity displacement, repair cost, institutional reputational harm).

**Mis-filing concern:** `s_415d_insurance_overstatement` — the directly deceived/induced party (the insurer) suffers a straightforward property loss (an inflated cash payout obtained by deception), which is the canonical ipc415_property pattern (cheating inducing delivery of property), not the "induced act causing damage to a third party" pattern used by the rest of this contract_id. The drafted el2 works around this by pinning the "damage" on unrelated third-party creditors via an attenuated, unrelated causal chain (Anjali using the money to abscond from a separate debt) rather than the insurer's own loss. Worth reviewer attention — weak fit for this limb.

### B. Entailment leak (omit_0 = el0, omit_2 = el2 dropped; 20 slices read)
None leak. el1 in every scenario is phrased generically ("[X] was intentionally induced to [act], something it would not otherwise have done") and never restates el0's specific false claim; el0+el1 together describe the induced act but never necessitate el2's downstream harm (e.g., co-signing a loan does not entail the loan later defaults; occupying a building does not entail it later collapses). **0/20 omission slices leak.**

---

## ipc416 (10 scenarios)

### A. Distinctness
| scenario_id | F_narrative |
|---|---|
| s_416_examhall_bina | Bina impersonates sister Leela via admit card to sit an exam |
| s_416_jobinterview_kiran | Kiran impersonates twin brother Milan for a job interview |
| s_416_courier_waseem | Waseem impersonates neighbour Farooq to sign for his courier package |
| s_416_licensingexam_juhi | Juhi impersonates Isha via admit card to sit a licensing exam |
| s_416_inheritance_pretense | Vikram impersonates a deceased man's adopted son to claim inheritance |
| s_416_exam_hall_substitution | Divya sits a medical entrance exam in Pooja's place, using Pooja's identity |
| s_416_hotel_check_in_fraud | Arun uses a stolen card + forged ID to check into a hotel as the cardholder |
| s_416_job_reference_impersonation | Shreya impersonates a candidate's former employer on a reference call |
| s_416_lottery_ticket_substitution | Nikhil alters a lottery ticket to substitute his own name for the original holder's |
| s_416_rental_sister_substitution | Maya uses her sister's ID to sign a lease as her sister |

**Verdict — flag two clusters:**
- `s_416_examhall_bina`, `s_416_licensingexam_juhi`, `s_416_exam_hall_substitution`: same fact pattern — a candidate's exam is sat under someone else's identity via that person's admit card, inducing exam staff to credit the sitting to the true candidate's name/registration. el0 and el1 are essentially interchangeable across all three, only the exam type (qualifying/licensing/medical entrance) and who benefits (self vs. arranged proxy) differ.
- `s_416_hotel_check_in_fraud` + `s_416_rental_sister_substitution`: moderate rephrasing — X uses another person's identity documents to present as that person, inducing a service provider (hotel / landlord) to grant access or execute a transaction (room booking / lease). Weaker overlap than the exam trio (stolen-card-plus-forged-ID vs. borrowed genuine ID; room access vs. lease) but the same "borrowed-identity-documents induce a grant of access" skeleton.

`s_416_jobinterview_kiran`, `s_416_courier_waseem`, `s_416_inheritance_pretense`, `s_416_job_reference_impersonation`, `s_416_lottery_ticket_substitution` are each materially distinct.

**Mis-filing concern:** `s_416_lottery_ticket_substitution` — Nikhil alters the physical ticket document and then presents it **as himself**, not by pretending to be the original ticket holder or any other real/fictitious individual. This is closer to document forgery/fraud than "cheating by personation" (pretending to be another person / knowingly substituting one person for another in an act). Worth reviewer attention.

### B. Entailment leak (omit_0 = el0, omit_1 = el1 dropped; 20 slices read)

**omit_0: clean, 0/10 leak** — the personation fact alone does not restate whether the officer/staff was actually induced.

**omit_1 (personation act dropped, el0 retained): 3/10 plausible leaks**, confined to the exam-personation cluster, where el0's phrasing about *whose name* the sitting is credited to only makes sense if the sitter and the named candidate diverge:

| scenario_id | slice | dropped | leaking fact | quoted phrase | why |
|---|---|---|---|---|---|
| s_416_examhall_bina | omit_1 | el1 (Bina pretended to be Leela) | F_el0 | "**credit the sitting to Leela's name and registration**" | phrasing "credit ... to [name]" only needs saying if the actual sitter is someone else — presupposes a substitution took place |
| s_416_licensingexam_juhi | omit_1 | el1 | F_el0 | "**credit the sitting under Isha's registration**" | same reasoning |
| s_416_exam_hall_substitution | omit_1 | el1 | F_el0 | "**credit the high score to Pooja's name** and admit her" | same reasoning |

Flagged as PLAUSIBLE rather than definite — the phrase is suggestive of a name/sitter mismatch rather than a strict logical necessity, but is a real tell worth a wording fix. The other 7 scenarios' el0 ("staff induced to grant access/complete booking/execute lease/etc.") carries no such tell and is clean.

---

## ipc182_misdirected_act (10 scenarios)

### A. Distinctness
| scenario_id | F_narrative |
|---|---|
| s_182a_neighbour_farhan | Farhan falsely denies Kavya has a building permit to a municipal officer, wants demolition |
| s_182a_taxraid_geeta | Geeta falsely claims rival Manav hides income to a tax officer, wants a raid |
| s_182a_licenserevoke_pankaj | Pankaj falsely denies Sameera has a food-safety cert to a licensing officer, wants revocation |
| s_182a_customsflag_wahid | Wahid falsely claims traveller Latifa carries undeclared goods, wants detention/search |
| s_182m_zoning_false_environmental | Aman falsely claims env. violations to a zoning officer, wants variance denied |
| s_182m_license_false_bribery | Priya falsely claims applicant bribery to a license examiner, wants fail + discipline |
| s_182m_police_crime_scene_alibi | Rajesh falsely places competitor at a crime scene, wants an arrest warrant |
| s_182m_tax_audit_false_evasion | Ananya falsely claims employee hid income, wants penalties + prosecution |
| s_182m_university_grade_manipulation | Arjun falsely accuses a professor of grade manipulation, wants suspension |
| s_182m_customs_false_smuggling | Harpreet falsely claims a truck carries contraband, wants confiscation |

**Verdict — flag three pairs** (on top of noting the whole set shares one abstract 3-clause template — false report / intent to induce action / description of the action the officer "ought not to take" — which is a structural pattern worth flagging on its own even though most individual pairs differ enough in target/domain to pass a strict distinctness test):
- `s_182a_neighbour_farhan` + `s_182a_licenserevoke_pankaj`: near-verbatim — el0 uses the identical clause **"knew to be false since he/she had seen [the document] himself/herself"** in both (permit vs. certificate), reporting to a local regulatory officer to trigger a punitive action against a competitor's property/business.
- `s_182a_taxraid_geeta` + `s_182m_tax_audit_false_evasion`: both are a false unreported-income accusation made to a tax authority (officer/auditor) about a rival/employee, to trigger a raid or penalties+prosecution — same core mechanism.
- `s_182a_customsflag_wahid` + `s_182m_customs_false_smuggling`: both are a false undeclared-goods/contraband report to a customs/immigration official, to trigger detention/search or confiscation — same core mechanism.

The remaining four (`zoning_false_environmental`, `license_false_bribery`, `police_crime_scene_alibi`, `university_grade_manipulation`) differ enough in domain and specific claim to stand as distinct scenarios, though they share the same macro-template as the rest of the set.

All 10 correctly fit the misdirected-act (false-information-to-a-public-servant) limb. No mis-filings.

### B. Entailment leak (omit_1 = el1, omit_2 = el2 dropped; 20 slices read)

**omit_1: clean, 0/10 leak.** el2 is phrased as a conditional/hypothetical ("[Action] is an act the officer ought not to take once the true facts were known") and never asserts that the reporter intended or achieved that specific outcome.

**omit_2 (dropped: the "action officer ought not to take" fact; el0+el1 retained): 6/10 leak**, confined to the `s_182m_*` batch, where el1 names the specific adverse action verbatim/near-verbatim instead of staying generic:

| scenario_id | slice | dropped | leaking fact | quoted phrase | why |
|---|---|---|---|---|---|
| s_182m_zoning_false_environmental | omit_2 | el2 (denial of variance) | F_el1 | "intends to cause the zoning officer to **deny the variance application**" | el1 already names the exact action ("denial of a zoning variance") that el2 describes |
| s_182m_license_false_bribery | omit_2 | el2 (fail applicant + discipline) | F_el1 | "intends to cause the examiner to **fail the applicant and file a disciplinary complaint**" | near word-for-word match to el2's "Failing an applicant and initiating a disciplinary complaint" |
| s_182m_police_crime_scene_alibi | omit_2 | el2 (arrest warrant + detention) | F_el1 | "intends to cause the officer ... to **obtain an arrest warrant** for his competitor" | matches el2's "Issuance of an arrest warrant ... of the competitor" |
| s_182m_tax_audit_false_evasion | omit_2 | el2 (penalties + prosecution) | F_el1 | "intends to cause the auditor to **impose financial penalties and initiate prosecution**" | near word-for-word match to el2's "Imposing penalties on and initiating prosecution of the employee" |
| s_182m_university_grade_manipulation | omit_2 | el2 (suspension) | F_el1 | "intends to cause the dean to **suspend the faculty member from duties**" | near word-for-word match to el2's "Suspension of a faculty member from teaching duties" |
| s_182m_customs_false_smuggling | omit_2 | el2 (confiscation) | F_el1 | "intends to cause the customs official to ... **confiscate the cargo**" | matches el2's "Confiscation of legitimate cargo" |

Clean on omit_2: the four `s_182a_*` scenarios, whose el1 stays deliberately generic ("intended to cause the officer to act on the information given") without naming the specific action, so el2's specific consequence is not pre-empted.

---

## Summary table

| contract_id | scenarios | rephrasing pairs/clusters | mis-filed | leaking omission slices |
|---|---|---|---|---|
| ipc405_misappropriation | 10 | 0 | 0 | 0/20 |
| ipc405_use_or_disposal | 10 | 1 four-way cluster | 0 | 10/20 (all omit_2) |
| ipc405_wilfully_suffers | 10 | 1 four-way cluster | 0 | 6/20 (all omit_0, s_405s_* batch) |
| ipc415_property | 10 | 1 six-way cluster | 0 | 6/30 (all omit_1) |
| ipc415_damaging_act | 10 | 2 pairs | 1 (insurance_overstatement, weak fit) | 0/20 |
| ipc416 | 10 | 1 three-way + 1 two-way cluster | 1 (lottery_ticket_substitution, weak fit) | 3/20 (plausible, all omit_1) |
| ipc182_misdirected_act | 10 | 3 pairs (+ whole-set macro-template note) | 0 | 6/20 (all omit_2, s_182m_* batch) |
| **Total** | **70** | **6 clusters/pair-groups** | **2 weak-fit flags** | **31/150** |
