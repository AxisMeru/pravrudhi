# Composition test kit — content readings

Spike: `spikeA-composition`, 2026-09-15. Source read at `prabhasa-nyaya` `origin/main` = `db5c5cd`.
Companion files: `CompositionKit.lean` (the same readings in Lean), `findings.md` (API blockers).

**Scope.** Two composition pairs exist on main:

| Pair | Outer Contract | Outer element referencing the inner | Inner family |
|---|---|---|---|
| A | `ipc416` | `IPC416.cheats` (= `IPC415.cheats`) | `ipc415_property` (limb A), `ipc415_damaging_act` (limb B) |
| B | `bns47` | `BNS47.abetsCommission` | `bns46_instigation`, `bns46_conspiracy`, `bns46_intentional_aid` (unregistered) |

Both inner references are *families* of Contracts, not single Contracts — s.415 splits into two
limbs and s.45/46 into three routes. The composition operator is therefore a disjunction over a
family, which is where all the double-counting risk lives.

**Kit conventions** (the operator must state its own; these are what the expectations below
assume):
- *Limb selection*: when no limb is fully satisfied, report the omissions of the limb with the
  fewest; ties broken by registry order. (See `findings.md` F3 — `Adequacy.lean` supplies no rule.)
- *Attribution*: an omitted element is reported as the element of the Contract whose `required`
  list names it, not re-labelled as the outer element.
- *Counting*: "satisfied once" means the composed trace names the inner discharge one time, whether
  one limb or every limb is made out.

Claims are abbreviated; every one is `Claim.satisfies (Node.ent conduct) <element>` at the single
shared `conduct` entity. `Refuted` is `false` for every reading in the kit — see rule 4.

---

## Pair A — `IPC416.cheats` → IPC 415

Outer required: `cheats`, `personation`.
Limb A required: `deception`, `fraudulentOrDishonestInducement`, `deliveryOrConsentToRetain`.
Limb B required: `deception`, `intentionalInducementToActOrOmit`, `damageOrHarm`.

