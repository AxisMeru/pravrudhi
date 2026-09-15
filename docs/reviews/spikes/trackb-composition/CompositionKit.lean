/- Track B spike (spikeA-composition, 2026-09-15): content test kit for the forthcoming
   cross-Contract composition operator, for the two composition pairs that exist on main at
   db5c5cd -- `IPC416.cheats` -> IPC 415's two limb Contracts, and `BNS47.abetsCommission` ->
   BNS 46's three route Contracts.

   NOT a proposed operator and not an authored legal Contract. Every `Contract` and `Entity`
   referenced below is the one already on main; this file adds only named `Reading` values and
   `#guard`s. The `#guard`s fall into two kinds, and the distinction is the point of the file:

   * TODAY guards -- what today's `Adequacy.lean` API already decides about each reading. Several
     of these pin behaviour that is WRONG under the reviewer's no-double-counting rule (marked
     `DEFECT`); they are asserted anyway, so that landing the operator makes them fail loudly
     rather than silently changing meaning.
   * `TODO(composition)` comments -- the expectation the operator must meet, which today's API
     cannot express at all.

   The reviewer's five content rules, for both pairs:
     1. inner satisfied on ANY ONE limb  => outer element satisfied exactly once
     2. inner satisfied on BOTH/ALL limbs => outer element satisfied exactly once
     3. omission inside the inner Contract => reported as the INNER element's omission, never as
        a bare missing outer element
     4. a denial inside the inner Contract refutes through to the outer verdict exactly once
     5. silence on the inner Contract entirely => exactly one omission, of the OUTER element

   Placement: intended for `lean/PrabhasaNyaya/Seeds/CompositionKit.lean` with a matching import
   line in `lean/PrabhasaNyaya.lean`. NOT BUILT -- see `findings.md` section 0; the spike seat's
   sandbox refused `git worktree`, `cp -r`, `rsync`, `lean` and `lake`, so `lake build` was never
   run against this file. Treat every `#guard` below as UNVERIFIED until someone builds it. -/
import PrabhasaNyaya.Adequacy
import PrabhasaNyaya.Seeds.IPC415
import PrabhasaNyaya.Seeds.IPC416
import PrabhasaNyaya.Seeds.BNS46
import PrabhasaNyaya.Seeds.BNS47
import PrabhasaNyaya.Seeds.BNS69

namespace PrabhasaNyaya.Seeds.CompositionKit

open PrabhasaNyaya

/-! ## 0. The locus every Contract in both pairs happens to share

Each of the five Contracts declares its OWN `conduct : Entity`, and all five happen to be the
same value, `⟨"the conduct in the facts", Sorta.act⟩` (`IPC415.lean:98`, `IPC416.lean:73`,
`BNS46.lean:176`, `BNS47.lean:96`, `BNS69.lean:105`). `Contract.omitted` matches by
`List.contains`, i.e. structural equality, so cross-Contract matching works today ONLY because of
that coincidence -- nothing in `Adequacy.lean` requires it and no type error would appear if one
file renamed its conduct entity. Asserted here so a rename breaks a guard instead of silently
breaking composition. See `findings.md` F6. -/

#guard IPC415.conduct == IPC416.conduct
#guard BNS46.conduct == BNS47.conduct
#guard IPC415.conduct == BNS47.conduct

def conduct : Entity := IPC416.conduct

/-- One `satisfies` claim at the shared locus -- the only claim shape either pair uses. -/
def sat (e : Entity) : Claim := Claim.satisfies (Node.ent conduct) e

/-! ## 1. IPC 416 (`cheats` + `personation`) -> IPC 415 (limb A property / limb B damaging act)

Outer: `IPC416.contract`, required `[cheats, personation]` (`IPC416.lean:82-84`).
Inner family: `IPC415.contractPropertyInducement` (limb A: deception, fraudulent-or-dishonest
inducement, delivery-or-consent-to-retain) and `IPC415.contractDamagingInducement` (limb B:
deception, intentional inducement to act/omit, damage-or-harm). `IPC416.cheats` IS `IPC415.cheats`
(`IPC416.lean:66`), the section's conclusion, and is deliberately in NEITHER inner Contract's
`axioms`/`required` (`IPC415.lean:100-107`). -/

/-- **c1 / rule 1.** A pretends to be a Civil Service officer, on that footing dishonestly induces
    a shopkeeper to deliver goods on credit. Inner limb A fully stated; limb B not mentioned. -/
