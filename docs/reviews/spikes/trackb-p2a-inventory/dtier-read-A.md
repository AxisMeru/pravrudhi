# D-tier full merge-gate read — file A (dtier-flat-A.json)

**Counts:** 7 contract_ids × 10 scenarios = 70 scenarios, 250 records read in full.
**Rephrasing clusters found:** 2 confirmed (ipc182_abuse_of_power: 4-scenario cluster + 6-scenario cluster) + 1 confirmed pair (bns69: jobpromo/scholarship) + 1 soft/noted (not flagged) duplication of predicate-crime choice (bns46_intentional_aid: getawaycar/disguisesupplier).
**Mis-filed scenarios found:** 0.
**Leaking omission slices found:** 9 of 170 omission slices read (bns69: 6; bns47: 3). All other contract_ids (ipc182_abuse_of_power, bns85, bns46_instigation, bns46_conspiracy, bns46_intentional_aid) are clean.

---

## A. Distinctness

### ipc182_abuse_of_power (10 scenarios)

| scenario_id | F_narrative |
|---|---|
| s_182b_customs_zara | Zara falsely reported to a customs officer that a rival trader, Imran, was smuggling contraband electronics, hoping to trigger a punishing search of his shop. |
| s_182b_policecomplaint_tariq | Tariq falsely complained to police that his neighbour, Aisha, had threatened him with violence, hoping police would harass her with repeated visits. |
| s_182b_healthinspector_bilal | Bilal falsely told a health inspector that a rival restaurant, run by Choi, was serving contaminated food, hoping to trigger a punishing inspection. |
| s_182b_wildlifeofficer_nadia | Nadia falsely told a wildlife officer that a rival farmer, Om, was illegally hunting protected animals on his land, hoping to trigger a punishing search of his property. |
| s_182a_health_inspector_sanitary | Divij falsely reports to a health inspector that a restaurant has violated sanitary storage requirements, knowing full inspections show compliance, intending to cause the inspector to issue a business closure order that causes the owner significant financial injury. |
| s_182a_labor_wage_violation | Sanjana falsely reports to a labor commissioner that a factory employer has violated minimum wage laws, knowing payroll records verify full compliance, intending to cause the commissioner to launch an investigation and impose penalties, causing reputational and financial harm. |
| s_182a_building_earthquake_safety | Vikram falsely reports to a building inspector that a residential construction project violates earthquake safety codes, knowing the structure meets all specifications, intending to cause the inspector to issue a work stoppage order causing construction delays and financial losses. |
| s_182a_environmental_pollution_discharge | Pooja falsely reports to an environmental protection officer that a factory is illegally discharging pollutants into a river, knowing the factory operates within all compliance limits, intending to cause the officer to seize equipment and impose environmental penalties causing financial injury. |
| s_182a_fire_safety_exit_blockage | Aman falsely reports to a fire safety inspector that a hotel has blocked emergency exits, knowing all exits are properly maintained and marked, intending to cause the inspector to issue a temporary closure notice that damages the hotel's business and reputation. |
| s_182a_vehicle_emissions_defeat_device | Neha falsely reports to a vehicle emissions authority that a vehicle owner has installed equipment to defeat emissions controls, knowing the vehicle is completely unmodified and compliant, intending to cause the authority to suspend registration and impose fines causing financial injury. |

**Verdict — REPHRASING, two clusters, 10/10 scenarios implicated:**

*Cluster 1 (4 scenarios: customs_zara, policecomplaint_tariq, healthinspector_bilal, wildlifeofficer_nadia).* Identical frame: `[person] falsely told/reported to [an official] that [a rival/neighbour] was [X], hoping to trigger a punishing [search/harassment]`. F_el1 across all four is the literal template `[X] knew it was likely that the [official] would act on the information given`, and F_el2 is `The [official]'s lawful power ... would cause [target] annoyance` — same sentence, only the official-type and false-claim-type substituted. Only the professional domain (customs/police/health/wildlife) and the specific false claim are swapped; parties, conduct, object (trigger punitive state action out of personal rivalry, causing "annoyance"), and setting are the same fact pattern with words changed.