| # | Rule | Facts (one line) | Claims | Expected outer verdict | Expected omitted | Refuted |
|---|---|---|---|---|---|---|
| c1 | 1 | A pretends to be a Civil Service officer and on that footing dishonestly induces a shopkeeper to deliver goods on credit. | `cheats`, `personation`, `deception`, `fraudulentOrDishonestInducement`, `deliveryOrConsentToRetain` | **Satisfied** — `cheats` discharged by limb A, once | `[]` (limb B's elements must not appear) | false |
| c2 | 2 | Same personation; the same deception both moves property *and* induces a separate damaging act. | c1's claims + `intentionalInducementToActOrOmit`, `damageOrHarm` | **Satisfied** — `cheats` discharged **once**, not once per limb | `[]` | false |
| c3 | 3 | Illustration (g) under a false identity: A personates, deceives Z about the indigo, money changes hands, but A genuinely intended to deliver at the time of inducement. | `cheats`, `personation`, `deception`, `deliveryOrConsentToRetain` | **Not satisfied** | `[IPC415.fraudulentOrDishonestInducement]` — the **inner** element, once; **not** `[IPC416.cheats]`, and not limb B's two | false |
| c4 | 4 | *Unwritable for this pair on main* — neither IPC 415 limb nor IPC 416 carries any `denials`. See `findings.md` F4; the reference behaviour is `BNS69.amountsToRapeAsserted`. | — | (Refuted, once) | `[]` | (true) |
| c5 | 5 | Costume-party name badge; the reading says nothing about cheating or any s.415 element. | `personation` | **Not satisfied** | `[IPC416.cheats]` — exactly one, the **outer** element; limb A's three must not be appended | false |
| a1 | adversarial — bare outer assertion | "The accused cheated by personation", full stop; no s.415 working shown. | `cheats`, `personation` | **Not satisfied** | Limb A's three (`deception`, `fraudulentOrDishonestInducement`, `deliveryOrConsentToRetain`), or one typed *outer-element-asserted-without-inner-support* finding. Never `[]`; never `[cheats]` (the reading did assert it) | false |
| a2 | adversarial — inner ok, outer's other element missing | Limb A fully made out, but told under the accused's true identity — ordinary cheating. | `cheats`, `deception`, `fraudulentOrDishonestInducement`, `deliveryOrConsentToRetain` | **Not satisfied** | `[IPC416.personation]` — exactly one, the **outer** element | false |

**Today's behaviour on these** (`CompositionKit.lean` pins all of it):
- c1, b-analogues: the honest composed reading fails `check` on *both* Contracts — `IPC416.contract`
  does not license s.415's element claims and vice versa (`findings.md` F1).
- c3: `IPC416.contract.omitted c3 == []` — the gap is invisible to the outer Contract.
- a1: `IPC416.contract.check == true` and `omitted == []` — the bare assertion is affirmatively
  licensed, because `satisfies conduct cheats` is an axiom of `IPC416.contract` (`IPC416.lean:79`).
- c5 and a2 are already correct today and must stay correct after composition lands.

---

## Pair B — `BNS47.abetsCommission` → BNS 46

Outer required: `abetsCommission`, `conductInIndia`, `actAbroad`, `hypotheticalOffenceIfInIndia`.
Instigation route: `instigates`, `abettedIsOffenceOrWouldBe`.
Conspiracy route: `conspiresWithPursuanceAct`, `abettedIsOffenceOrWouldBe`.
Aid route: `intentionallyAids`, `abettedIsOffenceOrWouldBe`.

Note `abettedIsOffenceOrWouldBe` is required by **all three** routes (`BNS46.lean:188, 212, 232`) —
any operator that unions route `required` lists reports that one element three times.

| # | Rule | Facts (one line) | Claims | Expected outer verdict | Expected omitted | Refuted |
|---|---|---|---|---|---|---|
| b1 | 1 | The section's own illustration: A, in India, instigates B (a foreigner) to commit a murder abroad. | `abetsCommission`, `conductInIndia`, `actAbroad`, `hypotheticalOffenceIfInIndia`, `instigates`, `abettedIsOffenceOrWouldBe` | **Satisfied** — `abetsCommission` discharged by the instigation route, once | `[]` (conspiracy/aid route elements must not appear) | false |
| b2 | 2 | A instigates B, has also engaged in a conspiracy under which a pursuance act took place, and separately supplied intentional aid. | b1's claims + `conspiresWithPursuanceAct`, `intentionallyAids` | **Satisfied** — `abetsCommission` discharged **once**, not three times | `[]` | false |
| b3 | 3 | A, in India, merely relays that Z will be travelling alone — no urging, no conspiracy, no facilitating act — but what B would do abroad is stated to be an offence. | `abetsCommission`, `conductInIndia`, `actAbroad`, `hypotheticalOffenceIfInIndia`, `abettedIsOffenceOrWouldBe` | **Not satisfied** | One inner route element, once — kit convention `[BNS46.instigates]` (registry-first, since all three routes tie at one omission each); **not** `[BNS47.abetsCommission]`, and not all three routes' elements | false |
| b4 | 4 | *Unwritable for this pair on main* — no BNS 46 route and not BNS 47 carries any `denials`. See `findings.md` F4. | — | (Refuted, once) | `[]` | (true) |
| b5 | 5 | A, in India, discusses with B the possibility of an act abroad that would be an offence here; nothing about instigation, conspiracy or aid. | `conductInIndia`, `actAbroad`, `hypotheticalOffenceIfInIndia` | **Not satisfied** | `[BNS47.abetsCommission]` — exactly one, the **outer** element; the routes' six elements must not be appended | false |
| ba1 | adversarial — bare outer assertion | "A abetted the commission of the act", with no s.45/46 working shown. | `abetsCommission`, `conductInIndia`, `actAbroad`, `hypotheticalOffenceIfInIndia` | **Not satisfied** | The chosen route's two elements, or one typed *outer-element-asserted-without-inner-support* finding. Never `[]`; never `[abetsCommission]` | false |
| ba2 | adversarial — inner ok, outer's other element missing | A satisfies the instigation route but does the instigating from outside India (BNS 48's shape). | `abetsCommission`, `actAbroad`, `hypotheticalOffenceIfInIndia`, `instigates`, `abettedIsOffenceOrWouldBe` | **Not satisfied** | `[BNS47.conductInIndia]` — exactly one, the **outer** element | false |

**Today's behaviour**: b1 fails `check` on both Contracts (F1); b3 gives
`BNS47.contract.omitted == []` (F2) while the three routes report three omissions for one gap
(F3); ba1 gives `check == true`, `omitted == []` (F1). b5 and ba2 are already correct.

---

## Coverage map

| Reviewer rule | Pair A | Pair B | Writable against today's API? |
|---|---|---|---|
| 1 — one limb satisfies outer once | c1 | b1 | Partly — the non-composed halves only |
| 2 — both/all limbs still once | c2 | b2 | Partly |
| 3 — inner omission reported as inner | c3 | b3 | Partly — inner half yes, attribution no |
| 4 — inner denial refutes through once | c4 | b4 | **No** — no denials exist on either inner family |
| 5 — silence on inner = one outer omission | c5 | b5 | **Yes**, fully (already correct today) |
| adv. — bare outer assertion | a1 | ba1 | Yes, as a pinned defect |
| adv. — inner ok, outer's other element missing | a2 | ba2 | **Yes**, fully (already correct today) |
