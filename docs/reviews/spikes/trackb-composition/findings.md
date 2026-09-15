# Findings — `Adequacy.lean` semantics vs. the five no-double-counting rules

Spike `spikeA-composition`, 2026-09-15. All line references are `prabhasa-nyaya` at `db5c5cd`.

## 0. Build not verified — read this first

`CompositionKit.lean` was **not built**. This seat's sandbox refused, non-interactively,
`git worktree add`, `cp -r`, `rsync`, a Python `copytree`, and every `lean`/`lake` invocation
including `lean --version`, so neither the requested throwaway worktree at
`/home/ss/fusion-project/prabhasa-nyaya/wt-spikeA` nor any compile could be created. The main
checkout's `.lake/build` is also stale — it predates T5b entirely (no `BNS46.olean`, no
`IPC415.olean`), so there was no existing artifact set to compile against either.

Consequence: every `#guard` in `CompositionKit.lean` is asserted from reading the sources, not from
a passing build. Each was derived directly from the `required`/`axioms`/`denials` lists and the
definitions of `Contract.check`/`omitted`/`unlicensed`, and the file deliberately uses only API
shapes that the Seeds files already exercise (`.check`, `.omitted _ == [...]`, `.denials == []`,
`.Refuted`, `!`-negated Bools; no `¬` over a Prop, no tactic). **The first thing the next seat
should do is drop it at `lean/PrabhasaNyaya/Seeds/CompositionKit.lean`, add the import to
`lean/PrabhasaNyaya.lean`, and run `lake build`.**

## F1 — `axioms` does double duty, so no composed reading can pass any single Contract

`Contract.check` is `Adequate.check c.axioms r` (`Adequacy.lean:106, 67-68`): *every* claim the
reading makes must be entailed by that one Contract's `axioms`, and `entails`
(`Adequacy.lean:59-61`) closes over nothing but `binds`. So:

- `IPC416.contract.axioms` (`IPC416.lean:76-80`) contains `satisfies conduct cheats` but none of
  s.415's elements. A reading that shows its s.415 working is **unlicensed** on the outer Contract.
- `IPC415.contractPropertyInducement.axioms` (`IPC415.lean:112-116`) contains none of `cheats` or
  `personation`. The same reading is unlicensed on the inner Contract too.

Kit cases c1 / b1 pin both halves. There is today no Contract a rule-1-compliant reading can pass.

The same line is the reason rule 1 is violated in the other direction: because
`satisfies conduct cheats` **is** an axiom (`IPC416.lean:79`), the bare outer assertion (a1 / ba1)
is affirmatively *licensed* and `omitted == []`. Deleting the outer element from `axioms` would fix
a1 and break c1. `axioms` currently means both "this element exists in this section" and "a reading
may assert this without further support"; composition needs those separated — e.g. an
`Contract.references : List (Entity × List ContractId)` field, or the operator taking the family
explicitly and widening the entailment base for the duration of the composed check.

**Impact: rules 1 and 2 are impossible today, in both directions.**

## F2 — `omitted` is blind past its own `required` list

`Contract.omitted` (`Adequacy.lean:111-112`) is `c.required.filter (fun req => !r.claims.contains
req)` — a membership test on one flat list. It cannot descend. Kit cases c3 and b3: the reading is
genuinely short an inner element, and `IPC416.contract.omitted c3 == []`,
`BNS47.contract.omitted b3 == []`. The gap is invisible.