*Cluster 2 (6 scenarios: health_inspector_sanitary, labor_wage_violation, building_earthquake_safety, environmental_pollution_discharge, fire_safety_exit_blockage, vehicle_emissions_defeat_device).* Identical frame: `[person] falsely reports to a [regulator] that [entity] has/is [violation], knowing [compliance], intending to cause the [regulator] to [issue penalty/closure], causing [financial injury]`. F_el0 template: `[X] provides the [regulator] with false information about [violation], knowing his/her [statement/report/allegation] to be false/untrue`. F_el1 template: `[X] intends to cause the [regulator] to exercise [investigation/closure/suspension] authority`. F_el2 template: `[consequence] represents the [regulator]'s use of lawful regulatory power to cause financial injury to the [owner]`. Six different regulatory domains, one mad-libs skeleton.

No cross-cluster overlap beyond the shared genre (false report to an official); within each cluster the scenarios are not independent draws — they are the same fact pattern re-skinned. No scenario is mis-filed relative to ipc182's own limb (false information to a public servant, known likely to cause exercise of lawful power to another's injury/annoyance) — all 10 fit that limb correctly.

### bns69 (10 scenarios — false-promise/deceit inducement to intercourse)

| scenario_id | F_narrative |
|---|---|
| s_bns69_jobpromo_deepa | Rohit induced Deepa to have sexual intercourse with him by falsely promising her a management position at his company, a promotion he never intended to give her. |
| s_bns69_scholarship_naina | Vikrant induced Naina to have sexual intercourse with him by falsely promising to arrange a scholarship for her at a foreign university, a promise he never intended to keep. |
| s_bns69_marriagepromise_shalini | Anand induced Shalini to have sexual intercourse with him by promising to marry her, a promise he made without any intention of ever fulfilling it. |
| s_bns69_suppressedidentity_reema | Faisal induced Reema to have sexual intercourse with him by suppressing his true identity, letting her believe he was someone she was already engaged to. |
| s_bns69_actor_casting | A film casting director falsely promises a lead role in a major production to an aspiring actress, conditions sexual intercourse on this promise, the intercourse occurs, and medical evidence later establishes the woman was acutely intoxicated at the time. |
| s_bns69_visa_sponsorship | An employer falsely promises work visa sponsorship to a prospective employee, demands sexual intercourse as a condition, intercourse occurs, and investigation later reveals the woman was below 18 years of age. |
| s_bns69_medical_treatment | A private physician falsely promises specialized medical care for a chronic patient condition in exchange for sexual contact, intercourse occurs, and coercive threats regarding withholding essential treatment are later documented. |
| s_bns69_business_partnership | An entrepreneur falsely promises a formal business partnership to a woman entrepreneur, conditions sexual intercourse on finalizing the deal, intercourse occurs, and witnesses later testify to physical force and overwhelming resistance. |
| s_bns69_immigration_counsel | An immigration attorney falsely promises to immediately resolve pending deportation proceedings for a client, conditions sexual intercourse on this promise, intercourse occurs, and medical records show the woman's judgment was materially impaired by prescribed medication. |
| s_bns69_adoption_facilitation | A social worker falsely promises to expedite adoption procedures for a childless couple, demands sexual intercourse from the woman, intercourse occurs, and written threats to block the adoption if she refuses are later discovered. |

**Verdict — one confirmed rephrasing pair, rest distinct.**

`s_bns69_jobpromo_deepa` and `s_bns69_scholarship_naina`: F_el0 is the same sentence with only the promised benefit swapped — "induced [victim] by deceitful means, specifically a false promise of [promotion at his company / to arrange a foreign scholarship] that he never intended to keep." Both are "false promise of a career-advancement benefit, no marriage/identity angle" — same conduct, same object-type (career opportunity dangled), same setting (someone with power over an opportunity), parties swapped. Flag as rephrasing.

