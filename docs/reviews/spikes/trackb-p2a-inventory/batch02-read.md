# Batch-02 read: distinctness + entailment-leak audit

## Counts

- Contract IDs: 14, each with 4 scenarios → 56 scenarios total.
- Rephrasing pairs found (same parties/conduct/object/setting, words changed): **0**. Every scenario under every contract_id uses distinct named parties, objects, and settings — including the four visibly templated groups (`ipc405_wilfully_suffers`, `ipc182_*`, `bns47`, `bns85`, `bns46_*`), which vary conduct-carrier and object enough to count as materially distinct under the letter of the test. Noted as structurally templated, not flagged as rephrasing.
- Mis-filed scenarios found (facts don't fit the contract_id's limb): **0 confirmed**, **1 borderline** (`s_415d_invoice_tanya` under `ipc415_damaging_act` — see note below).
- Leaking omission slices found: **34 of 140** (24%), concentrated in 8 of 14 contract_ids. `ipc416` leaks on both omission directions in all 4 scenarios (8/8); `ipc182_abuse_of_power`, `bns69`, `bns47`, `bns46_conspiracy`, `bns46_intentional_aid` are fully clean (0 leaks, 12/8/16/8/8 slices read respectively).

---

## A. Distinctness per contract_id

### ipc405_misappropriation (4 scenarios)
- s_405m_warehouse_ravi: Ravi, a warehouse keeper, was entrusted with 200 sacks of rice belonging to Meena for safekeeping under a storage agreement.
- s_405m_jeweller_kunal: Priya entrusted her gold bangles to jeweller Kunal for repair; Kunal pawned them instead and kept the pawn money.
- s_405m_maintfund_harish: Harish, treasurer of a housing society, was given the society's collected maintenance funds to deposit in the bank; he instead spent part of it on personal debts.
- s_405m_sewingmachine_neha: Neha gave a tailor, Rakesh, an antique sewing machine to service; Rakesh sold it to a collector and kept the money.

Judgment: materially distinct (different parties, objects, professions). Common skeleton (entrustment → conversion for personal gain) is the shared legal test, not a rephrased fact pattern. All four fit the misappropriation limb (entrustment + dishonest conversion/sale/spend for own use). No misfile.

### ipc405_use_or_disposal (4 scenarios)
- s_405u_cold_storage_asha: Asha operated a cold-storage facility under a written contract requiring potatoes to be kept refrigerated at all times for the depositor, Farida.
- s_405u_artgallery_leela: Leela deposited a painting with gallery owner Devraj under a contract requiring it to be kept in a climate-controlled room; Devraj moved it to an ordinary room to display another piece.
- s_405u_grainsilo_baldev: Baldev ran a grain silo under a contract with a farmer cooperative requiring wheat to be kept segregated by farmer; Baldev mixed the wheat with another farmer's batch to save space.
- s_405u_vault_pooja: Pooja deposited family gold with a vault-keeper, Yusuf, under a contract requiring it to be kept in a sealed individual box; Yusuf moved it into a shared box to consolidate space.