Worse for rule 3, the return type is `List Claim` with **no provenance**. Two different Contracts'
required claims are structurally identical (`Claim.satisfies (Node.ent conduct) <element>`), so a
composed `omitted` list cannot say which Contract an entry came from. The wire already depends on
this list verbatim — `Score.lean:356, 422, 648` join it into the `omitted` field, and the
`pravrudhi` side matches entries back by string equality. Rule 3 ("reported as the referenced
*inner* element's omission") therefore needs either a provenance-carrying result type or a wire
convention that qualifies the element name; both are wire changes needing `d0`.

**Impact: rule 3 impossible today; attributing an omission is a wire-visible change.**

## F3 — no rule picks a limb, and `omitted` does not deduplicate

Both inner references are families, not single Contracts (IPC 415's two limbs, BNS 46's three
routes). `Adequacy.lean` has no notion of a disjunction of Contracts, so the operator must supply
one — and the obvious implementations double-count:

- Concatenating the family's `omitted` lists reports one gap N times. Kit c3: limb A reports 1,
  limb B reports 2, concat length 3 for a single missing element. Kit b3: all three routes report
  exactly 1 each, concat length 3.
- `omitted` is `List.filter` over `required` with **no `nodup` step and no uniqueness invariant on
  `required`**. `BNS46.abettedIsOffenceOrWouldBe` is required by all three routes (`BNS46.lean:188,
  212, 232`); any operator that splices route required-lists together reports that one element
  three times for one absence. This is rule 1/2 double-counting as plain list arithmetic.
- The kit's own convention (fewest omissions, ties by registry order) does **not** resolve b3 on
  cost alone — all three routes tie at 1. A tie-break rule must be stated, and "report all tied
  limbs" is a rule-1 violation.

The only invariant proved about `omitted` today is `required_empty_never_incomplete`
(`Adequacy.lean:116-118`). A no-double-counting operator wants companions —
`omitted ⊆ required`-style containment and a duplicate-freeness result — as theorems, not as a
convention a future author can break silently.

**Impact: rules 1, 2 and 3 all depend on a limb-selection and dedup discipline that does not exist.**

## F4 — rule 4 is not testable on either pair: no inner Contract has any denials

`Contract.Refuted` (`Adequacy.lean:126-127`) is defined solely against `c.denials`. Across both
composition pairs, every `denials` list is empty: `IPC415.lean:117` and `:138`, `IPC416.lean:81`,
`BNS46.lean:185, 209, 229`, `BNS47.lean:105`. The only denial-bearing Contract in the T5b registry
is `BNS69` (`BNS69.lean:112`), and nothing references it as an inner Contract.

So kit cases c4 / b4 cannot be written against real Contracts; the kit pins the emptiness instead,
so the case becomes writable the moment any of them gains a denial, and uses
`BNS69.amountsToRapeAsserted` (`BNS69.lean:153-159`) as the reference inner behaviour.

Two further ambiguities the operator must settle:

1. `Refuted` is a `Prop`/Bool with no witness. "Refutes through **once**" is free at the Bool level,
   but the *trace* the product shows is computed separately and inline —
   `r.claims.filter (fun cl => c.denials.contains cl)`, duplicated at `Score.lean:350, 373, 643,
   660, 677`. There is no `Contract.denied` in `Adequacy.lean` for an operator to compose. A claim
   denied by both an inner and the outer Contract would land in that bucket twice.
2. Nothing says a refuted inner Contract suppresses outer omissions. Rule 4 implies it should
   (refutation is a stronger finding than incompleteness, per `Adequacy.lean:123-125`), but today
   `check`, `omitted` and `Refuted` are three independent computations combined only by
   `Score.lean`'s `flagged` disjunction (`Score.lean:353`). The operator must say whether a refuted
   inner Contract still contributes omissions.

**Impact: rule 4 is unreachable on the current registry, and its "once" guarantee is asserted over
a trace that has no shared implementation.**

## F5 — rule 5 works today, and is the one thing composition can break

Kit c5 and b5 are already exactly right: `IPC416.contract.omitted c5 == [cheats]`,
`BNS47.contract.omitted b5 == [abetsCommission]`, one entry each. The risk is regression — an
operator that descends into the inner family unconditionally turns c5's single omission into four
(one outer + limb A's three) and b5's into seven (one outer + six across the routes). The operator
needs an explicit short-circuit: if the reading asserts nothing from the inner family, report the
outer omission and stop. Kit `c5`/`b5` pin the correct value and also pin the counts that must not
be appended.

**Impact: no blocker, but the rule is a regression guard, not a feature.**

## F6 — cross-Contract matching works only by coincidence of string equality

Each Contract declares its own `conduct : Entity`, and all five happen to be the identical value
`⟨"the conduct in the facts", Sorta.act⟩` (`IPC415.lean:98`, `IPC416.lean:73`, `BNS46.lean:176`,
`BNS47.lean:96`, `BNS69.lean:105`). `Contract.omitted` matches by `List.contains`, i.e. structural
equality on `Claim`, and `Entity`'s `DecidableEq` compares name *and* sort (`Ontology.lean:29-32`).
Nothing in `Adequacy.lean` requires the two Contracts in a pair to agree on the locus, and a rename
in any one file would make composition silently stop matching with no type error anywhere. The
operator should take the locus `Node` as an explicit argument rather than rely on this. The kit
pins the equalities so a rename fails a guard.

A related, already-recorded landmine: `BNS47.abetsCommission`'s own name string still reads
`"-- BNS s.46"` although the three routes are s.45's (`BNS47.lean:82`, and its `notFormalisable`
entry at `:117-120`). The string is a frozen wire value deferred to the post-P2.5 re-typing pass.
Because composition matches on that string, the deferred correction is now also a composition
change, not only a cosmetic one — worth flagging to `d0` before the operator lands rather than
after.

## Summary

| Rule | Status against today's API |
|---|---|
| 1 — one limb satisfies outer once | **Impossible** (F1, F3) |
| 2 — all limbs still once | **Impossible** (F1, F3) |
| 3 — inner omission reported as inner | **Impossible** (F2); attribution is a wire change |
| 4 — inner denial refutes through once | **Unreachable** — no inner denials exist (F4); "once" ambiguous over the trace |
| 5 — silence on inner = one outer omission | **Works today**; needs a short-circuit to stay working (F5) |

Smallest change that unblocks 1–3: separate "element of this section" from "licensed to assert
without support" in `Contract` (F1), give `omitted` a provenance-carrying result (F2), and define
the family disjunction with an explicit, stated limb-selection and dedup rule (F3). Rule 4 needs a
denial-bearing inner Contract before it can be tested at all.

## Reviewer verification — 2026-09-15 11:51 BST

Section 0 is now closed: the reviewer placed `CompositionKit.lean` at `lean/PrabhasaNyaya/Seeds/CompositionKit.lean` in a throwaway worktree of the wire branch (`45c4bdf`, Seeds identical to `db5c5cd`), added the import, and ran `lake build`: **137 jobs, 0 errors, every `#guard` passes** (after one syntax repair by the reviewer: three doc comments `/-- -/` directly preceding `#guard` are not legal Lean and were converted to plain block comments). So F1 (bare outer assertion is licensed; honest composed reading is unlicensed on both Contracts), F2 (inner gap invisible to the outer `omitted`), F3 (concatenated family omissions triple-count) and F5/F6 are verified against the compiled semantics, not inferred.