`s_bns69_marriagepromise_shalini` (false promise to marry — a legally distinct BNS s.69 ground) and `s_bns69_suppressedidentity_reema` (deceit as to identity/impersonation of a fiancé — also a distinct ground) are each unique, not rephrasings of 1/2 or of each other.

The six "conditions sex on X, intercourse occurs, and Y is later discovered" scenarios (actor_casting, visa_sponsorship, medical_treatment, business_partnership, immigration_counsel, adoption_facilitation) share a scaffold but each instantiates a genuinely different promised benefit (role/visa/treatment/partnership/legal resolution/adoption) *and* a genuinely different independent "deny" ground (intoxication/minority/threat-coercion/force/medication-impairment/threat) — that systematic variation across the deny-ground dimension is the intended design, not a duplicate fact pattern. Not flagged.

No mis-filing: all ten fit bns69's "induced by deceitful means" limb (false promise / suppressed identity are both recognised deceitful-means routes).

### bns47 (10 scenarios — extraterritorial abetment, s.45/47)

| scenario_id | F_narrative |
|---|---|
| s_bns47_assault_salim | Salim, while in Mumbai, instigated his associate Karim to assault a rival trader in Dubai, an act that would be a serious offence under Indian law had it occurred in India. |
| s_bns47_armsdeal_yasmin | Yasmin, working from her Delhi office, urged her overseas contact Omar toward organising a covert arms shipment bound for Bangkok. |
| s_bns47_investmentfraud_devansh | Devansh, in Chennai, engaged with Priyanka in a conspiracy to run a fraudulent investment scheme targeting victims in Singapore. |
| s_bns47_kidnapplot_farah | Farah, in Kolkata, intentionally aided her associate Zubin by wiring him funds to carry out a kidnapping plot in Kathmandu. |
| s_bns47_theft_instigation | A man in Delhi instigates his brother to steal electronics from a retail outlet in Jakarta, providing explicit instructions and encouragement via messaging; the theft constitutes an offence under Indian law. |
| s_bns47_assault_conspiracy | Two cousins in Mumbai conspire together to arrange a serious physical assault on a business rival, coordinating plans entirely within India for execution in Doha; grievous injury is explicitly intended. |
| s_bns47_document_fraud_aid | A woman in Bangalore provides forged government identity documents and financial transfers to an associate to enable large-scale identity fraud targeting residents in Dubai; her actions materially assist the overseas fraud scheme. |
| s_bns47_accounting_fraud_instigation | A CEO in Hyderabad instigates an accountant to perpetrate false financial statement entries on company accounts held in Hong Kong; fraudulent accounting manipulation is the explicit goal. |
| s_bns47_goods_trafficking_conspiracy | Three individuals in Punjab conspire to traffic restricted hazardous materials across international borders into Myanmar, allocating specific roles and finalizing transportation plans entirely within India. |
| s_bns47_extortion_assistance | A debt collector in Kolkata provides money laundering services and coaching in intimidation techniques to an extortion network to enable an extortion racket targeting overseas victims in Bangkok; his services directly facilitate the scheme. |

**Verdict — materially distinct, no rephrasing pairs, no mis-filing.** All ten vary genuinely on crime type (assault, arms dealing, investment fraud, kidnapping, theft, accounting fraud, identity fraud, goods trafficking, extortion) and on abetment route (instigation: assault_salim, armsdeal_yasmin, theft_instigation, accounting_fraud_instigation; conspiracy: investmentfraud_devansh, assault_conspiracy, goods_trafficking_conspiracy; intentional aid: kidnapplot_farah, document_fraud_aid, extortion_assistance). Repeating all three abetment routes under one contract_id is correct — bns47/s.47 covers all three as alternative routes to the same extraterritorial-abetment offence, so this is not a mis-filing.

### bns85 (10 scenarios — cruelty by husband/relative-of-husband, s.85/86)

