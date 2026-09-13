# Test-Time Training × Recursive Self-Improvement — research and implementation plan for Track B

Status: **research + design proposal, not an ADR, not a build criterion**. Nothing here writes to
`pravrudhi_kernel/`, `research/`, or `gates/`. Every number under "Spike" is real (measured on this
box on 2026-09-13) and labeled as a toy-scale illustration, not a benchmark of any production model.

Author: `claude/ttt-llm-research-0f1adf` (Claude Sonnet 5), a research session spun up by the operator
with no other agent active for ~2h while the fleet was cooling down. This document, and the branch it
lives on, are handed off for `cli-trackB` (or whichever session next owns
`~/projects/prabhasa-samskrutam`) to read, question, and act on — see **Handoff** at the end.

## 1. Why this document exists

The operator's framing, verbatim intent: Pravrudhi's RSI loop today improves everything *external* to
a frozen trained LLM — the harness, the corpus, the judge, the gate criteria, the prompts, the retrieval
layer. The weights themselves are a fixed artifact that only changes through an offline SFT/LoRA cycle,
reviewed and signed off by a human. Test-time training (TTT) is the line of work that lets weights
change too, continuously, during inference. The question this doc answers: what happens if RSI (which
improves the *process* that produces the model) and TTT (which lets the *model* improve itself while
running) are combined — first in general, then specifically for Track B's law-tuned foundational model
and the MVP pitch that model has to carry.

## 2. TTT for LLMs — where the field is (September 2026), not vision

TTT-for-vision (the original 2019–2022 line, adapting a classifier's BatchNorm/rotation-prediction head
per test image) is a different literature and out of scope here. The LLM/sequence-modeling line has its
own lineage:

