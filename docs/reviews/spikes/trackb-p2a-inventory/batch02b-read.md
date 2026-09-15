# Batch-02b re-read (corrected regeneration)

## Counts

- Scenarios: 56 (14 contract_ids x 4 scenarios each) — **unchanged** from batch02-read.
- Rephrasing pairs found (check A): **0**
- Mis-filed scenarios found (check A): **0**
- Omission slices read (check B): **128**
- Leaking omission slices found (check B): **9** — 1 in `ipc405_misappropriation`, 4 in `ipc415_property`, 4 in `bns46_conspiracy`

## A. Distinctness (brief pass — count confirmation + spot judgment)

56 scenario_ids confirmed present, 4 per contract_id, across all 14 contract_ids (`ipc405_misappropriation`, `ipc405_use_or_disposal`, `ipc405_wilfully_suffers`, `ipc415_property`, `ipc415_damaging_act`, `ipc416`, `ipc182_misdirected_act`, `ipc182_abuse_of_power`, `bns69`, `bns47`, `bns85`, `bns46_instigation`, `bns46_conspiracy`, `bns46_intentional_aid`). Matches the prior read's count — unchanged.

| contract_id | scenarios | rephrasing pairs? | mis-filed? |
|---|---|---|---|
| ipc405_misappropriation | warehouse_ravi, jeweller_kunal, maintfund_harish, sewingmachine_neha | none — distinct parties/objects/settings (rice sold to third party, bangles pawned, funds spent on debts, sewing machine sold to collector) | none — all entrustment + conversion-for-own-use |
| ipc405_use_or_disposal | cold_storage_asha, artgallery_leela, grainsilo_baldev, vault_pooja | none — same abstract template (deposit + specific contract term + violation of that term) but parties/object/setting/means all differ (refrigeration/climate-control/segregation/individual-box) | none — all deposit + violation of an express contractual mode-of-dealing term |
| ipc405_wilfully_suffers | locker_vikram, safedeposit_irfan, cashcounter_alka, toolshed_prakash | none — templated (entrustee sees unauthorized taking and lets it proceed) but parties/objects/settings differ each time | none — all entrustee-with-dominion wilfully allowing another's misappropriation |
| ipc415_property | showroom_tara, carshowroom_yash, ringseller_devika, landdeal_ismail | none | none — all false representation inducing payment/delivery of property |
| ipc415_damaging_act | signature_omar, drugtest_kabir, invoice_tanya, tenancy_rustam | none | none — all false statement inducing a harmful act against the deceived/third party, distinct from property-delivery cheating |
| ipc416 | examhall_bina, jobinterview_kiran, courier_waseem, licensingexam_juhi | none | none — all personation to obtain a benefit/credit under another's identity |
| ipc182_misdirected_act | neighbour_farhan, taxraid_geeta, licenserevoke_pankaj, customsflag_wahid | none | none — all false info + intent to cause an act the officer ought not take |
| ipc182_abuse_of_power | customs_zara, policecomplaint_tariq, healthinspector_bilal, wildlifeofficer_nadia | none | none — all false info + known-likely official action + lawful power causing annoyance |
| bns69 | jobpromo_deepa, scholarship_naina, marriagepromise_shalini, suppressedidentity_reema | none | none — all deceit/false-promise inducement to intercourse; `deny` slices correctly test independent-rape-route facts (threat, intoxication, fear, minority) |
| bns47 | assault_salim, armsdeal_yasmin, investmentfraud_devansh, kidnapplot_farah | none — abetment route varies (instigation/instigation/conspiracy/intentional-aid) alongside varied objects | none |
| bns85 | inlaws_meera, motherinlaw_kavita, brotherinlaw_ritu, husband_anjali | none | none — all husband-or-relative-of-husband cruelty |
| bns46_instigation | forgedletter_arjun, falsealarm_divya, stagedinjury_ketan, courtclerk_leena | none | none |
| bns46_conspiracy | burglaryring_manoj, smugglingring_faizan, ballotstuffing_gulzar, extortionplot_hina | none | none |
| bns46_intentional_aid | getawaycar_sonal, disguisesupplier_rana, lookout_farida, toolsupply_waris | none | none |

## B. Entailment leaks per omission slice

### Leaking slices