| scenario_id | F_narrative |
|---|---|
| s_bns85_inlaws_meera | Meera's husband's brother, Suresh, repeatedly harassed her to pressure her family into paying an additional dowry demand. |
| s_bns85_motherinlaw_kavita | Kavita's mother-in-law, Shanta, repeatedly threatened her with violence to coerce her into giving up her share of a family property. |
| s_bns85_brotherinlaw_ritu | Ritu's husband's younger brother, Pawan, locked her out of the kitchen and withheld her meals for days at a stretch, pressing her to sign over her jewellery to settle his gambling debts. |
| s_bns85_husband_anjali | Anjali's husband, Rajeev, repeatedly threatened self-harm in front of her to coerce her family into transferring assets to him. |
| s_bns85_mother_in_law_dowry | A mother-in-law systematically harasses her daughter-in-law through verbal abuse and threats, explicitly demanding additional jewelry and cash; the harassment culminates in forcible confinement. |
| s_bns85_brother_in_law_mental | A brother-in-law deliberately isolates his sister-in-law from family and friends, makes repeated dehumanizing remarks about her character, and threatens suicide if she disobeys his commands. |
| s_bns85_father_in_law_physical | A father-in-law regularly beats and physically assaults his daughter-in-law over household disputes, causing visible bruising and documented internal injuries; assaults are deliberate and escalating. |
| s_bns85_sister_in_law_coercion | A sister-in-law leverages household authority to threaten and harass her brother's wife, withholding food and basic necessities and demanding expensive jewelry and cash as unlawful compensation. |
| s_bns85_husband_torture | A husband deliberately tortures his wife through sustained physical violence, breaking her bones and causing internal injuries; violence escalates over months and is documented by medical professionals and hospital records. |
| s_bns85_cousin_in_law_property | A cousin-in-law manipulates household hierarchy to coerce his brother's wife into transferring ancestral property deeds and liquid savings, threatening family ostracism and loss of inheritance. |
| — | |

