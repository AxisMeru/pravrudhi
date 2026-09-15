# Composition operator design v2 — Track B content read

`prabhasa-nyaya` `assistant/trackA/composition-operator-design` @ `7b7f4f7`, `docs/composition-operator-design.md`. Read against the no-double-count rule and the kit's 14 readings (`docs/reviews/spikes/trackb-composition/test-kit.md`).

## Trace of the design over the kit

| reading | design result | kit expectation | verdict |
|---|---|---|---|
| c1 / b1 | union check licenses inner elements; one route fully grounded → `outerRest` only, bridge filtered → `[]` | satisfied once | ✓ |
| c2 / b2 | any route full → `[]`; bridge discharged once regardless of how many routes are full | once | ✓ |
| c3 | partial; limb A omits 1, limb B omits 2 → limb A → one inner omission `fraudulentOrDishonestInducement` | inner element, once | ✓ |
| b3 | partial; all three routes omit 1 → declared-order tiebreak → `instigates` | kit convention | ✓ (see ruling 1) |
| c5 / b5 | no inner claims → silence → `[outer: bridge]` | exactly one outer omission | ✓ |
| a1 / ba1 | no inner claims → silence → `[outer: bridge]` | never `[bridge]` as a plain omission: the reading asserted it | ✗ (amendment A) |
| a2 / ba2 | route full → `outerRest` = the outer's other missing element | exactly one outer element | ✓ |
| c4 / b4 | `Composition.refuted` policy; untestable today | refuted once | accepted as policy |

## Rulings

1. **Closest-route confirmed.** Fewest omissions, ties by declared order in `inner`. "All routes labelled" restates one gap N times and is refused (rule 1). Condition: the chosen route must be visible to the consumer through the provenance label (amendment B), so a tie-broken choice is never silent.
2. **Amendment A (required): distinguish "bridge asserted but ungrounded" from "silent".** a1/ba1 and c5/b5 are different findings: c5 is a reading that never claimed cheating; a1 claims it with no working. Under v2 both emit `outer: cheats` as an omission, and the wire consumer cannot tell a hallucinated conclusion from a non-conclusion. Fix: in the silence branch, if `r.claims.contains c.bridge` label the entry `bridge` (asserted, unsupported) else `outer` (not asserted). One line; the kit's a1/ba1 rows become `[bridge: cheats]` / `[bridge: abetsCommission]`.
3. **Amendment B (required): provenance labels are the frozen `contract_id` strings, not positional `inner[i]`.** Positions change on reorder and the wire consumer matches ids; `ProvenancedOmission.source` should carry `ipc415_property`, `bns46_instigation`, etc. Have `inner : List (String × Contract)` (id supplied by the caller from the frozen registry) or a parallel `innerIds`. Applies to `Composition.refuted`'s labels too.
4. **Rule 4 policy accepted**: a denial firing on any inner route refutes the whole composition; report it in a `refuted` bucket, compute `omitted` unchanged, and let the consumer treat refuted as dominant, as `Score.lean` already does for single Contracts. Untested until an inner Contract carries a denial; the kit pins the emptiness so the case activates automatically.
5. Union-of-axioms licensing (an unpursued route's elements are licensed) is accepted: route selection belongs to `omitted`, not `check`.
6. New wire tag for composed scoring, `REG` untouched: accepted. The composed omitted field carries `source:claim` pairs; the `bridge` label from amendment A is part of that format.

**Status: CLEARED for implementation conditional on amendments A and B**, with the kit's 14 readings (a1/ba1 updated per A) as the acceptance `#guard`s. Track A's shape gate applies alongside.