def c1_innerLimbAOnly : Reading :=
  { claims :=
      [ sat IPC416.cheats
      , sat IPC416.personation
      , sat IPC415.deception
      , sat IPC415.fraudulentOrDishonestInducement
      , sat IPC415.deliveryOrConsentToRetain ] }

-- TODAY: nothing omitted on either side; limb A is complete and both outer elements are stated.
#guard IPC416.contract.omitted c1_innerLimbAOnly == []
#guard IPC415.contractPropertyInducement.omitted c1_innerLimbAOnly == []

-- TODAY, DEFECT (findings.md F1): the HONEST composed reading -- the one that actually shows its
-- work on s.415 -- is `unlicensed` on BOTH Contracts. `IPC416.contract.axioms` does not contain
-- s.415's element claims, and `IPC415.contractPropertyInducement.axioms` does not contain `cheats`
-- or `personation`. Under today's API there is no single Contract a composed reading can pass.
#guard !IPC416.contract.check c1_innerLimbAOnly
#guard IPC416.contract.unlicensed c1_innerLimbAOnly ==
  [ sat IPC415.deception
  , sat IPC415.fraudulentOrDishonestInducement
  , sat IPC415.deliveryOrConsentToRetain ]
#guard !IPC415.contractPropertyInducement.check c1_innerLimbAOnly

-- TODO(composition): composed verdict SATISFIED; omitted `[]`; Refuted false. `cheats` is
-- discharged by limb A exactly once -- limb B's three elements must NOT appear as omissions.

/-- **c2 / rule 2.** Same personation, and the same deception both moves property AND induces the
    victim into a separate damaging act. Inner limbs A and B BOTH fully stated. -/
def c2_innerBothLimbs : Reading :=
  { claims :=
      [ sat IPC416.cheats
      , sat IPC416.personation
      , sat IPC415.deception
      , sat IPC415.fraudulentOrDishonestInducement
      , sat IPC415.deliveryOrConsentToRetain
      , sat IPC415.intentionalInducementToActOrOmit
      , sat IPC415.damageOrHarm ] }

#guard IPC416.contract.omitted c2_innerBothLimbs == []
#guard IPC415.contractPropertyInducement.omitted c2_innerBothLimbs == []
#guard IPC415.contractDamagingInducement.omitted c2_innerBothLimbs == []

-- TODO(composition): composed verdict SATISFIED; omitted `[]`; `cheats` discharged ONCE, not
-- once per satisfied limb. A composed trace that names s.415 twice is a rule-2 failure even
-- though the verdict is right.

/-- **c3 / rule 3.** Illustration (g)'s timing carve-out, under a false identity: A personates,
    deceives Z about the indigo, obtains the money, and property does move -- but at the time of
    inducement A genuinely intended to deliver. Limb A short of its mental element; limb B never
    engaged. -/
def c3_innerLimbAMissingInducement : Reading :=
  { claims :=
      [ sat IPC416.cheats
      , sat IPC416.personation
      , sat IPC415.deception
      , sat IPC415.deliveryOrConsentToRetain ] }

-- TODAY, DEFECT (findings.md F2): the outer Contract reports NOTHING missing. The gap is real and
-- is entirely invisible to `IPC416.contract`.
#guard IPC416.contract.omitted c3_innerLimbAMissingInducement == []

-- TODAY: the inner Contracts do see it -- but they disagree about how big it is. Limb A is one
-- element short, limb B is two. A composed operator must pick a limb; there is no rule in
-- `Adequacy.lean` today that picks one (findings.md F3).
#guard IPC415.contractPropertyInducement.omitted c3_innerLimbAMissingInducement ==
  [ sat IPC415.fraudulentOrDishonestInducement ]
#guard IPC415.contractDamagingInducement.omitted c3_innerLimbAMissingInducement ==
  [ sat IPC415.intentionalInducementToActOrOmit, sat IPC415.damageOrHarm ]
-- A naive concat over the limb family reports three omissions for one gap -- rule-3 and rule-1
-- double-counting in the same list.
#guard (IPC415.contractPropertyInducement.omitted c3_innerLimbAMissingInducement
        ++ IPC415.contractDamagingInducement.omitted c3_innerLimbAMissingInducement).length == 3

-- TODO(composition): composed verdict NOT SATISFIED; omitted EXACTLY
-- `[sat IPC415.fraudulentOrDishonestInducement]` -- the inner element, named as s.415's, and NOT
-- `[sat IPC416.cheats]`. Kit convention for the limb choice: the limb with the FEWEST omissions
-- (ties resolved by registry order); the operator must state whichever rule it adopts.

