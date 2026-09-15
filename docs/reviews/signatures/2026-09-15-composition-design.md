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

## Ruling 4 revised — route precedence (reconciled with Track A's R2), 2026-09-15

Question: does a source-quoted defeater asserted on a route the reading does not rely on refute an offence made out cleanly via another route?

**No. My earlier "any inner denial refutes the whole composition" is withdrawn; Track A's R2 (route precedence) is confirmed with the legal reason.** A disjunctive offence is made out if any one limb is made out. A defeater is a denial of a *particular* limb's element (IPC 405's good-faith illustration negates "dishonestly" on the limb it is asserted against); it says nothing about a different limb whose elements are all present and undenied. So: limb A fully grounded and undenied plus a limb-B defeater asserted is an offence under limb A, and the limb-B defeater is reported as that route's refutation, not as a refutation of the composition. A defeater that the text attaches to every limb (IPC 405 A and B both carry "dishonestly") is simply present in each of those routes' `denials`, and refutes each of them individually.

Rule: (i) a route is *available* iff fully grounded and not refuted; (ii) if any route is available, the bridge is satisfied and refuted routes are reported in the `refuted` bucket with their contract_id, not as a composition refutation; (iii) in the partial case, the composition is refuted iff the selected (closest) route is refuted; (iv) the composition is refuted iff no route is available and every route the reading attempted is refuted, or the selected route is refuted. Route selection must therefore exclude refuted routes when an unrefuted grounded route exists. Vacuous today (no inner denials), binding for the spec.

## v3 @ `754ba38` — SIGNED, 2026-09-15 12:12 BST

Amendments A and B are in (`OmissionSource.bridge`; `inner : List (String × Contract)` with frozen ids); closest-route with declared-order tiebreak; §7 trace re-checked by me against the kit: all 14 readings match, a1/ba1 now `[bridge: …]` distinct from c5/b5 `[outer: …]`. §8 is the reconciled route-precedence rule with the placement rule; §9 exposes the selected route, per-route counts and the tiebreak version on the wire; §10 keeps compositions out of `--list-contracts`.

One precision item, binding for the implementation, not a redesign: §8 prose (iii) ("in the partial case the composition is refuted iff the selected route is refuted") is not what `Composition.Refuted` computes (it refutes iff every engaged route is refuted), and §6's selection never skips a refuted route. Ruling: **delete (iii); selection excludes refuted routes whenever an unrefuted engaged route exists** (`indexed` filtered by `routeDenied ic r == []` first, falling back to all engaged routes only when all are refuted); the composition is refuted iff an `outer.denials` claim is asserted, or no route is available and every engaged route is refuted. This matches the legal rule (a defeater negates its own limb only) and the code as drafted. Add the corresponding `#guard` once an inner Contract carries a denial.

**Content signature: SIGNED at `754ba38`** with that clarification carried into the implementation. Acceptance unchanged: the 14 readings as `#guard`s in `Seeds/CompositionKit.lean`, plus Track A's shape signature.