**Verdict — materially distinct, no rephrasing, no mis-filing.** Relation-type repeats (mother-in-law: kavita/mother_in_law_dowry; husband: anjali/husband_torture; brother-in-law/husband's-brother family: meera/ritu/brother_in_law_mental) are expected — the contract requires varying "relative of husband" types — but each repeat pairs a different specific conduct and object (kavita: threatened violence over property share vs mother_in_law_dowry: verbal abuse + confinement over jewelry/cash; anjali: self-harm threats to coerce asset transfer vs husband_torture: physical torture/broken bones; meera: dowry harassment vs ritu: starvation/confinement over gambling debt vs brother_in_law_mental: isolation/suicide threats). None collapse to a word-swapped duplicate.

### bns46_instigation (10 scenarios)

| scenario_id | F_narrative |
|---|---|
| s_46i_forgedletter_arjun | Arjun wilfully forged a letter appearing to come from a magistrate, inducing constable Naveen to arrest an innocent man, Deshraj. |
| s_46i_falsealarm_divya | Divya wilfully told a fire officer a false story that her neighbour's shop had an illegal gas connection, inducing the officer to forcibly shut it down. |
| s_46i_stagedinjury_ketan | Ketan wilfully concealed from an insurance assessor that his claimed injury was staged, inducing the assessor to authorise a fraudulent payout to his accomplice, Rohan. |
| s_46i_courtclerk_leena | Leena wilfully misrepresented to a court clerk that a case file had already been dismissed, inducing the clerk to release a seized vehicle to Leena's associate, Sameer. |
| s_46i_doctor_diploma_fraud | Dr. Priya Sharma deliberately conceals her lack of legitimate medical credentials and misrepresents her qualifications to her colleague Amit Kumar, urging him to apply for a medical license using falsified educational documents. |
| s_46i_factory_pollution_concealment | Rajesh Patel, a factory manager, deliberately conceals pollution-reporting requirements from newly hired employee Sunita Desai and urges her to destroy industrial effluent monitoring records to avoid regulatory scrutiny. |
| s_46i_land_registration_fraud | Advocate Vikram Singh deliberately misrepresents the true ownership history and conceals existing property encumbrances to property buyer Neha Gupta, thereby provoking her to submit falsified title deeds to the registration authority. |
| s_46i_export_subsidy_claim_scheme | Customs officer Mohan Kumar deliberately misrepresents export-refund eligibility rules and conceals audit documentation requirements to exporter Pallavi Nair, thereby inciting her to file inflated and false government subsidy claims. |
| s_46i_child_labour_concealment | Factory owner Sarita Devi deliberately conceals the legal prohibition on child employment in hazardous work from labor contractor Jagesh Rao and urges him to hire minors at suppressed wages without proper documentation. |
| s_46i_insurance_claim_inflation | Banker Harish Malhotra deliberately misrepresents policy-loan eligibility criteria and conceals verification procedures to account-holder Rupali Chopra, thereby provoking her to file an inflated and false insurance claim. |

**Verdict — materially distinct, no rephrasing, no mis-filing.** Ten different deceit contents and ten different induced offences (wrongful confinement, wrongful restraint of trade, cheating, breach of official duty, licensing fraud, environmental-evidence destruction, land-registration fraud, export-subsidy fraud, child-labour violation, insurance fraud). `stagedinjury_ketan` (insurance payout via staged-injury concealment) and `insurance_claim_inflation` (loan-eligibility misrepresentation provoking an inflated claim) both touch insurance but via different concealed facts and different induced acts (authorising a payout to an accomplice vs. the victim herself filing an inflated claim) — distinct, not a rephrasing.

### bns46_conspiracy (10 scenarios)

| scenario_id | F_narrative |
|---|---|
| s_46c_burglaryring_manoj | Manoj engaged with Hema and Suresh in a plan to burgle a warehouse; Hema, acting under that plan, cut the perimeter fence to let the others in. |
| s_46c_smugglingring_faizan | Faizan engaged with Rekha and Suraj in a plan to smuggle goods past a checkpoint; Rekha, acting under that plan, bribed a guard to look away. |
| s_46c_ballotstuffing_gulzar | Gulzar engaged with Kamal and Asif in a plan to stuff a ballot box; Kamal, acting under that plan, distracted the polling officer while Asif inserted the extra ballots. |
| s_46c_extortionplot_hina | Hina engaged with Waqas and Imtiaz in a plan to extort a shopkeeper; Waqas, acting under that plan, delivered the threatening note. |
| s_46c_narcotics_distribution | Arun Sharma, Meera Desai, and Pradeep Nair engage in a conspiracy to distribute controlled narcotics throughout Mumbai, with Meera procuring drugs, Arun establishing distribution networks, and Pradeep collecting proceeds. |
| s_46c_tax_evasion_accounting | Accountant Venkat Krishnan and business owner Swapna Reddy conspire in Bangalore to conceal business income through falsified accounting records and underreported tax filings. |
| s_46c_tender_bid_rigging | Government official Ajay Verma and construction contractor Deepak Malhotra conspire in Lucknow to rig a public infrastructure tender by disclosing confidential bidding information. |
| s_46c_currency_counterfeiting | Rohit Singh and Navneet Kumar conspire in Amritsar to produce and distribute high-quality counterfeit Indian currency notes through an underground print operation. |
| s_46c_hospital_billing_fraud | Dr. Siddharth Das and accounting manager Kanchan Patel conspire in Pune to inflate medical bills by recording fictitious surgical procedures and diagnostic tests to insurance companies. |
| s_46c_vehicle_title_laundering | Gautam Chopra, Isha Patel, and Ashok Bhat conspire in Ahmedabad to purchase stolen vehicles, forge ownership documents, and re-register them under false identities. |

**Verdict — materially distinct, no rephrasing, no mis-filing.** Ten different target crimes (burglary, smuggling, ballot-stuffing, extortion, narcotics distribution, tax evasion, bid-rigging, counterfeiting, healthcare billing fraud, vehicle-title laundering), each with a distinct overt act in pursuance. No overlap.

### bns46_intentional_aid (10 scenarios)

| scenario_id | F_narrative |
|---|---|
| s_46a_getawaycar_sonal | Sonal, knowing her cousin Vikas planned to rob a jewellery store, parked her car outside the store beforehand specifically so Vikas could use it to flee. |
| s_46a_disguisesupplier_rana | Rana, knowing her friend Iqbal planned to rob a jewellery store, supplied him with a disguise beforehand specifically so he could evade identification during the robbery. |
| s_46a_lookout_farida | Farida, knowing her brother Salman planned to burgle a shop, stood watch outside as a lookout during the burglary to warn him of approaching police. |
| s_46a_toolsupply_waris | Waris, knowing his cousin Adil planned to break into a warehouse, supplied him with bolt cutters beforehand specifically so Adil could cut through the perimeter lock. |
| s_46a_blackmail_facilitator | Yuki Tanaka intentionally provides falsified intimate photographs and threatening messages to blackmailer Ravi Sinha in Lucknow, knowing these materials will be used to coerce payment from victims. |
| s_46a_smuggling_logistics_coordinator | Logistics coordinator Prakash Nair intentionally arranges safe transit routes, vehicle documentation, and concealment strategies for smuggler Sunil Menon's operations across the Indore customs barrier. |
| s_46a_drunk_driving_enabler | Bharati Malhotra intentionally withholds information that her friend Kunal Desai is intoxicated from other passengers and deliberately hands him car keys in Chandigarh, knowing he will drive impaired. |
| s_46a_document_forgery_supplier | Printer Govind Sinha intentionally supplies high-security blank forms and specialized printing materials to document forger Lakshmi Reddy in Bangalore, knowing she will use them to create false government certificates. |
| s_46a_witness_intimidation_spreader | Tarun Bisht intentionally spreads threatening messages and intimidating communications to witnesses in a criminal case against defendant Anita Gupta in Kanpur, knowing this will deter their testimony. |
| s_46a_illegal_weapons_supplier | Vikram Jadhav intentionally sells unregistered firearms and ammunition to Sameer Khan in Nagpur prior to a planned violent attack, knowing the weapons will facilitate the commission of that violence. |

**Verdict — not flagged as rephrasing, one soft note.** `s_46a_getawaycar_sonal` and `s_46a_disguisesupplier_rana` share the identical predicate crime ("planned to rob a jewellery store") and near-identical setup ("knowing [relative] planned to rob a jewellery store... beforehand specifically so [he] could..."), differing only in the aid item (getaway car vs. disguise) and the relative type (cousin vs. friend). Since the operative fact under test for this contract is the *aid conduct itself*, and that conduct genuinely differs (transportation vs. concealment-of-identity), this does not meet the rephrasing bar — but the choice to reuse the exact same predicate crime for two of ten slots is a minor redundancy worth flagging for the next authoring pass. Not counted in the rephrasing total. The other eight (lookout/burglary, bolt-cutters/warehouse break-in, blackmail/extortion, smuggling-logistics, drunk-driving, document-forgery, witness-intimidation, illegal-weapons) are all distinct predicate crimes and distinct aid conduct. No mis-filing.

---

## B. Entailment leak (omission slices only, "omit_*")

### Leaking slices found (9)

| scenario_id | slice | dropped element | leaking fact id | quoted phrase | why |
|---|---|---|---|---|---|
| s_bns69_actor_casting | omit_1 | el1 (intercourse occurred) | F_el0 | "induces Priya to sexual intercourse by falsely promising her the lead role" | El0's own wording already asserts the intercourse-inducement outcome, restating the very fact (that intercourse was induced/occurred) that el1 alone is supposed to supply. |
| s_bns69_visa_sponsorship | omit_1 | el1 | F_el0 | "induces Neha to sexual intercourse by making a false promise to sponsor her employment visa" | Same pattern — el0 states "induces ... to sexual intercourse," pre-empting el1. |
| s_bns69_medical_treatment | omit_1 | el1 | F_el0 | "induces Shreya to sexual intercourse by falsely promising free specialized medical treatment" | Same pattern. |
| s_bns69_business_partnership | omit_1 | el1 | F_el0 | "induces Radhika to sexual intercourse by falsely promising to formalize an equal business partnership" | Same pattern. |
| s_bns69_immigration_counsel | omit_1 | el1 | F_el0 | "induces Pooja to sexual intercourse by falsely promising to immediately resolve her deportation case" | Same pattern. |
| s_bns69_adoption_facilitation | omit_1 | el1 | F_el0 | "induces Divya to sexual intercourse by falsely promising to fast-track her adoption application" | Same pattern. |
| s_bns47_goods_trafficking_conspiracy | omit_2 | el2 (location outside India — Myanmar) | F_el0 | "traffic restricted materials by planning to transport them illegally into Myanmar" | El0 names the destination country verbatim, which is exactly the extraterritorial-location fact el2 alone is supposed to establish. |
| s_bns47_document_fraud_aid | omit_2 | el2 (location outside India — Dubai) | F_el0 | "identity theft fraud against overseas residents" | El0's "overseas residents" phrase presupposes the abroad-target fact that el2 is meant to supply, even though it doesn't name the specific country. |
| s_bns47_extortion_assistance | omit_2 | el2 (location outside India — Bangkok) | F_el0 | "extortion operations against overseas targets" | Same pattern — el0 presupposes the abroad-location fact via "overseas targets." |

Note: bns69's contrast pair — jobpromo_deepa, scholarship_naina, marriagepromise_shalini, suppressedidentity_reema — uses the safer phrasing "induced [victim] by deceitful means, specifically [promise]" (no "to sexual intercourse"), so their omit_1 slices do **not** leak. The leak is confined to the six later-authored "conditions sexual intercourse on this promise" scenarios.

Not flagged as leaks (checked and rejected): bns47's el3 ("would constitute an offence under Indian law if committed in India") is phrased as a counterfactual in every scenario's omit_2 slice, which might read as implying the act happened abroad — but this hypothetical phrasing is the double-criminality element itself (s.47's own defined test), not extra wording that reveals el2; treated as a definitional dependency, consistent with the batch-02 distinction between wording leaks and definitional dependencies, not a leak.

### Clean contract_ids (no leaks found)

- **ipc182_abuse_of_power** — 30 omission slices read (10 scenarios × 3), 0 leaks. Neither el0 (falsity), el1 (likelihood-of-action), nor el2 (annoyance/injury via lawful power) restates another dropped element in any of the 10 scenarios.
- **bns85** — 20 omission slices read (10 × 2), 0 leaks. el0 (relative-of-husband status) never states the cruelty conduct; el1 (cruelty conduct) never states the relationship.
- **bns46_instigation** — 20 omission slices read (10 × 2), 0 leaks. el0 (the misrepresentation/concealment act) does not restate el1's "would itself be an offence" characterization beyond naming the same underlying crime type, which is an unavoidable definitional dependency (you cannot describe an instigation without naming the object crime); el1 does not restate el0's specific deceit method.
- **bns46_conspiracy** — 20 omission slices read (10 × 2), 0 leaks. Same definitional-dependency pattern as bns46_instigation (el0 must name the conspired-to crime; el1's "with [names]' own knowledge and intention" is the statutory hypothetical test, not a restatement of the conspiracy itself).
- **bns46_intentional_aid** — 20 omission slices read (10 × 2), 0 leaks. Same pattern; el0's description of the aid necessarily overlaps in vocabulary with el1's crime-type label, but does not restate el1's "would itself be an offence" hypothetical, and el1 never names the aider or the aid method.
- **bns47** (remaining slices) — of 40 omission slices read, 37 are clean; only the 3 omit_2 slices listed above leak. omit_0 (abetment-conduct dropped) and omit_1 (India-presence dropped) are clean across all 10 scenarios — el1/el2/el3 never state who performed the abetment or how, and el0 never states where the abettor was physically located.