/- **c4 / rule 4.** A denial inside the inner Contract, refuting through to the outer verdict.
    THIS CASE CANNOT BE WRITTEN for this pair on today's main: neither IPC 415 limb carries any
    `denials` (`IPC415.lean:117,138`), and neither does IPC 416 (`IPC416.lean:81`). The guards
    below pin that emptiness so the case becomes writable the moment any of the three gains a
    denial. See findings.md F4. -/
#guard IPC415.contractPropertyInducement.denials == []
#guard IPC415.contractDamagingInducement.denials == []
#guard IPC416.contract.denials == []

/-- **c5 / rule 5.** Costume-party name badge, nothing more: personation-like conduct, and the
    reading says nothing at all about cheating or about any s.415 element. -/
def c5_silentOnInner : Reading := { claims := [ sat IPC416.personation ] }

-- TODAY, CORRECT: exactly one omission, of the outer element.
#guard IPC416.contract.check c5_silentOnInner
#guard IPC416.contract.omitted c5_silentOnInner == [ sat IPC416.cheats ]

-- What must NOT be added to that one omission once the operator lands: descending into limb A
-- unconditionally would report three further omissions for a reading that never invoked s.415.
#guard (IPC415.contractPropertyInducement.omitted c5_silentOnInner).length == 3

-- TODO(composition): composed verdict NOT SATISFIED; omitted EXACTLY `[sat IPC416.cheats]`,
-- length 1. The operator must short-circuit: silence on the inner Contract is one outer omission,
-- not an outer omission plus the inner Contract's whole required list.

/-- **a1 / adversarial.** The outer element asserted flat, with no inner claims of any kind --
    "the accused cheated by personation", full stop. Identical in claims to `IPC416.bothStated`. -/
def a1_bareOuterAssertion : Reading :=
  { claims := [ sat IPC416.cheats, sat IPC416.personation ] }

-- TODAY, DEFECT (findings.md F1): fully licensed and complete. `Claim.satisfies conduct cheats` is
-- itself an axiom of `IPC416.contract` (`IPC416.lean:79`), so a bare assertion of the outer
-- element is not merely unflagged -- it is affirmatively licensed. This is the case the operator
-- exists for, and it is also why the operator cannot simply drop `cheats` from `axioms`: doing so
-- would make an HONEST reading's `cheats` claim unlicensed too.
#guard IPC416.contract.check a1_bareOuterAssertion
#guard IPC416.contract.omitted a1_bareOuterAssertion == []

