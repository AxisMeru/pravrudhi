# Cert #1 full-IR gold — defect adjudication (Track B, 2026-09-16)
Branch assistant/trackA/p2b-eval-p-r-build @ 80873d2, gold.jsonl 405 records.
Method: mechanical layer (self) + full semantic read (9 parallel Sonnet readers, 45/batch, sibling-leak+sufficiency+consistency+coherence rubric) + Track B adjudication of every flag.

## CONFIRMED genuine defects — DROP or FIX (11)
- t_ipc405_misappropriation_9bc673e4_0000 — name-drift "Vikram Mehta"(F_narr/F_el0) vs "Vikram Mehra"(F_el1). FIX (1-token) + re-check.
- t_ipc415_damaging_act_4aed7df7_0045 — name-drift "Declan Harrow"(F_narr) vs "Declan Halloway"(F_el0-2). FIX + re-check.
- t_ipc405_misappropriation_4aed7df7_0011 — SIBLING-LEAK: Frost "utilized the blueprints to complete a paid consultation" = dishonest USE (ipc405_use_or_disposal discriminator), not misappropriation/conversion. DROP or relabel.
- t_ipc405_misappropriation_4aed7df7_0013 — SIBLING-LEAK: Vane "used the ignition key to sail the vessel for weekend leisure" (returnable) = USE, not conversion. DROP or relabel.
- t_ipc405_wilfully_suffers_4aed7df7_0032 — INCOHERENT ACTOR: entrustment is in Tamsin (F_el0) but the wilful-suffering is by Jorin (F_el1), who was never entrusted; CBT-by-suffering requires the entrusted person to be the sufferer. DROP.
- t_ipc416_4aed7df7_0017 — INCONSISTENCY: F_el0 has buyer Jovan "deliver three crates of reclaimed oak" he is purchasing (flow reversed); personation (F_el1) is present but F_el0 is muddled. FIX F_el0 or drop.
- t_ipc415_property_4aed7df7_0012 — INCONSISTENCY: deceived buyer Rostova "transferred ownership of the silver TO Solberg" (the deceiver) — reversed direction. DROP or fix.
- t_ipc182_misdirected_act_4aed7df7_0039 — INCONSISTENCY: F_el1 "Unit 4B vacant six months" but F_el2 "emergency eviction notice against the tenants of Unit 4B" — no tenants to evict. DROP or fix.
- t_ipc416_4aed7df7_0038 — SIBLING-LEAK (probable): "I am Eamon Croft's nephew and sole heir" = false kinship/status = plain 415 cheating, not personation (pretending to BE another identifiable person). Leans 415. DROP/relabel — Track A confirm 416 doctrine.
- t_bns47_4aed7df7_0043 — INSUFFICIENCY: urged conduct is only "a specific act", no named underlying offence, so F_el3's "would be an offence in India if committed in India" is unverifiable. FIX (name the offence) or drop.
- t_bns85_4aed7df7_0037 — INSUFFICIENCY: F_el0 establishes only that Tavish is the husband; the actual perpetrator Arjun Rane's relative-of-husband status (the discriminating element) is never established. FIX (add relationship fact) or drop.

## NEEDS Track A/d0 adjudication (systematic / contract-semantics; NOT unilaterally dropped)
- ipc415_property DELIVERY cluster — 0006, 0009, 0033, 0043, 0044 (5): each F_el2 shows the deceived BUYER taking possession / signing ownership TO HIMSELF, with no fact of the victim parting with property (money). Contract-semantics question: does ipc415_property's "delivery of property" element accept the deceived buyer's RECEIPT/ownership-acquisition, or require the victim to PART WITH property? The checker passed all; a strict reading finds the induced-delivery element under-established. Track A owns the contract semantics; on the answer these 5 either stand or drop/fix.
- bns46_conspiracy object-offence characterization — 0010 (disable alarm = "criminal trespass" w/o entry), 0013 (intercept shipment before possession = "criminal misappropriation" — really theft), 0018 (disable cameras = "criminal trespass"): the object-offence LABELS are legally loose, but the conspiracy structure (agreement + pursuant act to commit AN offence) holds. Minor; likely acceptable. Track A confirm whether object-offence precision is required for the conspiracy gold.

## CLEARED — reader over-flag, Track B rules CLEAN (keep)
- t_ipc182_abuse_of_power_4aed7df7_0015 and _0032: the false report about vacant 4B is the deliberate PRETEXT; F_el1/F_el2 explicitly establish the purpose (deploy the servant's statutory power to harass 4A). Coherent abuse-of-power. NOT defects.

## Leak / metric gap
- t_bns46_conspiracy_4aed7df7_0006 — my independent masked-shingle-jaccard(n=3, entity-masked) = 0.308 vs t_bns46_conspiracy_41fc1769_0019, OVER the within-batch template-dup threshold (_TEMPLATE_DUP_MASKED_JACCARD_REJECT=0.30). The cross-batch T-eval leak-check used only _JACCARD_REJECT=0.40 content-word bag + character-signature conjunction, which does NOT apply the 0.30 masked-shingle template metric cross-batch. RECOMMEND d0 re-run the cross-batch check with the 0.30 masked-shingle metric (same as within-batch) for exact values; drop 0006 if confirmed ≥0.30.