**Foundational architecture work.**
- *Learning to (Learn at Test Time): RNNs with Expressive Hidden States* (Sun et al., the
  `test-time-training` org) — TTT-Linear and TTT-MLP replace a fixed-size RNN hidden state with a small
  ML model (linear map / 2-layer MLP) whose "hidden state update" *is* a step of self-supervised
  gradient descent. At long context (past ~8k tokens) both variants beat Mamba, and the gap widens with
  context length — the core empirical claim of the whole line: an expressive, trainable hidden state
  scales better with context than a fixed-size one. [GitHub](https://github.com/test-time-training/ttt-lm-pytorch)
- *Titans: Learning to Memorize at Test Time* (Behrouz, Zhong, Mirrokni, Google Research) — a neural
  long-term memory module that decides what to memorize using a "surprise" signal (the gradient of its
  own loss), explicitly modeled on how surprising events are remembered more vividly. Memorization and
  forgetting both happen inside the forward pass. [arXiv:2501.00663](https://arxiv.org/pdf/2501.00663)
- *In-Place Test-Time Training* (2026) — the most deployment-relevant recent variant: instead of a new
  layer type, it repurposes the existing MLP's down-projection (`W_down`) as the fast weight, updated
  chunk-wise (512–1024 tokens) with a next-token-prediction-aligned objective, proven to raise correct-
  token logits while leaving incorrect ones "almost unchanged." It clips update magnitude
  (`τ = 1e-5`) for numerical stability and resets fast weights at document boundaries specifically to
  stop context leaking across unrelated sequences. Evaluated on 4B–14B Qwen3/LLaMA-3.1 at RULER
  (4k–256k), MMLU, HellaSwag, ARC, PIQA. This is the closest published design to "drop TTT into a model
  you didn't train for it," which matters for Track B since it wouldn't require training a TTT-native
  architecture from scratch. [arXiv:2604.06169](https://arxiv.org/html/2604.06169v1)
- *Transformer² (Transformer-Squared): Self-adaptive LLMs* (Sakana AI) — doesn't touch weights via
  gradient descent at all; a dispatch pass classifies the incoming task, then mixes pre-trained
  **expert vectors** (singular-value fine-tunes, SVF) into the base weights. An SVF update on
  Llama-3-8B needs ~0.58M parameters vs LoRA's ~35M for the same slot — two orders of magnitude
  cheaper per adaptation, at the cost of the expert vectors having to be trained offline first (RL,
  per-domain). Relevant to Track B as a *lighter* alternative to LoRA-based TTT for the "adapt to this
  case's jurisdiction/language register" slice of the problem. [arXiv:2501.06252](https://arxiv.org/pdf/2501.06252)

**Meta-learned self-adaptation (weights DO change, persistently).**
- *SEAL: Self-Adapting Language Models* (MIT Improbable AI Lab) — the model generates its own
  "self-edit" (restructured training data + hyperparameters), applies it via a LoRA SFT step, and an
  outer RL loop rewards self-edits that improve downstream performance. SEAL is explicitly framed by
  its own authors as "a round of TTT in the inner loop." Its own reported failure mode, verbatim from
  the paper's discussion: **repeated self-edits can lead to catastrophic forgetting**. This is the
  single most load-bearing citation for §4 below. [arXiv:2506.10943](https://arxiv.org/pdf/2506.10943)
- *SIA: Self Improving AI with Harness & Weight Updates* — combines an agent harness with an
  entropic-utility-objective weight-update stack on top of LoRA; the framing (harness *and* weights,
  not harness *or* weights) is the closest published statement of this document's core question.
  [arXiv:2605.27276](https://arxiv.org/pdf/2605.27276)

**Agentic / RSI-adjacent TTT (this is the literature that answers "RSI + TTT combined").**
- *Self-Improving LLM Agents at Test-Time* — studies the harness-only regime explicitly (memory,
  prompts, tool use; weights frozen) and positions it as **distinct from** recursive self-improvement
  schemes that require retraining. This is a precise description of what Pravrudhi's heartbeat/RSI loop
  already does today — the "before TTT" baseline this whole document is arguing past.
  [arXiv:2510.07841](https://arxiv.org/pdf/2510.07841)
- *No Time Like the Present: Agentic Test-Time Training for LLM Agents* (aTTT) — an agent adapts
  continuously across a multi-turn episode by training on its own trajectory, with a token-reweighting
  trick (down-weight repeated n-grams from prior turns) to stop the agent's own failure loops from
  poisoning its own training signal. +5.0 pts ALFWorld, +4.9 pts SWEbench Lite.
  [arXiv:2607.03441](https://arxiv.org/pdf/2607.03441)
- *TT-RL (Test-Time Reinforcement Learning)* — full RL updates at test time on unlabeled data, using
  majority vote or a reward model as the self-supervision signal (found via search, not deep-read here;
  flagged for Track B's own follow-up since it's the most aggressive point on this spectrum).
- Landscape maps: `awesome-rsi` and `Awesome-Harness-Self-Improvement` (GitHub, both actively curated)
  give a fuller reading list than this document reproduces; worth `cli-trackB` bookmarking directly
  rather than this doc re-listing every entry.

**Stability / forgetting mitigation (the piece every TTT-for-LLM paper treats as a footnote and every
production system treats as the whole problem).**
- 2026 consensus per multiple surveys: LoRA-family adapters are the default specifically *because* the
  base weights never move, so forgetting is zero by construction *for the base model* — the forgetting
  risk moves entirely into "does repeatedly retraining the adapter forget what the adapter itself
  learned earlier." Two concrete named mitigations found: **LoRA-Based Continual Learning with Critical
  Parameter Constraints** (explicit constraint stopping new updates from overwriting parameters
  identified as critical to prior adapters) and **FOREVER: Forgetting-Curve-Inspired Memory Replay**
  (schedules replay/regularization strength from an Ebbinghaus-curve model of when forgetting would
  otherwise happen). Both are 2026, both are LoRA-scoped, both are directly applicable to Track B's
  existing `sft-lora` recipe.
- The blunt industry line, also 2026: **"frontier-scale weight updates remain offline because every
  update needs evaluation, safety review, and rollback."** No production frontier lab is shipping live
  continuous weight updates to a served model without a human-gated promotion step. This matters
  enormously for §5 — it means the "engine-level" ambition here is not "make Track B's model update
  itself live in prod," it is "make the *proposal* of an update automatic and the *promotion* still
  gated," which is exactly what Pravrudhi's `gate.py` + `configs/delegation.yaml` already do for every
  other kind of change.

**Legal-domain grounding (the MVP pitch's literature, not TTT's).**
- Citation hallucination in legal LLMs is measured at 13–21% of generated citations across current
  systems; RAG reduces it by constraining citation to retrieved evidence rather than parametric recall;
  the highest-leverage architectural move identified across multiple 2026 papers is exactly what
  Track B's finetune proposal already does — *stop asking the model to recall facts from its weights,
  put the facts in front of it, and make citing outside that context an explicit, checkable failure
  mode.* [LegalCiteBench](https://arxiv.org/html/2605.10186v1),
  [Citation Grounding](https://arxiv.org/pdf/2606.00898),
  [Who Checks the Citations?](https://arxiv.org/pdf/2606.21155). None of these papers use TTT — they
  validate the *graph-grounding* half of Track B's design, independent of whether TTT is ever added.

## 3. What RSI + TTT combined actually means, in general

Two axes, previously orthogonal in Pravrudhi's own architecture:

| | changes the **process** (RSI, today) | changes the **weights** (TTT, proposed) |
|---|---|---|
| **who improves** | the harness: judge, gate criteria, corpus curation, prompts, retrieval, routing | the model itself, from its own usage |
| **when** | between releases, offline, one heartbeat "beat" at a time | during/immediately after each inference |
| **how it's checked today** | `gate.py` + `configs/delegation.yaml`, human sign-off required for canonical checkpoints | nothing — Pravrudhi has no TTT path yet |
| **literature term** | "self-improving agent at test time" (harness-only) — arXiv:2510.07841 | SEAL / aTTT / SIA / TT-RL |

Combining them is not "turn TTT on and let the RSI loop supervise it" as an afterthought — the
literature (SEAL's own admitted forgetting, aTTT's need for token-reweighting to stop self-poisoning,
TT-RL's reliance on an external reward signal) converges on one structural fact: **an unsupervised or
self-supervised weight-update loop needs an external, independent, non-self-referential check on its
own output, or it degrades.** That check is *exactly* what an RSI harness already is: a component that
observes the system from outside, applies a fixed evaluation, and gates promotion. So the honest
one-sentence answer to "what happens if you combine them" is:

> **TTT gives the RSI loop a new kind of action to propose (a weight delta, not just a harness change);
> RSI gives TTT the safety property none of its own papers can derive from first principles (a gate that
> is not the same process that produced the update).**

This is also the resolution the TRIZ analysis below converges on independently (§4), which is a useful
cross-check: two different reasoning tools (literature synthesis, contradiction analysis) landing on the
same architectural shape is a much stronger signal than either alone.

**What this unlocks, generally, once built (roughly in order of how defensible each claim is):**

1. **Continuous grounding instead of periodic SFT.** New case law, new corrected abstentions, and
   drift in what "correct" citation looks like currently only reach the model at the next SFT/LoRA
   cycle (Track B's `finetune` step, run by a human decision). A gated TTT path lets the model propose
   its own micro-adaptation to *this session's* graph context immediately, with the RSI loop deciding
   whether that proposal is worth consolidating into the next real LoRA training run — turning "we
   should retrain on this" from a human noticing a pattern into a measured, queued signal.
2. **A new, real drive for `pramana_navyata` (evidence freshness).** Per `kshudha.py`'s own docstring,
   this is the one drive in the six-drive appetite model that **has no source module and always reports
   `unknown`** — not a gap in this document's imagination, a gap already named in the code. TTT gives it
   a real, measurable source for the first time: "how much does the current model's behavior on recent
   graph nodes differ from a freshly-adapted one" is a number the engine could compute and act on,
   instead of reporting `unknown` forever.
3. **Self-improvement that is bounded by construction, not by discipline.** Today "don't merge adapters
   into canonical checkpoints without human sign-off" (CHARTER) is a rule people and agents follow.
   With a TTT proposal pipeline routed through `gate.py`, it becomes a rule the *pipeline* enforces the
   same way it already enforces `sign_gate` refusing agent identities — the same mechanism, a new input
   type.
4. **Higher-leverage automation on the *unwired* half of the appetite model.** `unnati_avakasha`
   (benchmark headroom) already has a source; `pramana_navyata` gets one from #2. Two of six drives
   moving from "always unknown" / "static" to "TTT-informed" is a bigger jump in the RSI loop's own
   self-knowledge than any single harness improvement shipped in the 2026-09-12 team-lead era (per
   memory `pravrudhi-standing-objectives`), because it changes what the loop can *see*, not just what
   it can *do*.
5. **The risk, stated as plainly as the literature states it:** every paper in §2's "meta-learned"
   and "agentic" rows lists forgetting / self-poisoning / drift as *the* open problem, not a solved
   footnote. Combining RSI+TTT does not remove that risk — it gives Pravrudhi a pre-existing mechanism
   (gates, delegation, ledger) to contain it that most of the papers above had to invent bespoke
   solutions for (token-reweighting, critical-parameter constraints, forgetting-curve replay). That is
   the actual argument for doing this *in Pravrudhi specifically*, not a general argument that TTT is
   safe.

## 4. TRIZ analysis (via `triz-engine`)

Four contradiction-matrix lookups, chosen to isolate the sub-problems separately rather than one vague
"adaptability vs. safety" pair:

| # | Improving | Worsening | Principles (IDs) |
|---|---|---|---|
| 1 | Adaptability/versatility (35) | Reliability (27) | 1 Segmentation, 13 The Other Way Around, 8 Anti-weight, 24 Intermediary |
| 2 | Adaptability/versatility (35) | Difficulty of detecting/measuring (37) | 1 Segmentation |
| 3 | Extent of automation (38) | Device complexity (36) | 15 Dynamics, 24 Intermediary, 10 Preliminary Action |
| 4 | Speed (9) | Loss of time (25) | 10 Preliminary Action, 13 The Other Way Around, 28 Mechanics Substitution, 38 Strong Oxidants |

**Physical contradiction** ("the weights must be both fixed, for auditability, and variable, for
continuous learning, at the same time") resolved via the engine's separation-principle lookup:
- **Space**: different *parts* of the weight-stack carry different requirements — not the whole model.
- **System level**: the whole committed system stays reproducible at the gate level while a *part*
  (an ephemeral fast-weight delta) varies per session — directly analogous to the engine's own example,
  "distributed system: eventually consistent globally, strongly consistent locally."
- **Time**: separate the "when you learn" from "when you answer" and from "when you commit" into three
  different cadences, rather than one.

**Reading the principles back into a concrete architecture** (this is the design in §5, derived, not
asserted):

- **Segmentation (1)** → a **three-tier weight stack**, not one mutable blob:
  1. frozen base checkpoint (never touched, current state),
  2. gated persistent LoRA (Track B's existing `sft-lora` recipe, promoted only by human sign-off —
     unchanged from today),
  3. a new **ephemeral fast-weight tier** (small rank, resettable, diffable in isolation) — the TTT
     layer, and *only* the TTT layer, is allowed to move live.
- **Intermediary (24)** → this is not a new component. `gate.py` + `configs/delegation.yaml`'s
  `sign_gate_delegated`/`unmet_conditions` machinery already is an intermediary between "an agent
  proposes a change" and "the change is canonical." A TTT delta proposing to graduate from tier 3 to
  tier 2 is just a new *kind* of gate input, checked by the same closure-layer + delegation-conditions
  pattern already used for every other gate today. No new trust boundary is invented.
- **The Other Way Around (13)** → invert commit order and invert *when* adaptation happens relative to
  the answer: never "adapt live, answer, hope it was fine" — always "answer from the last *accepted*
  state now, adapt in shadow, check the shadow before it ever becomes the served state next time."
  This is also directly how `sign_gate_delegated` already works (`check_gate` runs *before* `_sign`
  ever touches the file) — the pattern already exists, TTT just needs to use it for weight deltas too.
- **Anti-weight (8)** → pair every TTT-adapted response with a cheap frozen-baseline comparison
  (exactly what the regression probe in the spike below does) so the "lift" of adaptation is measured
  against a fixed counterweight, not asserted.
- **Dynamics (15) / Preliminary Action (10) / Mechanics Substitution (28) / Strong Oxidants (38)** →
  the *mechanism* of the update itself should be the cheapest one that works: In-Place TTT's closed-form
  `W_down` update or Transformer²'s SVF mixing, not full backprop through the whole network; and where
  possible, precomputed/async (adapt after answering, not during) rather than adding latency to the
  live request. This is a direct steer *away* from "backprop through a full LoRA rank-16 stack on the
  live request path" and *toward* the smaller, cheaper mechanisms §2 already surfaced as SOTA.

`score_solution` scored the resulting design **3/4 against the Ideal Final Result** (near-IFR): it
leverages existing resources (gate.py, delegation.yaml, the unwired kshudha drives), costs no new
infrastructure, and introduces no new problems the papers above haven't already flagged and this
design already routes around — the one unmet IFR criterion is "self-resolving": this is a *designed*
containment (a gate check), not a contradiction that disappears by architecture alone. That is
consistent with the industry-consensus line in §2 ("frontier-scale weight updates remain offline
because every update needs evaluation") — nobody has made this self-resolving yet, and this document
does not claim Pravrudhi will be first.

## 5. Track B specifically: superpowers for the foundational model, without breaking the MVP pitch

Track B's own finetune proposal (`proposals/prabhasa-nyaya/finetune/README.md`) already states the MVP
pitch precisely: *"answers a question of law with the statute or precedent it relied on, and says it
does not know rather than inventing a citation... a wrong answer can be traced back to the inference
step that produced it."* Every recommendation below is written to strengthen that pitch, not compete
with it — TTT that improved raw accuracy while making answers *less* traceable would be a net loss for
this specific product.

### 5.1 What TTT adds on top of the existing graph-grounded LoRA design

Track B's `finetune` step already trains the model to condition on a linearized subgraph and cite only
nodes present in it (§ of that README). TTT's addition is narrow and specific: **a self-supervised
adaptation pass over *this query's* subgraph, before generation, so the model's attention/representation
of the just-retrieved nodes is fresher than what a static LoRA trained weeks ago encodes.** Concretely,
per query:

1. Retrieval hands the model a subgraph (as today — no change to retrieval).
2. A small, cheap, ephemeral fast-weight adaptation (rank ≤ 8, few steps, self-supervised
   next-token-prediction *on the subgraph text itself*, never on any candidate answer) runs before
   generation.
3. Generation proceeds from the adapted state.
4. The fast-weight delta is **discarded by default** (tier 3, ephemeral) unless the RSI harness's gate
   flags this query's pattern as worth consolidating (§5.3).

This is closest to the *In-Place TTT* design in §2 (repurpose an existing projection, chunk-wise
update, LM-aligned objective, reset at boundaries) rather than SEAL's persistent self-edit design —
persistence is exactly the thing Track B's MVP pitch cannot afford without a human-gated promotion step
in between, per CHARTER.

### 5.2 Why this strengthens the legal MVP pitch specifically (not just "the model gets smarter")

- **Traceability survives.** Every served answer still comes from a state that is either (a) the frozen
  gated snapshot, unmodified, or (b) that snapshot plus a fast-weight delta trained *only* on the exact
  subgraph shown in that answer's own prompt — which means the "what did the model rely on" trace for
  audit purposes is *still exactly the subgraph in the prompt*, because the delta cannot have introduced
  outside information (it was never shown any). This is a much stronger property than "the LoRA was
  trained on a big corpus once" — it's per-answer, inspectable, and reconstructable.
- **Abstention gets sharper, not softer.** The legal literature (§2, LegalCiteBench et al.) identifies
  grounding + calibrated abstention as the highest-leverage lever against hallucination. A fast-weight
  pass conditioned only on the subgraph should, if it works, make the model *more* sensitive to what the
  subgraph does and doesn't support — i.e. more likely to abstain correctly, not less. The spike below
  is a first, small, honest check of whether that's true in practice (it moved abstention accuracy from
  0.75 to 0.75/1.00 across conditions — see §6, read with the stated caveats about n=10).
- **New case law becomes a measured signal, not a backlog item.** Today, "the corpus is stale" is
  something a human notices. With TTT wired to `pramana_navyata` (§3.2), the gap between "how the frozen
  model treats a newly-ingested statute" and "how a TTT-adapted pass treats it" becomes a number the
  engine reports — a concrete, checkable "freshness debt" metric that feeds the next `finetune` proposal
  with evidence instead of a hunch.
- **The MVP pitch gets a second, harder-to-fake proof point.** "Cites only what's in context" is
  checkable today by inspecting one answer. "The model can absorb a brand-new statute at query time
  and correctly ground its answer in it, with the fast-weight delta thrown away right after" is a
  demo Track A's verifier (the Lean 4 Nyaya layer) could plausibly gate on too — turning TTT from an
  engine-internal capability into an externally-demonstrable one.

### 5.3 Concrete phased plan for Track B (proposal, sized for `cli-trackB` to scope, not a build criterion)

**Phase 0 — measurement only, no new inference path.** Reuse Track B's existing gated eval
(`mmlu_professional_law`/`mmlu_pro_law` via `scripts/ext_eval.sh`, per memory
`pravrudhi-multi-repo-team-2026-09-12`) plus the specialty panel. Add one new offline measurement: for a
held-out slice, compare frozen-LoRA vs. frozen-LoRA-plus-ephemeral-TTT-pass on citation-groundedness and
abstention-accuracy (the same two metrics the spike in §6 uses, on real Track B data instead of toy
data). This answers "does TTT help *this* model on *this* task" before any inference-path change is
proposed. No promotion, no gate change, no new drive wiring — purely evidence-gathering, the kind of
step CHARTER's EMPIRICAL bar already requires before anything downstream can cite a number.

**Phase 1 — ephemeral-only TTT in a shadow path.** Wire the fast-weight adaptation into the inference
path but *only* in shadow mode: it runs, it's logged, it never changes what's served. This validates
latency/stability (the In-Place TTT paper's clipping mechanism, `τ`, and reset-at-boundary discipline
are the reference implementation to adapt) with zero user-facing risk.

**Phase 2 — ephemeral TTT live, gated regression probe.** Serve from the adapted state (tier 3, per
query, reset after). Add the regression-probe check from §6's spike as a real gate layer (a fixed,
versioned canary set — general capability + a handful of settled-law sanity questions the model must
never get wrong) so a bad single-query adaptation is caught before it's ever served, not just logged.
This is the first phase where the "Anti-weight" and "Intermediary" TRIZ principles become load-bearing
in production, not just in the toy spike.

**Phase 3 — consolidation proposals, still human-gated.** Only after Phase 2 has run long enough to
accumulate a pattern (e.g., the same statute keeps triggering large, consistent fast-weight deltas
across many queries) does the harness propose — never apply — a real LoRA retraining run incorporating
that pattern, through the existing `finetune`/`gates` pipeline exactly as today, with the TTT signal as
new *evidence* for the proposal rather than a new *authority* to act on it. This is where
`pramana_navyata` gets wired to a real source (§3.2) and where CHARTER's "no merging adapters into
canonical checkpoints without human sign-off" stays exactly as strict as it is today — TTT changes what
generates the *proposal*, never who signs the *promotion*.

**Explicitly out of scope for Track B right now:** persistent self-edits (SEAL-style), TT-RL, or any
design where the fast-weight tier is not reset by default. Track A's Lean 4 verifier and the gate
pipeline are not yet positioned to review a continuously-drifting model; building toward that is a much
larger, later bet than anything in this phased plan.

## 6. Spike: a small, honest, toy-scale check of the mechanism

**What this is not:** a benchmark, a claim about Track B's actual model, or evidence usable in any gate.
It ran against `Qwen/Qwen2.5-1.5B-Instruct` (already cached on this box, chosen only for being small and
present — no relation to Track B's Sanskrit-core model), n=10 synthetic legal-style examples, and a
hand-rolled rank-4 LoRA (not `peft` — this box's shared training container has a broken
`peft`/`torchao` version pairing; reported as a real environment issue, not routed around silently, see
"Environment note" below). Code and raw results: `docs/research-spikes/2026-09-13-ttt-rsi-track-b/`
(`run_ttt_rsi_prototype.py`, `report.json`). Run on the RTX 5090 via the existing idle `rtx5090-train`
container (confirmed 0% GPU utilization before and reserved for ~15–20 seconds; no collision with any
other job — `nvidia-smi` and `docker ps` checked immediately before running).

**Setup.** 10 synthetic questions shaped like Track B's prompt format (a linearized subgraph of
labeled statute/case nodes + a question), 6 answerable from the given subgraph and 4 deliberately not
(correct behavior: abstain with a fixed phrase). A fixed 5-prompt "regression probe" (arithmetic,
translation, common sense — nothing legal) whose next-token NLL under the model stands in for "did we
break something unrelated." Three conditions, same 10 examples, same order:

- **frozen** — no adaptation.
- **ephemeral** — before each example, the LoRA is reset to a fixed random `A` / zero `B` (true reset,
  not just zeroing `B` — an earlier version of this script had exactly that bug, caught by watching the
  probe trace fail to return to baseline between examples; fixed before the numbers below), then 6
  AdamW steps (lr 1e-3) of next-token prediction **on the subgraph text only**, never the question or
  answer.
- **accumulated** — identical per-example adaptation, but the adapter and optimizer state are **never**
  reset between examples — continuous online TTT with no safeguard, the condition the literature (§2)
  warns about.

**Results** (full numbers in `report.json`):

| condition | correct-behavior rate | hallucination rate | abstention accuracy | probe NLL: start → end |
|---|---|---|---|---|
| frozen | 0.4 | 0.1 | 0.75 | 1.660 → 1.660 (flat, by definition) |
| ephemeral | 0.6 | 0.1 | 0.75 | 1.665 → 1.696 (bounces 1.67–1.83 per example, no trend) |
| accumulated | 0.8 | 0.2 | 1.00 | 1.665 → 2.011 (monotonic climb through the sequence) |

**Gate simulation** (post-hoc, applied to the accumulated run's trajectory): a relative-NLL-increase
threshold of 15% over the frozen baseline would have **rejected 7 of the 10 updates**, first triggering
at example 4 (a 25.7% relative increase) — meaning a live gate, wired in from the start, would have
caught the drift a third of the way through the sequence, well before the probe NLL had climbed past
2.0.

**Reading this honestly, at n=10:** the point-estimate differences (0.4 / 0.6 / 0.8 correct-behavior
rate) are not statistically meaningful on their own — this is 10 synthetic examples on a model with no
legal training at all. What *is* a real, structural finding, independent of sample size: **ephemeral
TTT (reset every example) does not accumulate drift on the regression probe; un-gated continuous TTT
does, visibly and monotonically, on the same adaptation strength, same data, same model.** That
contrast is the mechanism this whole document argues for — not "TTT makes legal answers better" (this
spike is far too small to claim that), but "un-gated continuous TTT drifts, ephemeral-with-gate does
not, and a cheap gate catches the drift early." The accumulated condition also happened to score highest
on the toy task metric, which is the SEAL/aTTT story in miniature: **accumulation can look like it's
winning right up until the regression probe says otherwise** — exactly why the gate needs to be
structural (§4, §5.3), not a thing a human remembers to check.

**Environment note (for whoever runs this next):** `rtx5090-train`'s `peft==0.19.1` requires
`torchao>=0.16.0`, but the container has `torchao==0.11.0+git`. `get_peft_model` fails at import time
with `ImportError: Found an incompatible version of torchao`. This spike worked around it with a
~30-line hand-rolled `LoRALinear` (in `run_ttt_rsi_prototype.py`) rather than upgrading `torchao` in a
shared container other sessions may depend on. If Track B wants real `peft` in that container, that's a
one-line `pip install -U torchao` for someone who's confirmed nothing else pins the old version —
flagged here, not fixed here.

## 7. Core-engine hooks — a sketch, not a decision

Per CHARTER, `pravrudhi_kernel/` (T0) changes require an accepted ADR before any commit — nothing here
proposes editing it. This section sketches where a *general* TTT capability would attach to the existing
engine, for whoever writes that ADR (Studio's team-lead line, per `pravrudhi-team-lead` memory, or the
operator directly), not as something this session is asking to be approved.

- **`kshudha.py`'s `pramana_navyata` drive** goes from "no source module, always `unknown`" to having a
  real source: the delta between a frozen model's behavior and a TTT-adapted pass's behavior on recently
  ingested content. This is additive — it fills an acknowledged gap, it doesn't change any existing
  drive's semantics.
- **`heartbeat.py`'s `GPU_CAPABILITIES` frozenset** (`{"pretrain", "finetune", "rl", "performance"}`,
  the set of capabilities that compete for the GPU a live training night has already claimed) would need
  a new member, e.g. `"ttt_consolidate"`, so a future consolidation-proposal beat (§5.3 Phase 3) doesn't
  silently compete with an active training job — reusing the exact contention guard that already exists,
  not inventing a new one.
- **`gate.py` / `configs/delegation.yaml`** would need a new gate-closure layer (alongside
  `domain_gate`, `signoff`, etc. — see `GateReport`/`Signoff` in `pravrudhi_kernel/schema` for the
  existing shape) representing "regression-probe check passed," so `sign_gate_delegated`'s
  `unmet_conditions` can refuse an autonomous close the same way it already refuses one with a failing
  domain gate. `sign_gate` itself already refuses every agent identity for a *human* sign-off act; a TTT
  consolidation proposal reaching that point would go through the same human gate every other canonical
  checkpoint change goes through today — CHARTER's line stays exactly as strict.
- **Deliberately not sketched here:** any change to what counts as "the model" for versioning/release
  purposes, any user-facing surface for triggering TTT, and any interaction with the Studio/User-edition
  split (`docs/adr/2026-09-12-super-and-user-pravrudhi-intent.md`) — all three are bigger, separate
  decisions than this document's scope.

## 8. Risks and open questions, stated plainly

1. **Every cited TTT-for-agents paper (SEAL, aTTT, SIA) treats forgetting/drift/self-poisoning as an
   open problem it mitigates, not one it solves.** This document's containment strategy (gate the
   promotion, reset by default) reduces exposure; it does not make the underlying phenomenon go away.
2. **The spike is toy-scale and single-run.** No seeds varied, no statistical test, n=10. It demonstrates
   a mechanism, not a magnitude. Phase 0 (§5.3) exists specifically to get a real magnitude on real
   Track B data before anything is built.
3. **Latency.** Even the cheapest TTT mechanisms in §2 (In-Place TTT's chunk-wise update, Transformer²'s
   SVF mixing) add compute to the request path unless done asynchronously. §4's TRIZ resolution
   (Preliminary Action / Mechanics Substitution) argues for async/precomputed adaptation, but that is a
   design preference here, not something the spike measured.
4. **Who reviews a regression-probe canary set, and how often does it need to change?** A stale canary
   set is a false sense of safety — this needs its own answer before Phase 2 (§5.3), and isn't answered
   in this document.
5. **Does Track A's verifier need to know a TTT pass happened at all?** If the served answer's
   traceability claim (§5.2) is "reconstructable from the prompt alone," Track A shouldn't need to care
   — but that claim itself should be checked by Track A, not asserted by Track B or by this document.

## 9. Handoff — branch, files, and how to pick this up

- **Branch**: `claude/ttt-llm-research-0f1adf` (this session's worktree branch, based on `main` at
  `71336e9`/`63618d3`, two commits behind current `main` tip `62db7b1` as of 2026-09-13 — rebase or diff
  against current `main` before treating anything path-specific here as current). **Not merged to
  `main`, not intended to be** — this is a research artifact, pushed for visibility, not a change
  landing anywhere.
- **This document**: `docs/ttt-rsi-track-b-research-and-implementation.md` (this file).
- **Spike code + raw results**: `docs/research-spikes/2026-09-13-ttt-rsi-track-b/`
  (`run_ttt_rsi_prototype.py`, `report.json`). Reproducible by anyone with access to a CUDA box with
  `torch`+`transformers` (no `peft` needed, it's hand-rolled) — see the environment note in §6 if using
  the `rtx5090-train` container specifically.
- **TRIZ working notes**: `.triz/session.jsonl` in this worktree (gitignored, local only — the
  parameter lookups and IFR score in §4 are reproducible via the `triz-engine` MCP plugin's
  `list_parameters`/`lookup_matrix`/`get_separation_principles`/`score_solution` tools if useful to
  redo or extend).
- **Nothing in this branch touches** `prabhasa-samskrutam` (Track B's actual repo), `prabhasa-nyaya`'s
  `research/`/`gates/` directories, or any canonical checkpoint. The spike's model, data, and code are
  entirely self-contained and disposable.
- **For `cli-trackB` (or whoever owns Track B next):** start at §5 and §6. §5.3's Phase 0 is sized to be
  a single measurement pass against data you already have (no new infra) — the natural first move if
  this direction is worth pursuing at all. If GPU time is scarce or a training night is live, everything
  in this document can wait; nothing here is time-sensitive. Reply through the same channel Track B
  normally uses (per `pravrudhi-team-lead` memory: push a branch, the team-lead line reviews and merges)
  rather than treating this document itself as authoritative — it's a research proposal, not a decision.

## 10. What the prototype found (2026-09-13, same day, Track B's real model and real held-out set)

Everything below was measured on Track B's actual 370M law-tuned checkpoint
(`m7_retry_checkpoint.pt`) against Track B's own 690-item held-out set and its own scorer
(`scripts/eval/score_law_qa.py`), by the prototype in `prototypes/nyaya_ttt_rsi/`
(run `rsi_run1e`; `report.md`/`report.json`/`report.html` committed there). Every number has a
Wilson interval in the report; comparisons are paired McNemar over the same 690 items.

| metric (690 held-out) | A: published closed-book | B′ frozen + harness | B′ consolidated | C′ = B′ + per-query TTT | D = one RSI self-training round |
|---|---|---|---|---|---|
| citation recall (score_law_qa) | 0.004 | 0.176 | 0.145 | 0.128 | **0.198** |
| citation precision | 0.004 | 0.206 | 0.190 | 0.190 | **0.232** |
| abstention correctness (9 items) | 0.556 | 0.444 | **0.889** | 0.889 | 0.889 |
| law_lookup prefix similarity | 0.059 | 0.108 | **0.626** | 0.617 | 0.617 |
| gold passage selected | – | 0.104 | 0.491 | 0.467 | **0.501** |
| abstain when gold not retrieved | – | 0.514 | 0.777 | 0.770 | **0.851** |
| false abstain when gold shown | – | 0.430 | 0.116 | 0.182 | **0.086** |
| emitted citation grounded in context | 0.0 | 1.0 | 1.0 | 1.0 | 1.0 |

- **A → B′: p = 1.1e-50** (gold-citation-present 0.003 → 0.259, bootstrap CI [0.22, 0.29]). The MVP
  claim — *cites only what is in context, or abstains* — holds by construction (grounded rate 1.0)
  and the accuracy behind it is no longer zero. Retrieval ceiling: recall@4 = 0.77 with section
  titles in the corpus, so gold-selected 0.49–0.50 is ~65% of what retrieval allows.
- **What did the work:** the RSI *harness* — a title-bearing retrieval layer, a compact node context
  that fits the model's 512-byte training regime, likelihood *selection* over uniform candidates
  instead of free generation, a harness-calibrated abstention rule (τ on best NLL, δ on margin,
  fitted on a train-split dev slice, balanced accuracy 0.84 vs 0.58 for the frozen model), and one
  gated consolidation SFT on 2,496 harness-built examples (regression probe Δ +7.5%, under the 15%
  gate). B′-frozen shows the harness alone already lifts recall 0.004 → 0.176; consolidation is what
  fixes abstention (0.44 → 0.89), selection (0.10 → 0.49) and lookup text (0.11 → 0.63).
- **Per-query ephemeral TTT (the C′ column and a 8-cell dev sweep over steps × lr × targets): a
  clean negative at 370M.** Best sweep cell +0.008 gold-selected on dev (below the 0.03 bar); the
  aggressive cell (8 steps, lr 3e-3, all projections) was destructive (−0.41, false-abstain 0.86).
  §3's thesis survives only in its first half at this scale: TTT gave the loop a new kind of
  proposal, and the gate did its job (690/690 per-query decisions checked) — but the proposals were
  not worth keeping. The design's containment worked; the capability did not appear.
- **One RSI self-training round (D):** 500 fresh train-split prompts, no gold read; the harness's own
  τ/δ accepted 366 pseudo-labels (for the record only, 70% matched gold); consolidation passed the
  probe gate; re-calibrated. Directionally better on every sub-metric, **not significant** on the
  headline (p = 0.54 at n = 690). One round is not evidence of compounding; it is evidence the loop
  closes without a human in it and does not degrade.
- **Six data/decoding defects in Track B's own pipeline were found and fixed on the way** (F11–F16
  in `docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md`): no stop
  terminator in any SFT target (why its `citation_reached` was 25/227), a greedy-decoding trap toward
  any fixed short string, section titles absent from the corpus (why "which provision states
  '<title>'" was unlearnable from context), abstain-string memorisation from exact repetition, a
  length bias in total-NLL selection, and the context-budget arithmetic. Plus the environment fixes
  F1/F2/F5–F7 and **F17: Track B's 1.13B SFT OOM is closed** (fragmentation falsified with the
  pre-registered test; batch 4 completes 40/40 at 23.8 GiB, 3 epochs ≈ 26 min).
- **Revised reading of §5.3:** Phase 0–2 of the plan are effectively done and the answer to "does
  per-query TTT help *this* model on *this* task" is no. The next lever is scale, not test-time
  updates: the 1.13B line, now unblocked, with the same harness. A first real 1.13B SFT using the
  round-1e data is recorded under `prototypes/nyaya_ttt_rsi/runs/g0_sft_round1/` (§11).

## 11. The 1.13B line: first real SFT completes (2026-09-13, 14:00–14:50 BST)

**What ran.** Track B's 1.13B Megatron-Core checkpoint (`m4/final.pt`, 1,129,634,752 params) was
fine-tuned for one epoch on the union of Track B's own `m7_mix_v1.jsonl` and the harness-built
round-1e grounded SFT set (14,908 examples, 1.90M tokens) with the new trainer in
`prototypes/nyaya_ttt_rsi/g0/train_megatron_sft.py`, inside the isolated `ttt-lab` image. No
Track B file was modified. Raw record: `runs/g0_sft_round1/{train_report.json, docker_stdout.log,
smoke_stdout.log, smoke2_stdout.log}`; the 4.5 GB `final.pt` stays on disk, untracked
(sha256 `fe800a55…9e58ae`, recorded in the report).

| | value |
|---|---|
| shape | batch 2 × grad-accum 4 (effective 8), seq_len 1536, lr 1e-5, warmup 50, vram-fraction 0.85 |
| steps | 7,454 micro / 1,864 optimizer |
| wall | 1,221 s (20.4 min) |
| peak VRAM / RSS | 26.0 GiB / 6.7 GiB |
| final loss | 4.9e-05 on the last (round-1e) batch; m7-mix batches stayed 0.1–2.9 throughout |
| reload check | in-process reload + forward pass OK, logits `[1, 8, 256]` |

**Two findings that change the F17 picture.** (1) F17's "batch 4 completes" was true at
seq_len 512; at the 1536 the round-1e prompts actually need, batch 4 passes preflight but OOMs on
the micro-step after the first optimizer step, once AdamW's fp32 state exists, at both 0.85 and
0.95 of the device. That is a real capacity ceiling. Batch 2 × accum 4 is the shape that works.
(2) The OOMs that looked like fragmentation earlier were partly a **reference-cycle leak**: the
Track B wrapper registers a forward hook bound to itself, so `del model; empty_cache()` leaves the
old 13.5 GB resident through a reload. `gc.collect()` between the two fixes it. Full detail, with
the silent-NaN-on-fully-masked-rows bug and the Megatron embedding wrapping bug, is **F18** in
`docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md`.

**What the smoke shows, and what it does not.** Generation on three held-out prompts built by the
harness produced well-formed citation-style completions (two of three named the gold article);
candidate scoring ran cleanly (five uniform candidates, best margin 3.48 nats); the generic LoRA
injector wrapped 128 transformer-engine linears with a bit-identical forward at B = 0, so the
three-tier weight stack of §4 is now mechanically available at 1.13B too. **This is plumbing
evidence only.** The 1.13B model has *not* been run through the 690-item held-out in scoring mode
with its own calibration, so no number here is comparable to §10's table. The near-zero final loss
on round-1e batches says the templated citation targets were memorised in one epoch, which is a
warning about the SFT set's diversity, not an accuracy claim.

**Next concrete step (for whoever continues):** add a Megatron backend flag to `evaluate.py` (the
adapter in `g0/generate_megatron.py` already exposes generate + candidate NLL), calibrate τ/δ on the
train dev slice for this checkpoint, and run B′-frozen and B′ on the 690. If 1.13B + harness does not
clear 370M + harness (recall 0.145–0.198) by more than the paired CI half-width, the scale lever is
weaker than §5.3 assumed and the retrieval ceiling (recall@4 = 0.77) becomes the binding constraint.

## 12. Round 2 design: pointwise grounded judgment (2026-09-13, afternoon)

**The contradiction, stated with numbers.** The harness's recall is bounded by what it can show
the model. BM25 recall@k on the 690 held-out (measured today, same store the harness uses):

| k | all | law_lookup | cite_to_title | citation_retrieval |
|---|---|---|---|---|
| 4 | 0.772 | 0.652 | 0.758 | 0.938 |
| 8 | 0.836 | 0.740 | 0.828 | 0.974 |
| 16 | 0.870 | 0.797 | 0.863 | 0.982 |
| 32 | 0.909 | 0.872 | 0.899 | 0.991 |
| 64 | 0.941 | 0.925 | 0.938 | 0.996 |

But one prompt at 370M is capped at 950 bytes (trained at 512 tokens = 512 bytes), which forces
k = 4 with 60-byte bodies, and even then the model picks the gold passage only 49% of the time it
is shown. Improving reliability (TRIZ parameter 27) worsens prompt length (4) and device
complexity (36). The contradiction matrix returns principles 15, 29, 28, 11 and 27, 35, 10, 34;
the ones that bite here are **1 segmentation**, **28 replace the mechanism** (a k-way reading
becomes a pairwise measurement), **27 cheap first stage** (BM25 proposes, the LM disposes),
**40 composite** (fuse LM margin with the BM25 rank prior) and **10 prior action** (train the
same judgment the harness makes at test time). Session entry logged in `.triz/session.jsonl`;
IFR score 2/4 (cheap, no new problems; not yet self-resolving).

**The design.** For each of k retrieved passages build one short prompt (header, title, body up
to 220 bytes, question) and score two fixed continuations: the passage's own canonical citation
and the abstain phrase. The margin `NLL(abstain) − NLL(cite)` ranks passages; the best margin,
optionally fused with `−λ·log(rank)`, is thresholded with the same dev-calibrated τ/δ rule as
before. Cost is linear in k with no prompt growth, so k = 32 is affordable and the ceiling moves
from 0.77 to 0.91. Selection becomes a binary judgment a 370M model can plausibly learn, and the
RSI loop can train exactly that judgment from its own gate-accepted pseudo-labels (positives =
accepted passage, hard negatives = the other retrieved passages), which is round 3.

**What is being run.** (a) Pointwise mode in `evaluate.py`, frozen weights and round-1 LoRA, k ∈
{8, 16, 32}, λ chosen on the dev slice; paired against B′ and B′(frozen). (b) A Megatron backend
in `model_io.py` so the 1.13B checkpoint from §11 goes through the same harness. (c) A CPU-only
BM25 tuning pass (field weights, abbreviation expansion, exact-header boosts) fitted on train,
reported once on held-out. Results are appended below as they land; nothing in this section is a
result yet.

### 12.1 Result (c): the first stage was the cheap half of the ceiling

The BM25 tuning pass (`prototypes/nyaya_ttt_rsi/retrieval_tuning.py`, Codex gpt-6-astra,
144 configurations searched on the train split only, 12 s of CPU) found that weighting the
`[act, section]` header and the section title six times the body, with a fixed boost when the
question names an explicit article/section that matches a header, lifts held-out recall from
0.772 to **0.984 at k = 4** (0.703 at k = 1, 0.987 at k = 8). I re-ran the winner on the 690
independently and got the same numbers. Raw: `runs/retrieval_tuning/results.json`.

Why so large, said plainly: Track B's questions are templated ("What does Constitution of India,
Article 5 provide?", "Which provision of X states: '<title>'?", "What is the subject of X, Article
5?"), so the header and title fields carry almost all the signal and the body was mostly noise
in the original score. That is a property of the evaluation set, not of legal retrieval in
general; the prototype's grounded-by-construction claim is unaffected, but anyone quoting recall
numbers for this set should say the retrieval half is nearly solved by field weighting alone.
The consequence for the model is that **gold_selected_when_shown becomes the whole game**: with
the gold passage on screen 98% of the time, every remaining miss is a selection or abstention
error, which is exactly what pointwise judgment and the contrastive round target. The pointwise
agent has been told to add tuned-store conditions so the two effects can be separated.

### 12.2 Interim result (a): selection accuracy with the gold on screen

Pointwise and scoring-mode runs on the 690 (`runs/pointwise_run1/`, per-item answers saved).
The number to read first is **pre-abstention selection accuracy** (selected passage == gold over
the 681 non-abstain items), because it does not depend on the abstention rule:

| condition | store | weights | k | selected == gold | law_lookup | cite_to_title | citation_retrieval |
|---|---|---|---|---|---|---|---|
| S4t_f | tuned | frozen | 4 | 0.021 | 0.000 | 0.000 | 0.062 |
| **S4t** | tuned | round-1 LoRA | 4 | **0.570** | 0.819 | 0.833 | 0.057 |
| P8t_f | tuned | frozen | 8, pointwise | 0.023 | 0.022 | 0.026 | 0.022 |
| P8t | tuned | round-1 LoRA | 8, pointwise | 0.559 | 0.819 | 0.833 | 0.026 |
| P32 | plain | round-1 LoRA | 32, pointwise | 0.361 | 0.586 | 0.493 | 0.004 |
| P8f / P16f / P32f | plain | frozen | pointwise | 0.022 / — / 0.004 | | | |

Three things follow. **First**, the round-1 LoRA is what makes the model a selector at all: the
frozen 370M picks the gold 2% of the time with four candidates on screen, far below chance,
which means it has a fixed positional or string preference rather than reading the passages.
**Second**, with the tuned first stage and the round-1 weights, selection is right 57% of the
time overall and 82–83% on two of the three kinds; the harness's ceiling has moved from
"retrieval" to one kind, `law_citation_retrieval` ("which provision states '<title>'"), where
the selector is at 6% even though the title is now in the prompt. That kind is the round-3
target. **Third**, pointwise scoring with an untrained judge does not beat multi-passage
scoring (P8t ≈ S4t), confirming §12's "prior action" point: the judgment has to be trained
before segmentation pays off, and the trained multi-passage selector already reaches the same
place at a quarter of the cost.

**A harness bug found on the way (F19).** The abstention calibration was fitted on "gold shown
vs not shown". With recall@4 at 0.98 the negative class on the dev slice has two items, the
balanced-accuracy grid degenerates, and the rule abstained on 72–82% of items where the gold
was on screen, collapsing post-abstention recall to 0.06 while the selector was right 57% of
the time. The fix, now being applied, calibrates on selection correctness (abstain when the
selected passage would be wrong), which is the decision the rule was always meant to make.
Post-abstention numbers for this round are reported in §12.3 once recalibrated.

### 12.3 Result (a), recalibrated: what the 370M can and cannot select

All numbers: 690 held-out, Track B's scorer, abstention calibrated on the train dev slice with
the corrected selection-correctness label (F19), rank-prior λ chosen on dev. Full table with
CIs and every paired test: `runs/pointwise_run1/report.md`. The scorer's headline
`citation_recall` counts only the 227 `law_citation_retrieval` items (the title kind);
`law_lookup` is scored by prefix match of the emitted body; `law_cite_to_title` is not scored.

| condition | store | weights | λ | pre-abst. selection, title kind | citation recall | precision | lookup exact-prefix | abstention correct | false abstain when shown |
|---|---|---|---|---|---|---|---|---|---|
| A (Track B closed-book, §10) | — | frozen | — | — | 0.004 | 0.004 | 0.059 | 0.556 | — |
| B′ (§10) | plain, k=4 | round-1 LoRA | 0 | 0.145 | 0.145 | 0.190 | 0.626 | 0.889 | 0.116 |
| B′(frozen) | plain, k=4 | frozen | 0 | 0.176 | 0.176 | 0.206 | 0.108 | 0.444 | 0.430 |
| **P16f** | plain, k=16 pointwise | frozen | 0.5 | 0.780 | **0.762** [0.703–0.813] | 0.801 | 0.167 | 0.778 | 0.357 |
| P32f | plain, k=32 pointwise | frozen | 0.5 | 0.780 | 0.736 | 0.803 | 0.192 | 0.889 | 0.396 |
| S4t_f | tuned, k=4 | frozen | 1.0 | 0.696 | 0.542 | 0.804 | 0.000 | 1.000 | 0.776 |
| **S4t** | tuned, k=4 | round-1 LoRA | 1.0 | 0.352 | 0.044 | 0.435 | **0.868** | 0.222 | 0.376 |
| P8t / P8t_f | tuned, k=8 pointwise | LoRA / frozen | 0 | 0.154 / 0.154 | 0.040 / 0.026 | — | 0.872 / 0.032 | 0.778 / 1.000 | 0.366 / 0.969 |

Paired: P16f vs B′(frozen) p = 1.9e-33, vs B′ p = 0.006, bootstrap CI on the difference
[0.20, 0.27] and [0.02, 0.11]; S4t vs B′ on lookup is a different metric and was not paired.

**Reading it honestly.**
- **The 0.76 on the title kind is the retriever answering.** P16f's dev-chosen λ = 0.5 makes the
  rank prior dominate margins of ~0.1 nats; the frozen model's own pointwise judgment selects at
  8% (P8f, λ = 0). Plain BM25 recall@1 on that kind is 0.780, identical to P16f's pre-abstention
  selection. The model contributes an abstention veto (cited 95%, precision 0.80), nothing more.
  It is grounded by construction and a real improvement in what the system emits, but it is not
  evidence of model capability, and the agent's summary overstated it.
- **The round-1 LoRA turned the model into a selector on the number kinds and broke it on the
  title kind.** With the tuned store, S4t selects the gold pre-abstention at 0.82 (lookup) and
  0.83 (cite_to_title) versus 0.35 on the title kind, and the lookup exact-prefix score moves
  0.626 → 0.868. The plausible mechanism is string-matching the section number that appears
  verbatim in those two question templates; title matching is the capability the 370M does not
  have after one LoRA epoch, and the LoRA made it worse than frozen everywhere it was measured.
- **Post-hoc composition, flagged as such.** Routing by question template (title kind → P16f,
  everything else → S4t; the abstain items look like lookups and therefore go to S4t) and
  re-scoring the saved per-item answers gives citation recall 0.762, precision 0.801, lookup
  exact-prefix 0.868, grounded 1.0, but abstention correctness 0.222. The routing rule was
  chosen after seeing held-out per-kind results, so this is an upper bound to be validated on
  dev, not a result (`runs/pointwise_run1/routed_S4t_P16f_answers.jsonl`).
- **Abstention is now the weakest link, and it is the MVP's core claim.** With near-perfect
  retrieval, an absent provision still yields four header-similar passages, and the LoRA model
  confidently cites one (7 of 9). The dev slice used for calibration contains no genuine
  absent-gold items, so the rule was never fitted on the case that matters. Fix in flight for
  round 3: synthetic absent-gold dev negatives (gold removed from the candidate list), mirroring
  `grounded_data.py`'s synthetic-abstain construction.
- **Pointwise scoring did not pay off on its own** (P8t ≈ S4t, p = 1.0). Segmentation only
  helps once the judgment is trained; with the tuned store the multi-passage selector reaches the
  same place at a quarter of the cost. Round 3 trains the judgment; §12's design stands but its
  order was wrong: first stage, then selector training, then segmentation.

**What is running now (round3 agent):** the 1.13B checkpoint through the same harness (does
scale fix title matching?), then an RSI round 3 on the 370M with the title kind oversampled and
shuffled-order copies, through the gate, with the absent-gold calibration; round 4 if it compounds.