| scenario_id | slice | dropped element | leaking fact id | quoted phrase | why |
|---|---|---|---|---|---|
| s_405m_maintfund_harish | omit_0 | el0 (entrustment: funds handed to Harish specifically for deposit) | F_el1 | "spent a portion of the funds on his own personal debts **instead of depositing them**" | "instead of depositing them" restates that Harish had been given the funds for the specific purpose of depositing — i.e. restates the dropped entrustment-for-deposit fact, not just generic entrustment. |
| s_415p_showroom_tara | omit_0 | el0 (false representation: watch falsely claimed as genuine brand) | F_el1 | "Whatever passed between them was **calculated fraudulently**, aimed at securing payment" | "fraudulently" means "by deceit" — it restates that a false representation occurred, which is exactly the dropped element. |
| s_415p_carshowroom_yash | omit_0 | el0 (false representation: car falsely claimed accident-free) | F_el1 | "This was **done fraudulently**, with intent to secure full market-price payment" | Same as above — "fraudulently" restates that deception took place. |
| s_415p_ringseller_devika | omit_0 | el0 (false representation: stone falsely claimed natural) | F_el1 | "This was **done fraudulently**, aimed at securing a premium price" | Same — restates deception. |
| s_415p_landdeal_ismail | omit_0 | el0 (false representation: land falsely claimed clear title) | F_el1 | "This was **done fraudulently**, aimed at inducing Radha to pay the deposit" | Same — restates deception. |
| s_46c_burglaryring_manoj | omit_0 | el0 (conspiracy: Manoj engaged with Hema/Suresh) | F_narrative | "This concerns a warehouse burglary **plan involving Manoj**, Hema, and Suresh" | "plan involving [name]" is a synonym for conspiracy and names the very person whose participation is the dropped fact — restates it in the narrative, which is retained in every slice. |
| s_46c_smugglingring_faizan | omit_0 | el0 (conspiracy: Faizan engaged with Rekha/Suraj) | F_narrative | "This concerns a smuggling **plan involving Faizan**, Rekha, and Suraj" | Same pattern. |
| s_46c_ballotstuffing_gulzar | omit_0 | el0 (conspiracy: Gulzar engaged with Kamal/Asif) | F_narrative | "This concerns a ballot-stuffing **plan involving Gulzar**, Kamal, and Asif" | Same pattern. |
| s_46c_extortionplot_hina | omit_0 | el0 (conspiracy: Hina engaged with Waqas/Imtiaz) | F_narrative | "This concerns an extortion **plan involving Hina**, Waqas, and Imtiaz" | Same pattern. |

Note on `bns47`'s parallel omit_0 narratives ("This concerns a kidnapping plot Farah and Zubin **discussed**", etc.): these use "discussed," which is neutral about whether Farah/Salim/etc. actually agreed to or participated in the scheme, unlike `bns46_conspiracy`'s "plan **involving** [name]" — so they were not flagged.

### Clean contract_ids (no leaks found)

| contract_id | omission slices read | result |
|---|---|---|
| ipc405_misappropriation | 8 (1 leaking — see above) | 7/8 clean |
| ipc405_use_or_disposal | 8 | clean — el2 (specific contract term) never leaked by el1 (the conduct alone), and vice versa |
| ipc405_wilfully_suffers | 8 | clean — "seeing X and deliberately letting it proceed" implies some oversight capacity but never lexically restates "entrusted"/"dominion"; treated as an inherent definitional dependency, not a wording leak |
| ipc415_property | 12 (4 leaking — see above) | 8/12 clean (omit_1, omit_2 all clean — el1/el2 correctly separate intent from completed payment/delivery) |
| ipc415_damaging_act | 8 | clean — el1 here uses neutral "intentionally induced ... something [they] would not otherwise have done" with no "false"/"fraudulently" wording, unlike ipc415_property |
| ipc416 | 8 | clean — retained facts describe the official's action or the personation act only in terms that don't name/confirm the other |
| ipc182_misdirected_act | 8 | clean |
| ipc182_abuse_of_power | 12 | clean |
| bns69 | 8 | clean — el0 (deceit) never names intercourse; el1 (intercourse) never names the deceit |
| bns47 | 16 | clean — el0 (abetment route) uses neutral "discussed" in narrative when dropped; India-presence/overseas-act/would-be-offence elements never leak each other |
| bns85 | 8 | clean |
| bns46_instigation | 8 | clean — narratives here never name the instigator (unlike bns46_conspiracy) |
| bns46_conspiracy | 8 (4 leaking — see above) | 4/8 clean (omit_1 slices clean) |
| bns46_intentional_aid | 8 | clean — narratives say "involving [aider] and [principal]" without asserting a joint plan, unlike bns46_conspiracy's "plan involving" |