-- TODO(composition): composed verdict NOT SATISFIED. Omitted must be the cheapest inner limb's
-- own required list (limb A's three elements under the kit convention) -- or a single, distinctly
-- typed "outer element asserted without inner support" finding, if the operator prefers that. It
-- must NOT be `[]`, and it must NOT be `[sat IPC416.cheats]` (the reading did assert `cheats`;
-- reporting it as omitted would be false).

/-- **a2 / adversarial.** Inner limb A fully satisfied, `cheats` properly asserted -- but the
    OUTER Contract's own other element is missing: an ordinary cheat told under the accused's true
    identity. The gap is outer, not inner. -/
def a2_innerLimbAOuterOtherMissing : Reading :=
  { claims :=
      [ sat IPC416.cheats
      , sat IPC415.deception
      , sat IPC415.fraudulentOrDishonestInducement
      , sat IPC415.deliveryOrConsentToRetain ] }

-- TODAY, CORRECT on the omission, and the inner side is clean.
#guard IPC416.contract.omitted a2_innerLimbAOuterOtherMissing == [ sat IPC416.personation ]
#guard IPC415.contractPropertyInducement.omitted a2_innerLimbAOuterOtherMissing == []

-- TODO(composition): composed verdict NOT SATISFIED; omitted EXACTLY
-- `[sat IPC416.personation]`, length 1. Satisfying the inner Contract must not mask, duplicate, or
-- be duplicated by, the outer Contract's own remaining elements.

/-! ## 2. BNS 47 (`abetsCommission` + three territorial elements) -> BNS 46 (three s.45 routes)

Outer: `BNS47.contract`, required `[abetsCommission, conductInIndia, actAbroad,
hypotheticalOffenceIfInIndia]` (`BNS47.lean:106-110`).
Inner family: `BNS46.contractInstigation`, `BNS46.contractConspiracy`,
`BNS46.contractIntentionalAid` -- s.45's three routes, each requiring its own route element PLUS
the shared `abettedIsOffenceOrWouldBe` (`BNS46.lean:186-188, 210-212, 230-232`). That shared
element being required by all three routes is the concrete arithmetic behind rules 1 and 2 for
this pair; see b3 below. -/

/-- **b1 / rule 1.** The section's own illustration: A, in India, instigates B (a foreigner) to
    commit a murder abroad. Inner instigation route fully stated; conspiracy and aid not
    mentioned. -/
def b1_innerInstigationOnly : Reading :=
  { claims :=
      [ sat BNS47.abetsCommission
      , sat BNS47.conductInIndia
      , sat BNS47.actAbroad
      , sat BNS47.hypotheticalOffenceIfInIndia
      , sat BNS46.instigates
      , sat BNS46.abettedIsOffenceOrWouldBe ] }

#guard BNS47.contract.omitted b1_innerInstigationOnly == []
#guard BNS46.contractInstigation.omitted b1_innerInstigationOnly == []

-- TODAY, DEFECT (findings.md F1), the same cross-licensing failure as c1 on the other pair.
#guard !BNS47.contract.check b1_innerInstigationOnly
#guard BNS47.contract.unlicensed b1_innerInstigationOnly ==
  [ sat BNS46.instigates, sat BNS46.abettedIsOffenceOrWouldBe ]
#guard !BNS46.contractInstigation.check b1_innerInstigationOnly

-- TODO(composition): composed verdict SATISFIED; omitted `[]`; `abetsCommission` discharged by the
-- instigation route exactly once. The conspiracy route's pursuance-act element and the aid route's
-- own element must NOT appear as omissions.

/-- **b2 / rule 2.** A instigates B, has also engaged in a conspiracy under which an act took
    place in pursuance of it, and separately supplied intentional aid -- all three s.45 routes
    made out on the same facts. -/
def b2_innerAllThreeRoutes : Reading :=
  { claims :=
      [ sat BNS47.abetsCommission
      , sat BNS47.conductInIndia
      , sat BNS47.actAbroad
      , sat BNS47.hypotheticalOffenceIfInIndia
      , sat BNS46.instigates
      , sat BNS46.conspiresWithPursuanceAct
      , sat BNS46.intentionallyAids
      , sat BNS46.abettedIsOffenceOrWouldBe ] }

#guard BNS47.contract.omitted b2_innerAllThreeRoutes == []
#guard BNS46.contractInstigation.omitted b2_innerAllThreeRoutes == []
#guard BNS46.contractConspiracy.omitted b2_innerAllThreeRoutes == []
#guard BNS46.contractIntentionalAid.omitted b2_innerAllThreeRoutes == []

-- TODO(composition): composed verdict SATISFIED; omitted `[]`; `abetsCommission` discharged ONCE,
-- not three times. A composed trace naming s.46 three times is a rule-2 failure.

/-- **b3 / rule 3.** A, in India, merely relays to B abroad that Z will be travelling alone, with
    no urging, no conspiracy and no facilitating act -- but the reading does state that what B
    would do is an offence. Every inner route is short by exactly its own route element. -/
def b3_innerMissingRouteElement : Reading :=
  { claims :=
      [ sat BNS47.abetsCommission
      , sat BNS47.conductInIndia
      , sat BNS47.actAbroad
      , sat BNS47.hypotheticalOffenceIfInIndia
      , sat BNS46.abettedIsOffenceOrWouldBe ] }

-- TODAY, DEFECT (findings.md F2): the outer Contract reports nothing missing.
#guard BNS47.contract.omitted b3_innerMissingRouteElement == []

-- TODAY: each inner route reports exactly one omission -- ITS OWN. Three routes, one real gap.
#guard BNS46.contractInstigation.omitted b3_innerMissingRouteElement == [ sat BNS46.instigates ]
#guard BNS46.contractConspiracy.omitted b3_innerMissingRouteElement ==
  [ sat BNS46.conspiresWithPursuanceAct ]
#guard BNS46.contractIntentionalAid.omitted b3_innerMissingRouteElement ==
  [ sat BNS46.intentionallyAids ]
-- The naive concat: three omissions for one gap, with all three routes tied at cost 1, so
-- "fewest omissions" does not break the tie here (findings.md F3).
#guard (BNS46.contractInstigation.omitted b3_innerMissingRouteElement
        ++ BNS46.contractConspiracy.omitted b3_innerMissingRouteElement
        ++ BNS46.contractIntentionalAid.omitted b3_innerMissingRouteElement).length == 3