Judgment: materially distinct (different objects/settings/parties). All four correctly test the "use/disposal in violation of contractual direction" limb (no conversion or sale — bailee mishandles in place, doesn't take for himself), correctly distinct from misappropriation. No misfile.

### ipc405_wilfully_suffers (4 scenarios)
- s_405w_locker_vikram: Vikram managed a bank locker room and was entrusted with the keys to Sunita's locker containing her jewellery.
- s_405w_safedeposit_irfan: Irfan managed a safe-deposit room and was entrusted with dominion over Fatima's box; he wilfully let his nephew take an item from it knowing it was unauthorized.
- s_405w_cashcounter_alka: Alka managed a shop's till and was entrusted with dominion over the day's cash; she wilfully allowed a co-worker to pocket some of it, pretending not to notice.
- s_405w_toolshed_prakash: Prakash managed a construction site's toolshed and was entrusted with dominion over the rented equipment; he wilfully let a subcontractor remove tools without signing them out.

Judgment: same abstract template (entrusted with dominion → third party takes/misuses it → protagonist wilfully lets it happen), but parties, objects, and settings are all distinct in each — materially distinct under the letter of the test, not a same-parties rephrasing. All four correctly test the "wilfully suffers another to misappropriate" limb (distinct from a direct-conversion misappropriation case). No misfile.

### ipc415_property (4 scenarios)
- s_415p_showroom_tara: Tara sold Nikhil a watch, falsely claiming it was a genuine imported brand, to induce him to pay full price for it.
- s_415p_carshowroom_yash: Yash sold a used car to Meher, falsely claiming it had never been in an accident, to get her to pay full market price.
- s_415p_ringseller_devika: Devika sold a ring to Farid, falsely claiming the stone was a natural diamond, to get him to pay a premium.
- s_415p_landdeal_ismail: Ismail sold a plot of land to Radha, falsely claiming it had clear title, to induce her to pay the deposit and sign the sale deed.

Judgment: materially distinct (different goods, false claims, parties). All four fit the "cheating — inducing delivery of property to the deceiver" limb (victim pays the deceiver directly). No misfile.

### ipc415_damaging_act (4 scenarios)
- s_415d_signature_omar: Omar falsely told Priya that her business partner had already signed off on a risky loan, inducing her to co-sign it herself and lose her savings.
- s_415d_drugtest_kabir: Kabir falsely told his employer that his colleague Renu had failed a mandatory drug test, inducing the employer to suspend her.
- s_415d_invoice_tanya: Tanya falsely told her business partner that a supplier's invoice had already been verified and approved, inducing him to release payment for goods never received.
- s_415d_tenancy_rustam: Rustam falsely told a landlord that a prospective tenant, Salma, had a poor payment history, inducing the landlord to cancel her tenancy offer.

Judgment: materially distinct fact patterns. **Borderline misfile note**: `s_415d_invoice_tanya` involves an induced *release of payment* (property leaving the victim's control), which structurally resembles the property-delivery route of `ipc415_property` rather than the pure act/omission-causing-harm route (co-signing, suspending, cancelling a tenancy) shared by the other three. It is defensible as `damaging_act` because the payment goes to a third-party supplier, not to the deceiver Tanya herself — so property is not delivered to the cheat, only harm is caused to the deceived party's business. Flagged as a close call, not a hard misfile.

### ipc416 (4 scenarios)
- s_416_examhall_bina: Bina sat a qualifying examination by pretending to be her sister Leela, whose name was on the admit card, to gain entry to the exam hall.
- s_416_jobinterview_kiran: Kiran attended a job interview pretending to be his more-qualified twin brother, Milan, whose name was on the shortlist, to get the position.
- s_416_courier_waseem: Waseem signed for a courier package addressed to his neighbour, Farooq, pretending to be Farooq so the courier would release it to him.
- s_416_licensingexam_juhi: Juhi appeared for a professional licensing exam by claiming to be a different registered candidate, Isha, whose admit card she had obtained.

Judgment: materially distinct (different targets/venues/parties). All four correctly fit personation-cheating. No misfile — but see Part B: el0 and el1 in this contract_id are not independently establishable from each other (structural entailment issue, not a filing issue).

### ipc182_misdirected_act (4 scenarios)
- s_182a_neighbour_farhan: Farhan falsely told a municipal officer that his neighbour Kavya had built her extension without any permit, hoping to get her structure demolished.
- s_182a_taxraid_geeta: Geeta falsely told a tax officer that her business rival, Manav, was hiding undeclared income, hoping to trigger a raid on his premises.
- s_182a_licenserevoke_pankaj: Pankaj falsely told a licensing officer that his competitor's restaurant, run by Sameera, was operating without a valid food-safety certificate.
- s_182a_customsflag_wahid: Wahid falsely told an immigration officer that a returning traveller, Latifa, was carrying undeclared goods, hoping she would be detained and searched.

Judgment: materially distinct (different officials, targets, false claims). All four fit the "intent to cause officer to do an act it ought not do" limb, correctly distinct from the abuse_of_power limb below (no misfile).

### ipc182_abuse_of_power (4 scenarios)
- s_182b_customs_zara: Zara falsely reported to a customs officer that a rival trader, Imran, was smuggling contraband electronics, hoping to trigger a punishing search of his shop.
- s_182b_policecomplaint_tariq: Tariq falsely complained to police that his neighbour, Aisha, had threatened him with violence, hoping police would harass her with repeated visits.
- s_182b_healthinspector_bilal: Bilal falsely told a health inspector that a rival restaurant, run by Choi, was serving contaminated food, hoping to trigger a punishing inspection.
- s_182b_wildlifeofficer_nadia: Nadia falsely told a wildlife officer that a rival farmer, Om, was illegally hunting protected animals on his land, hoping to trigger a punishing search of his property.

Judgment: materially distinct. Correctly uses the "knew it likely officer would act, lawful power → annoyance" limb (vs. misdirected_act's "intended an act the officer ought not take"), properly kept separate from the previous contract_id. No misfile.

### bns69 (4 scenarios)
- s_bns69_jobpromo_deepa: Rohit induced Deepa to have sexual intercourse with him by falsely promising her a management position at his company, a promotion he never intended to give her.
- s_bns69_scholarship_naina: Vikrant induced Naina to have sexual intercourse with him by falsely promising to arrange a scholarship for her at a foreign university, a promise he never intended to keep.
- s_bns69_marriagepromise_shalini: Anand induced Shalini to have sexual intercourse with him by promising to marry her, a promise he made without any intention of ever fulfilling it.
- s_bns69_suppressedidentity_reema: Faisal induced Reema to have sexual intercourse with him by suppressing his true identity, letting her believe he was someone she was already engaged to.

Judgment: materially distinct means of deceit (job, scholarship, marriage, identity). All fit "consent by deceitful means" limb. No misfile.

### bns47 (4 scenarios)
- s_bns47_assault_salim: Salim, while in Mumbai, instigated his associate Karim to assault a rival trader in Dubai.
- s_bns47_armsdeal_yasmin: Yasmin, in Delhi, instigated her contact Omar to arrange an illegal arms shipment in Bangkok.
- s_bns47_investmentfraud_devansh: Devansh, in Chennai, engaged with Priyanka in a conspiracy to run a fraudulent investment scheme targeting victims in Singapore.
- s_bns47_kidnapplot_farah: Farah, in Kolkata, intentionally aided her associate Zubin by wiring him funds to carry out a kidnapping plot in Kathmandu.

Judgment: materially distinct (different cities/crimes/abetment routes). el0 deliberately varies across instigation/conspiracy/intentional-aid — all valid abetment routes for the same extraterritorial-jurisdiction test, not a misfile.

### bns85 (4 scenarios)
- s_bns85_inlaws_meera: Meera's husband's brother, Suresh, repeatedly harassed her to pressure her family into paying an additional dowry demand.
- s_bns85_motherinlaw_kavita: Kavita's mother-in-law, Shanta, repeatedly threatened her with violence to coerce her into giving up her share of a family property.
- s_bns85_brotherinlaw_ritu: Ritu's husband's younger brother, Pawan, repeatedly humiliated her and withheld food to coerce her family into paying a fresh dowry demand.
- s_bns85_husband_anjali: Anjali's husband, Rajeev, repeatedly threatened self-harm in front of her to coerce her family into transferring assets to him.

Judgment: materially distinct relatives/conduct/motive. All fit "cruelty by husband or relative" limb. No misfile — but see Part B: the omission narrative for el0 leaks the relationship in all four.

### bns46_instigation (4 scenarios)
- s_46i_forgedletter_arjun: Arjun wilfully forged a letter appearing to come from a magistrate, inducing constable Naveen to arrest an innocent man, Deshraj.
- s_46i_falsealarm_divya: Divya wilfully told a fire officer a false story that her neighbour's shop had an illegal gas connection, inducing the officer to forcibly shut it down.
- s_46i_stagedinjury_ketan: Ketan wilfully concealed from an insurance assessor that his claimed injury was staged, inducing the assessor to authorise a fraudulent payout to his accomplice, Rohan.
- s_46i_courtclerk_leena: Leena wilfully misrepresented to a court clerk that a case file had already been dismissed, inducing the clerk to release a seized vehicle to Leena's associate, Sameer.

Judgment: materially distinct settings/officials/instruments of deceit. All fit the instigation-via-misrepresentation limb. No misfile.

### bns46_conspiracy (4 scenarios)
- s_46c_burglaryring_manoj: Manoj engaged with Hema and Suresh in a plan to burgle a warehouse; Hema, acting under that plan, cut the perimeter fence to let the others in.
- s_46c_smugglingring_faizan: Faizan engaged with Rekha and Suraj in a plan to smuggle goods past a checkpoint; Rekha, acting under that plan, bribed a guard to look away.
- s_46c_ballotstuffing_gulzar: Gulzar engaged with Kamal and Asif in a plan to stuff a ballot box; Kamal, acting under that plan, distracted the polling officer while Asif inserted the extra ballots.
- s_46c_extortionplot_hina: Hina engaged with Waqas and Imtiaz in a plan to extort a shopkeeper; Waqas, acting under that plan, delivered the threatening note.

Judgment: materially distinct crimes/conspirators. Each correctly requires an agreement plus an overt act in pursuance by a co-conspirator — properly distinct from the instigation and intentional-aid contract_ids. No misfile.

### bns46_intentional_aid (4 scenarios)
- s_46a_getawaycar_sonal: Sonal, knowing her cousin Vikas planned to rob a jewellery store, parked her car outside the store beforehand specifically so Vikas could use it to flee.
- s_46a_disguisesupplier_rana: Rana, knowing her friend Iqbal planned to rob a jewellery store, supplied him with a disguise beforehand specifically so he could evade identification during the robbery.
- s_46a_lookout_farida: Farida, knowing her brother Salman planned to burgle a shop, stood watch outside as a lookout during the burglary to warn him of approaching police.
- s_46a_toolsupply_waris: Waris, knowing his cousin Adil planned to break into a warehouse, supplied him with bolt cutters beforehand specifically so Adil could cut through the perimeter lock.

Judgment: materially distinct. Facilitation without an agreement (no conspiracy alleged) — correctly the intentional-aid limb, distinct from conspiracy above. No misfile.

**No rephrasing pairs found anywhere in the batch.**

---

## B. Entailment leak per omission slice

Table of confirmed leaks only (clean contract_ids summarized below the table).

| scenario_id | slice | dropped element | leaking fact id | quoted phrase | why |
|---|---|---|---|---|---|
| s_405m_maintfund_harish | omit_0 | el0 (entrustment) | F_narrative | "funds that came into Harish's hands **as treasurer**" | Naming Harish's role as treasurer presupposes the custodial/fiduciary position the dropped entrustment fact is meant to establish; contrast with the other 3 scenarios in this contract, whose omit_0 narratives carry no role tag. |
| s_405u_cold_storage_asha | omit_0 | el0 (deposit under written contract) | F_el2 | "**the written contract** expressly required continuous refrigeration" | Asserting what "the written contract" required presupposes a written contract exists — exactly the fact el0 states and omit_0 is meant to hide. |
| s_405u_artgallery_leela | omit_0 | el0 | F_el2 | "**the written contract** expressly required the painting to be kept in a climate-controlled room" | Same pattern — presupposes the contract el0 establishes. |
| s_405u_grainsilo_baldev | omit_0 | el0 | F_el2 | "**the written contract** expressly required Baldev to keep each farmer's wheat segregated" | Same pattern. |
| s_405u_vault_pooja | omit_0 | el0 | F_el2 | "**the written contract** expressly required Pooja's gold to be kept in a sealed individual box" | Same pattern. |
| s_405w_cashcounter_alka | omit_0 | el0 (entrustment with dominion) | F_narrative | "shop's cash till **managed by** Alka" | "Managed by" presupposes the custodial/managerial role that el0's entrustment fact establishes; contrast with locker_vikram/safedeposit_irfan omit_0 narratives, which name no role. |
| s_405w_toolshed_prakash | omit_0 | el0 | F_narrative | "rented equipment **managed by** Prakash" | Same pattern. |
| s_415p_showroom_tara | omit_0 | el0 (false representation) | F_el1 | "securing payment for the watch **as though it were genuine**" | "As though genuine" presupposes the watch is not actually genuine — restating the falsity el0 alone establishes. |
| s_415p_carshowroom_yash | omit_0 | el0 | F_el1 | "secure full market-price payment **as though the car were accident-free**" | Same pattern (implies the car was not accident-free). |
| s_415p_ringseller_devika | omit_0 | el0 | F_el1 | "securing a premium price **as though the stone were a natural diamond**" | Same pattern (implies the stone was not natural). Note: `s_415p_landdeal_ismail` omit_0 does **not** leak — its el1 text has no comparable "as though X" clause. |
| s_415d_signature_omar | omit_1 | el1 (induced to co-sign) | F_el2 | "Priya's **co-signature** exposed her to the lender's claim" | Asserting the co-signature's consequence presupposes the co-signing happened — the very outcome el1 states and omit_1 is meant to hide. |
| s_415d_drugtest_kabir | omit_1 | el1 (induced to suspend) | F_el2 | "Renu's **suspension** caused her financial harm" | Presupposes the suspension occurred. |
| s_415d_invoice_tanya | omit_1 | el1 (induced to release payment) | F_el2 | "**The payment released** caused the business financial harm" | Presupposes the payment was released. |
| s_415d_tenancy_rustam | omit_1 | el1 (induced to cancel tenancy) | F_el2 | "The **cancelled tenancy** caused Salma financial harm" | Presupposes the cancellation occurred. |
| s_416_examhall_bina | omit_0 | el0 (deceived invigilator) | F_el1 | "Bina pretended to be Leela, presenting Leela's admit card **as her own identity**" | el0 and el1 describe the same personation act from two angles; on facts already fixing that someone sat under Leela's card (control narrative context), el1 alone entails el0. |
| s_416_examhall_bina | omit_1 | el1 (pretended to be Leela) | F_el0 | "Bina **deceived the exam invigilator into believing she was Leela**" | el0's "deceived...into believing she was Leela" already restates the personation act el1 is meant to hide. |
| s_416_jobinterview_kiran | omit_0 / omit_1 | el0 / el1 | F_el1 / F_el0 | "presenting Milan's documents as his own identity" / "deceived the interview panel into believing he was Milan" | Same mutual-entailment pattern as examhall_bina. |
| s_416_courier_waseem | omit_0 / omit_1 | el0 / el1 | F_el1 / F_el0 | "Waseem pretended to be Farooq when signing" / "deceived the courier into believing he was Farooq" | Same pattern. |
| s_416_licensingexam_juhi | omit_0 / omit_1 | el0 / el1 | F_el1 / F_el0 | "presenting Isha's admit card as her own identity" / "deceived the exam staff into believing she was Isha" | Same pattern. |
| s_182a_neighbour_farhan | omit_0 | el0 (false statement) | F_el2 | "once **the true facts (that a permit existed)** were known" | States the true fact that el0's falsity is measured against, directly disclosing what Farhan lied about. |
| s_182a_taxraid_geeta | omit_0 | el0 | F_el2 | "once **the true facts (no hidden income)** were known" | Same pattern. |
| s_182a_licenserevoke_pankaj | omit_0 | el0 | F_el2 | "once **the true facts (that a valid certificate existed)** were known" | Same pattern. |
| s_182a_customsflag_wahid | omit_0 | el0 | F_el2 | "once **the true facts (no undeclared goods)** were known" | Same pattern. |
| s_bns85_inlaws_meera | omit_0 | el0 (relative-of-husband relationship) | F_narrative | "Meera and **her husband's brother, Suresh**" | The omission narrative names the exact relationship el0 is supposed to establish, instead of using a neutral placeholder. |
| s_bns85_motherinlaw_kavita | omit_0 | el0 | F_narrative | "Kavita and **her mother-in-law, Shanta**" | Same pattern. |
| s_bns85_brotherinlaw_ritu | omit_0 | el0 | F_narrative | "Ritu and **her husband's brother, Pawan**" | Same pattern. |
| s_bns85_husband_anjali | omit_0 | el0 (husband relationship) | F_narrative | "Anjali and **her husband, Rajeev**" | Same pattern. |
| s_46i_forgedletter_arjun | omit_0 | el0 (instigation via forged letter) | F_el1 | "acting with knowledge that **a claimed magistrate's order was forged**" | Confirms the forgery/falsity that is el0's core content, even though el0 itself is dropped. |
| s_46i_falsealarm_divya | omit_0 | el0 | F_el1 | "acting with knowledge that **the gas-connection claim was false**" | Same pattern. |
| s_46i_stagedinjury_ketan | omit_0 | el0 | F_el1 | "acting with knowledge that **the injury claim was staged**" | Same pattern. |
| s_46i_courtclerk_leena | omit_0 | el0 | F_el1 | "acting with knowledge that **the case file was still open**" | Same pattern. |

### Clean contract_ids (no leaks found)

- **ipc405_misappropriation**: 8 omission slices read; only 1 leaks (table above), the other 7 (both slices of warehouse_ravi, jeweller_kunal, sewingmachine_neha, plus maintfund_harish omit_1) are clean.
- **ipc405_use_or_disposal**: 12 omission slices read; only the 4 omit_0 slices leak (table above); all 4 omit_1 and all 4 omit_2 slices (8 total) are clean.
- **ipc405_wilfully_suffers**: 8 omission slices read; only 2 leak (table above); locker_vikram and safedeposit_irfan omit_0 (2), plus all 4 omit_1 slices (4), are clean (6 of 8).
- **ipc415_property**: 12 omission slices read; only 3 leak (table above); landdeal_ismail omit_0 plus all omit_1/omit_2 across all four scenarios (9 of 12) are clean.
- **ipc415_damaging_act**: 12 omission slices read; only the 4 omit_1 slices leak (table above); all 4 omit_0 and all 4 omit_2 (8 of 12) are clean.
- **ipc416**: 8 omission slices read; all 8 leak (table above) — 0 clean.
- **ipc182_misdirected_act**: 12 omission slices read; only the 4 omit_0 slices leak (table above); all 4 omit_1 and all 4 omit_2 (8 of 12) are clean.
- **ipc182_abuse_of_power**: 12 omission slices read; **none leak**.
- **bns69**: 8 omission slices read; **none leak**.
- **bns47**: 16 omission slices read; **none leak**.
- **bns85**: 8 omission slices read; only the 4 omit_0 slices leak (table above); all 4 omit_1 slices are clean.
- **bns46_instigation**: 8 omission slices read; only the 4 omit_0 slices leak (table above); all 4 omit_1 slices are clean.
- **bns46_conspiracy**: 12 omission slices read; **none leak**.
- **bns46_intentional_aid**: 8 omission slices read; **none leak**.

### Notable structural pattern, not a per-slice fact leak

`ipc416`'s el0 ("deceived X into believing the defendant was Y, thereby cheating X into permitting the act") and el1 ("pretended to be Y, presenting Y's documents as their own identity") describe the same personation act from two angles that mutually entail each other given the fixed control narrative. This makes both omission directions structurally leaky in all four scenarios, not just an isolated wording slip — worth a design-level look rather than a per-fact patch.