-- TODO(composition): composed verdict NOT SATISFIED; omitted must name s.46/s.45's own gap ONCE,
-- not once per route, and not as `[sat BNS47.abetsCommission]`. Kit convention: the
-- registry-first route (`bns46_instigation`), so omitted == `[sat BNS46.instigates]`. The operator
-- must state its tie-break rule explicitly; "report all tied routes" is a rule-1 violation.

/- **b4 / rule 4.** Not writable for this pair either: no BNS 46 route and not BNS 47 carries any
    `denials` (`BNS46.lean:185,209,229`; `BNS47.lean:105`). Pinned so the case becomes writable the
    moment one does. See findings.md F4. -/
#guard BNS46.contractInstigation.denials == []
#guard BNS46.contractConspiracy.denials == []
#guard BNS46.contractIntentionalAid.denials == []
#guard BNS47.contract.denials == []

/- Rule 4's reference behaviour, from the ONE denial-bearing Contract in the T5b registry
    (`BNS69.lean:112`): an affirmative assertion of the carve-out refutes, and refutation is a
    single Bool, not a list -- so "exactly once" is free at the inner level. What the operator must
    add is propagation, and a trace that names the refuting claim once. -/
#guard !BNS69.contract.check BNS69.amountsToRapeAsserted
#guard BNS69.contract.Refuted BNS69.amountsToRapeAsserted

-- TODO(composition): given a denial-bearing inner Contract, a reading asserting that denied claim
-- must make the COMPOSED verdict Refuted, with the refuting claim reported once, attributed to the
-- inner Contract, and NOT also reported as an outer omission.

/-- **b5 / rule 5.** A, in India, discusses with B the possibility of an act abroad that would be
    an offence here -- the reading says nothing whatever about instigation, conspiracy or aid.
    Same claims as `BNS47.missingAbetment`. -/
def b5_silentOnInner : Reading :=
  { claims :=
      [ sat BNS47.conductInIndia
      , sat BNS47.actAbroad
      , sat BNS47.hypotheticalOffenceIfInIndia ] }

-- TODAY, CORRECT: exactly one omission, of the outer element.
#guard BNS47.contract.check b5_silentOnInner
#guard BNS47.contract.omitted b5_silentOnInner == [ sat BNS47.abetsCommission ]

-- What must NOT be appended: unconditional descent adds two per route, six across the family.
#guard (BNS46.contractInstigation.omitted b5_silentOnInner).length == 2
#guard (BNS46.contractInstigation.omitted b5_silentOnInner
        ++ BNS46.contractConspiracy.omitted b5_silentOnInner
        ++ BNS46.contractIntentionalAid.omitted b5_silentOnInner).length == 6

-- TODO(composition): composed verdict NOT SATISFIED; omitted EXACTLY
-- `[sat BNS47.abetsCommission]`, length 1.

/-- **ba1 / adversarial.** `abetsCommission` asserted flat, no inner claims at all. Same claims as
    `BNS47.allStated`. -/
def ba1_bareOuterAssertion : Reading :=
  { claims :=
      [ sat BNS47.abetsCommission
      , sat BNS47.conductInIndia
      , sat BNS47.actAbroad
      , sat BNS47.hypotheticalOffenceIfInIndia ] }

-- TODAY, DEFECT (findings.md F1): licensed and complete on the bare assertion.
#guard BNS47.contract.check ba1_bareOuterAssertion
#guard BNS47.contract.omitted ba1_bareOuterAssertion == []

-- TODO(composition): composed verdict NOT SATISFIED; omitted must be the chosen route's own
-- required list (two elements) or a single typed "outer element asserted without inner support"
-- finding -- never `[]`, and never `[sat BNS47.abetsCommission]`.

/-- **ba2 / adversarial.** Inner instigation route satisfied and `abetsCommission` asserted, but
    the OUTER Contract's own locus element is missing: A does the instigating from outside India
    (BNS 48's shape, not BNS 47's). -/
def ba2_innerRouteOuterOtherMissing : Reading :=
  { claims :=
      [ sat BNS47.abetsCommission
      , sat BNS47.actAbroad
      , sat BNS47.hypotheticalOffenceIfInIndia
      , sat BNS46.instigates
      , sat BNS46.abettedIsOffenceOrWouldBe ] }

#guard BNS47.contract.omitted ba2_innerRouteOuterOtherMissing == [ sat BNS47.conductInIndia ]
#guard BNS46.contractInstigation.omitted ba2_innerRouteOuterOtherMissing == []

-- TODO(composition): composed verdict NOT SATISFIED; omitted EXACTLY
-- `[sat BNS47.conductInIndia]`, length 1.

end PrabhasaNyaya.Seeds.CompositionKit
