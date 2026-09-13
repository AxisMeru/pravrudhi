# Fixes and findings for the main sessions (from the isolated `ttt-lab` work, 2026-09-13)

Everything below was done in an isolated container (`ttt-lab`, started from the same
`rtx5090-train:latest` image with an 8 GiB RAM cap, my worktree mounted at `/lab`,
`/fusion-project` and `~/projects/prabhasa-samskrutam` mounted **read-only**). Nothing here
touched the running `rtx5090-train`, the two engine containers, or any repo other than this
branch. Each item says what is broken, what fixed it, and what the owning session should do.

> Numbering note: sections were appended by several agents in parallel; F3/F4 were never used and the order below is by appending agent, not by number. The G0 result is F17.

## F1. `peft` is unusable in the `rtx5090-train:latest` image (`torchao` version pairing)

- **Symptom:** `from peft import get_peft_model; get_peft_model(model, LoraConfig(...))` raises
  `ImportError: Found an incompatible version of torchao. Found version 0.11.0+git, but only
  versions above 0.16.0 are supported` (peft 0.19.1 → `peft/import_utils.py:143`). Any LoRA
  recipe using peft in that container is dead on arrival; only hand-rolled adapters work.
- **What does not work:** `pip install -U torchao` (pulls the newest torchao, which imports
  `torch.nn.functional.ScalingType` and needs torch ≥ 2.11; the image ships torch
  `2.8.0a0+...nv25.06`). Import of torchao itself then fails.
- **What works (verified in `ttt-lab`):** `pip install 'torchao==0.16.0'`. torchao logs
  "Skipping import of cpp extensions due to incompatible torch version" (its CUDA kernels are
  off, which peft's plain LoRA path never needs) and `get_peft_model` succeeds.
- **Owner action (Track B / whoever maintains the image):** add `torchao==0.16.0` to the
  image's Dockerfile (`~/rtx5090setup/docker/Dockerfile`) or run the pin once inside
  `rtx5090-train`. Check first that nothing in that container depends on torchao 0.11's cpp
  kernels (quantized inference would; plain SFT/LoRA does not).

## F2. Track B's model cannot load in `rtx5090-train:latest`; only `prabhasa/nemo-5090:26.02` has the stack

- **Symptom:** `import mamba_ssm` and `import megatron.core` both fail in `rtx5090-train:latest`
  (the container that has been running for two days with the HF cache and `/fusion-project`
  mounted). NemotronH (both the 370M custom-loop line and the 1.13B Megatron line) needs
  `mamba_ssm`; the 1.13B loader also needs Megatron-Core.
- **What works:** `prabhasa/nemo-5090:26.02` (torch `2.10.0a0+...nv25.11`, `mamba_ssm 2.3.1`,
  `megatron.core`, `transformers 4.57.6`, `peft 0.13.2` + `torchao 0.14.0` — and in *this* image
  peft imports cleanly, so F1 is specific to the `rtx5090-train` image).
- **Owner action:** any Track B doc that tells a reader to "use the training container" should name
  the image; `rtx5090-train` is a general-purpose box, not the Track B stack. The isolated
  `ttt-lab` container used for this prototype is `prabhasa/nemo-5090:26.02` with the worktree at
  `/lab`, Track B repo at `/trackB` (ro), `~/.local/share/prabhasa-samskrutam` at `/trackB-local`
  (ro), `--memory 8g --memory-swap 8g` (measured 370M/1.13B RSS is ≈3–5 GiB per Track B's own G0
  notes, so 8 GiB is a cap that fails fast instead of thrashing the host).

## F5. Loading `m7_retry_checkpoint.pt` with `map_location="cpu"` OOM-kills in an 8 GiB container

- **Symptom:** `torch.load(ckpt_path, map_location="cpu", weights_only=False)` on the 4.2 GB
  `m7_retry_checkpoint.pt` blob — the exact pattern used by both
  `prabhasa.infrastructure.ml.inference.NemotronHRunner` (which loads to `self.device`, so it
  is only at risk when that device is `"cpu"`) and `scripts/m7/dry_run_sft.py::load_source_model`
  (which always hardcodes `map_location="cpu"`) — gets killed (exit 137) inside the `ttt-lab`
  container (`--memory 8g --memory-swap 8g`). The unpickle stages the full state dict as host
  RAM tensors before any device transfer; building a second, CPU-resident `NemotronH` (353M
  params) alongside that staged copy pushes past the cap even before `.to(device)` runs.
- **What works (verified in `ttt-lab`, used in `prototypes/nyaya_ttt_rsi/model_io.py::load_model`):**
  `torch.load(ckpt_path, map_location="cuda:0", weights_only=False, mmap=True)` — loading
  straight to the CUDA device skips the CPU staging buffer for tensor storages, and `mmap=True`
  (torch ≥ 2.1; confirmed present in `prabhasa/nemo-5090:26.02`'s torch 2.10) maps the file's
  storages instead of reading them fully into memory up front. Verified end-to-end: loads,
  generates the full 690-item `law_qa_heldout_v3.jsonl` set, and reproduces Track B's own
  published M7 "after" numbers (citation recall/precision 1/227, abstention 5/9,
  `law_lookup.prefix_similarity_mean` 0.0587 vs the recorded 0.0584) inside the 8 GiB cap
  (peak VRAM 5.46 GiB, host RAM never spiked).
- **Owner action (Track B):** `scripts/m7/dry_run_sft.py::load_source_model` always loads to
  `"cpu"` regardless of the caller's target device — anyone re-running it (or copying its
  pattern) inside a RAM-capped container should switch that call to
  `map_location=<target_device>, mmap=True` when the target is CUDA, or otherwise ensure the
  host has enough free RAM to stage the full checkpoint (~4.2 GB) plus a CPU-resident model
  copy (~1.4 GB fp32) simultaneously.

## F8. `ttt.LoRALinear` cannot wrap a Mamba2 mixer's `in_proj`/`out_proj` on this model -- `mamba_ssm`'s fused kernel reads `.weight` directly

- **Symptom:** injecting LoRA at `blocks.<i>.mixer.{in_proj,out_proj}` for any of the 21 Mamba2
  blocks (per `model_io.linear_module_names`'s own listing, which explicitly names these as
  candidates) and then running a forward pass raises
  `AttributeError: 'LoRALinear' object has no attribute 'weight'` from
  `mamba_ssm/modules/mamba2.py:197` (`outproj_weight=self.out_proj.weight`). `mamba_ssm`'s
  fused CUDA path reads `self.out_proj.weight` as a raw tensor rather than calling
  `self.out_proj(x)` as an ordinary submodule -- any module-wrapping LoRA approach (not just
  `ttt.LoRALinear`) is incompatible with these two projections on this architecture, only a
  weight-merging or hook-based LoRA scheme would work.
- **What does work:** the 3 attention blocks' `blocks.<i>.mixer.{qkv,out_proj}` --
  `CausalSelfAttention.forward` (`/trackB/scripts/m2/train_130m.py`) calls
  `self.qkv(x)` / `self.out_proj(x)` as normal module calls, so `LoRALinear` wraps them fine.
- **What `prototypes/nyaya_ttt_rsi` does about it:** `evaluate.py`'s
  `attention_lora_target_regex(model)` builds the injection regex from
  `model_io.attention_block_indices(model)` and restricts it to `mixer.{qkv,out_proj}` on
  those indices only (6 Linears total on the 370M checkpoint, not 48) -- conditions C/D's
  LoRA capacity is therefore attention-projections-only, not "attention q/o and mamba
  in/out" as the original module contract assumed before this was discovered by running it.
- **Owner action (ttt-gate / whoever extends `ttt.py`):** either accept that Mamba2 mixer
  projections are out of scope for this wrapper-based `inject_lora`, or implement a
  weight-merge-at-forward-time variant (patch `.weight`/`.bias` in place via a context
  manager around the fused call) if Mamba-mixer LoRA is ever actually needed.

## F9. `ttt.adapt`'s default `loss_fn` cannot train on this model: it detaches gradients and its `model_io` import never resolves

- **Symptom:** with no explicit `loss_fn` passed to `ttt.adapt`, `_default_loss_fn`
  (`ttt.py`) does `import model_io` (unqualified) inside a package (`prototypes.nyaya_ttt_rsi`)
  -- this never resolves to the sibling module (it would need `from . import model_io` or the
  fully-qualified name), so the `try/except ImportError` always takes the fallback branch.
  That fallback calls `model(input_ids=ids_t, labels=ids_t)`, a HuggingFace-style signature
  this model's `NemotronH.forward(tokens, boundary, roles)` does not have --
  `TypeError: NemotronH.forward() got an unexpected keyword argument 'input_ids'`.
  Separately, even if the `model_io` import were fixed, the primary branch calls
  `model_io.sequence_nll(...)`, which returns a plain detached `float` (`.item()`), then
  re-wraps it with `torch.as_tensor(loss_val, ...)` -- a leaf tensor with no `grad_fn`, so
  `loss.backward()` inside `adapt`'s training loop would raise (no gradient can reach the
  LoRA parameters) even once the import is fixed.
- **What `prototypes/nyaya_ttt_rsi` does about it:** `evaluate._tensor_nll_loss(model, tok,
  prompt, continuation)` duplicates `model_io.sequence_nll`'s exact forward-pass math (same
  byte-level teacher-forcing, same prompt-byte exclusion) but returns the tensor still
  attached to the autograd graph. `evaluate._adapt` and `loop.consolidate` always pass this
  in explicitly as `loss_fn`, never relying on `ttt.adapt`'s default.
- **Owner action (ttt-gate / loader):** fix `_default_loss_fn`'s import to `from . import
  model_io` (or accept the caller must always pass `loss_fn` and drop the fragile default
  entirely), and if the primary branch is kept, have it call a tensor-returning variant of
  `sequence_nll` rather than re-wrapping the already-`.item()`'d float.

## F10. A second `ttt.inject_lora` pass cannot "stack" an ephemeral LoRA on top of an already-injected persistent one

- **Context:** the 2026-09-13 pivot plan (main session) asked for conditions B'/C' where
  "the ephemeral LoRA stacks on the persistent one, reset per query" -- i.e. two independent
  `LoRALinear` layers at the same target projections, one trained (persistent, Phase 2) and one
  ephemeral (per-query, reset every time).
- **Why it does not work as literally specified:** `ttt.inject_lora(model, target_regex, ...)`
  finds targets via `isinstance(module, nn.Linear) and pattern.search(name)` over
  `model.named_modules()`. After the FIRST injection, the target dotted names (e.g.
  `blocks.7.mixer.qkv`) now hold a `LoRALinear`, not an `nn.Linear` -- the wrapped original
  `nn.Linear` still exists but as `blocks.7.mixer.qkv.base`, a different dotted name. A second
  `inject_lora` call with the same (or any) target regex therefore matches nothing at those
  positions; there is no supported way to inject a second LoRA layer on top of a first one
  without either changing the target regex to explicitly match `\.base$` (untested, and
  `LoRALinear.forward` calls `self.base(x)` directly, not via a route the outer wrapper's own
  `x @ A^T @ B^T` term could compose with meaningfully) or modifying `ttt.py` (out of scope --
  owned by ttt-gate).
- **What `prototypes/nyaya_ttt_rsi`'s Phase 3 driver
  (`runs/rsi_run1/_phase3_driver.py`) does instead:** conditions B'/C' reuse the SAME `LoRALinear`
  set that Phase 2 trained (`evaluate.persistent_lora_target_regex`, r=16/alpha=32, not the r=8
  attention-only set used by plain conditions C/D) as `evaluate.run_condition`'s `loras` argument.
  `run_condition`'s own machinery already does exactly the intended thing without a second layer:
  it snapshots the current (persistent) LoRA state once as `base_snap`, runs `ttt.adapt`'s
  ephemeral gradient steps FROM there per query, and restores to `base_snap` after every query --
  functionally identical to "ephemeral TTT stacked on a persistent base", just implemented as
  continued fine-tuning of one LoRA object rather than two composed ones. The steps/lr instructed
  for the ephemeral layer (steps=4, lr=1e-3) are used as given; only its LoRA rank is not
  independently 8 (it inherits Phase 2's r=16, since it is the same object).
- **Owner action:** if a true two-layer stack is ever required (e.g. to keep the ephemeral
  adaptation's rank independent of the persistent one's), `ttt.inject_lora` would need either a
  `target_regex` variant matching `\.base$` with `LoRALinear.forward` updated to route through the
  wrapped `LoRALinear.base` as if it were the frozen `nn.Linear` (it already is, structurally --
  `self.base(x)` on a `LoRALinear` works today, so the fix may be as small as allowing
  `inject_lora` to wrap a `LoRALinear.base` when it is itself an `nn.Linear`, i.e. relaxing its
  `isinstance` check to look one level through an existing wrapper), or an explicit multi-adapter
  design.

## F11. No training target had a stop terminator -- byte model with no EOS never learns to self-terminate; Track B's own SFT data has the same property

- **Symptom (round 1's mode collapse, 2026-09-13 pivot):** grounded conditions B'/C' -- the
  first ones served from a persistent LoRA actually fine-tuned to answer in the grounded-prompt
  format -- looked internally contradictory: abstention 9/9 "correct" AND `abstain_on_miss`
  227/227 (both apparently perfect) alongside `hallucinated_citation_rate` 0.74 and
  `grounded_rate` only 0.26. Raw generations explained it directly, e.g.
  `'Not found in the provided corpus: no "Article 324 (Constitution of India).'` -- the model
  correctly abstains, then keeps generating (nothing stopped it) and fabricates a
  citation-shaped continuation that `retrieval.parse_answer` correctly detects as a citation.
  `retrieval.grounded()` requires EVERY citation in an answer to be among the shown passages,
  so that one fabricated tail alone flips an otherwise-correct or correctly-abstained answer to
  `grounded=False`.
- **Root cause:** round 1's `grounded_data.py` built every training target (citation AND
  abstain) with no trailing terminator at all. This model's byte tokenizer has no EOS token and
  `evaluate.generate`/`model_io.generate` always runs the full `max_new_tokens` budget
  regardless (`evaluate.STOP_STRINGS` truncation is applied POST-HOC, after generation
  completes -- it can only cut a stop string the model actually produced). Since training never
  showed the model any token sequence that comes right after a correct answer, there was
  nothing to teach it to emit `"\n\n"` (or any of `STOP_STRINGS`) there, so generation
  continues unconstrained into the base model's pretrained continuation habits after every
  single answer, citation or abstain alike.
- **This is not specific to the harness's synthetic data.** Track B's own `law_v3` SFT targets
  (`/trackB/data/sft/law_v3_train.jsonl`, `law_qa_heldout_v3.jsonl`) have the identical
  property -- no terminator appended, and `scripts/m7/generate.py`'s own batched greedy decode
  (mirrored by `model_io.generate`) has no stop-string or EOS handling either, just a fixed
  per-kind `max_new_tokens` cap. This is very likely why Track B's own M7 eval report's
  `law_lookup.citation_reached` was only 25/227 in the reproduced condition-A baseline
  (`runs/baseline_A/report.json`) even on CLOSED-BOOK generation where the model has nothing
  to hallucinate FROM except its own training distribution: with `max_new_tokens=256` fixed and
  no terminator ever trained, generation for `law_lookup` almost always overruns or underruns
  the actual citation's position in the target text, landing on an arbitrary cut point that
  usually is not the citation line -- a property of the training recipe's lack of an explicit
  stop signal, not of retrieval or model capacity.
- **What `prototypes/nyaya_ttt_rsi` does about it (round 1b):** `grounded_data.py` now appends
  `TARGET_STOP_SUFFIX = "\n\n"` to every training target (after the harness's own groundedness
  gate checks the raw, unsuffixed target, so gate semantics are unaffected), and
  `build_dataset`'s stats block now asserts
  `target_ends_with_stop_suffix_rate == 1.0` as a hard harness-gate check on its own output, so
  this specific regression cannot silently recur.
- **Owner action (Track B):** consider appending an explicit, consistent terminator (e.g. a
  literal `"\n\n"` or a dedicated sentinel byte) to every SFT target in future `law_v3`-style
  data generation, and have `scripts/m7/generate.py` (and any caller of
  `model_io.generate`/its own `batched_greedy_decode`) apply a matching stop-string truncation
  post-generation -- the same two-sided fix (train the terminator, then look for it at eval
  time) applied here. Without it, any fixed `max_new_tokens` cap is measuring "where generation
  happened to be cut off", not "what the model considers its answer".

## F12. Greedy free generation is structurally biased toward a single short, fixed answer string -- the "greedy-decoding trap"

- **Context:** round 1b (after F11's terminator fix) passed its regression-probe gate
  (probe_delta_rel 5.3%, under the 15% threshold) but FAILED the in-sample sanity check:
  citation_hit_rate 0.0/30, spurious_abstain_rate 29/30 -- the consolidated model abstained on
  almost every training prompt, including ones whose gold passage was shown in context.
- **Diagnosis (main session, confirmed by the in-sample loss curve showing no training
  instability -- epoch means 0.292/0.335/0.253, nothing runaway):** greedy decoding picks the
  argmax byte at EVERY position independently, one byte at a time, with no lookahead to total
  sequence likelihood. The abstain target is a single fixed string ("Not found in the provided
  corpus") shared by 100% of the ~330 abstain examples (12.8% of training); its first byte 'N'
  therefore accumulates a large, concentrated probability mass at position 1. The ~2,250
  citation targets are structurally diverse (different acts, different section numbers, being
  drawn from ~2,000+ distinct provisions), so their combined first-byte mass is spread thin
  across many different starting bytes ('A', 'S', arbitrary `law_lookup` provision-text starts,
  etc.). At byte 1, argmax can therefore favor 'N' (abstain) even for a prompt whose correct
  citation continuation has strictly higher TOTAL sequence likelihood than the abstain
  continuation -- greedy decoding never compares total sequence probability, only the
  per-position conditional. This is a structural property of any single fixed low-entropy
  target string competing against diverse alternatives under greedy decoding, independent of
  how well the model was actually trained; it was masked in round 1 only because the missing
  terminator (F11) produced a different, also-broken symptom (hallucinated continuations) before
  this failure mode could even be observed cleanly.
- **This applies to Track B's own inference path too.** `scripts/m7/generate.py` /
  `model_io.generate`'s batched greedy decode has the exact same one-byte-at-a-time argmax
  structure, and Track B's own `law_v3` training data has a real, non-synthetic `law_abstain`
  class (80 train / 9 heldout records) using a fixed abstention phrasing -- any Track B eval or
  downstream use of closed-book or grounded generation on this model inherits the same
  structural risk of the model over-abstaining (or under-abstaining, depending on which
  candidate happens to have the shorter/more probable prefix) whenever answer classes have very
  different target-string entropy.
- **What `prototypes/nyaya_ttt_rsi` does about it:** `evaluate.py` adds a "scoring mode"
  (`build_candidates`, `score_candidates`, `run_condition_scoring`) that sidesteps greedy
  decoding entirely for the grounded conditions: build one full candidate answer per shown
  passage (canonical citation format matching what `score_law_qa.py` substring-matches, or the
  passage text + citation line for `law_lookup`) plus the abstain phrase, score each candidate's
  MEAN PER-BYTE NLL over its own full continuation with `model_io.sequence_nll`, and pick the
  argmin -- this compares whole-sequence likelihood, not per-position argmax, so it cannot fall
  into the greedy trap. It is also grounded by construction (every candidate is either a shown
  passage's citation or the abstain phrase), so `grounded_rate` is always 1.0 in this mode and
  the informative metrics become which candidate wins and by what margin.
- **Owner action (Track B):** if closed-book or grounded generation ever needs to choose between
  answering and abstaining (or between semantically distinct answer classes with very different
  target-string entropy), consider scoring full candidate continuations by sequence likelihood
  rather than relying on greedy free generation, especially when one class (like abstention) uses
  a single fixed phrasing -- greedy decoding's byte-by-byte argmax is not equivalent to "the
  model's most likely answer" whenever candidate classes differ this much in string diversity.

## F13. `Passage` carried no section TITLE, so a forced-in gold passage was observationally identical to a random one for 2 of 3 citation kinds

- **Context:** round-1/1b's SFT runs both collapsed to near-universal abstention even under
  scoring mode (F12's fix). Diagnosed by the main session: `law_citation_retrieval` asks
  `'Which provision states: "<TITLE>"?'` and `law_cite_to_title` asks for the title outright, but
  `retrieval.PassageStore` built `Passage.text` from the `law_lookup` record's BODY only -- the
  section TITLE (a separate field, present as its own `law_cite_to_title` record for the same
  (act, section)) was never part of a passage's rendered text. Verified directly: the string
  "Citizenship at the commencement of the Constitution" (Article 5's title) appears nowhere in
  Article 5's body text. A forced-in gold passage was therefore observationally IDENTICAL to an
  irrelevant one for those two kinds -- the training label was uncorrelated with anything visible
  in context, and the one thing that WAS a reliable, low-loss training signal (the fixed abstain
  string) won.
- **Fix (`retrieval.py`):** `Passage` gained a `title` field, populated from the matching
  `law_cite_to_title` record (present for all 2,269 corpus passages, across both splits); BM25
  now indexes citation + title + body; `build_grounded_prompt` renders `[<act>, <section>]
  <title>. <body>`; truncation (`evaluate.truncate_passages`) only ever shortens `.text`, so the
  title is never cut. Recall jumped: recall@1 unmeasured-before -> 0.567, recall@3 0.66 -> 0.736,
  recall@5 0.73 -> 0.793 (measured on the real 690-item held-out set).
  `grounded_data.py`'s `build_dataset` now hard-asserts `n_title_missing_in_context == 0`
  (every non-abstain training example's gold title must appear verbatim in its own context).
- **Disclosure:** a held-out item's `law_cite_to_title` title is now part of the shared corpus
  (retrievable for ANY query, not just its own) -- the same decision already made for
  `law_lookup` bodies (the store already pooled both splits' bodies before this fix); section
  headings, like section bodies, are part of the statute text itself. Recorded here rather than
  left implicit.
- **Still not sufficient on its own** -- see F14: round-1c (title fix + 1 epoch, matching
  round 1's epoch count) still failed the scoring-mode sanity check identically to round-1b.

## F14. Single-epoch SFT with one exact-duplicate low-entropy target repeated many times out-memorizes many distinct once-seen targets, independent of context support

- **Context:** after F12 (scoring mode) and F13 (titles) were both fixed and verified, round-1c's
  in-sample scoring-mode sanity check STILL failed identically to round-1b: citation_hit_rate
  0/30, spurious_abstain_rate 30/30, on a model whose regression probe barely moved
  (probe_delta_rel 0.0004). This is not a decoding artifact (F12) or a missing-signal artifact
  (F13) -- both were independently confirmed fixed on this exact run.
- **Diagnosis, from the raw per-candidate NLLs on one training example** (id
  `law_citation_retrieval:Bharatiya Nyaya Sanhita:Section 330`, title
  "House-trespass and house-breaking.." CONFIRMED present in its own context): the correct
  citation candidate scored NLL 0.218, a wrong shown passage's citation scored 0.216/0.537, and
  the abstain candidate scored NLL 0.0037 -- roughly 60x lower (higher likelihood) than the
  correct answer, not a close call. Root cause: the abstain target is the exact SAME 35-byte
  string (`"Not found in the provided corpus\n\n"`) repeated byte-for-byte identically across
  330/2,578 training examples (12.8%), while each citation target is a DISTINCT string appearing
  exactly ONCE. A single epoch of AdamW over single-example steps drives a many-times-repeated
  identical short target toward near-zero loss (rote memorization via repetition) far faster than
  any individual once-seen diverse target can be learned to a comparable degree -- this is a
  structural property of the TRAINING DATA'S SHAPE (repetition count x target diversity), not of
  decoding, grounding signal, or model capacity. It would recur with any fixed abstention phrase
  used across many examples in a single-epoch, per-example SFT regime, regardless of how well
  each individual citation example's context supports its own answer.
- **Not yet fixed --standing by for direction** (options recorded for whoever picks this up:
  diversify the abstain target text per example so it is not one exact repeated string;
  replicate/upweight citation targets so they receive comparable repetition exposure; more
  epochs specifically for citation targets while holding abstain exposure fixed; or a much
  lower abstain_frac for a single-epoch regime).
- **Applies beyond this harness:** any SFT recipe (Track B's own `law_v3`-style data included)
  that mixes one exact-duplicate low-entropy answer class against many high-entropy,
  seen-once answer classes should expect the duplicate class to become disproportionately
  likely under the trained model's own likelihood, independent of context -- this is a general
  property of cross-entropy training on an imbalanced-by-diversity (not just imbalanced-by-count)
  target distribution, not specific to abstention or to this checkpoint.

## F15. TOTAL-NLL candidate selection is itself length-biased once the abstain string is removed -- it favors SHORTER wrong passages over LONGER correct ones for `law_lookup`

- **Context:** round-1d removed the abstain string from training and from the candidate set
  entirely (F14's fix), selecting the shown passage with the lowest TOTAL (sum-over-bytes) NLL
  as coordinator-specified (to stop a short fixed string from winning purely on being short --
  see F14). In-sample scoring-mode citation_hit_rate still capped at 0.40 (1 epoch) / 0.433
  (2 epochs), both below the 0.5 gate.
- **Diagnosis, from raw per-candidate scores** (id
  `law_lookup:Indian Penal Code:Section 367`, round-1d 2-epoch state): the GOLD passage's
  candidate had mean-per-byte NLL 0.4582 -- the LOWEST (best) of all three shown passages --
  yet lost selection because its TOTAL NLL (195.6, from a longer body) exceeded a wrong,
  shorter passage's TOTAL NLL (127.3). `law_lookup` candidates are full passage bodies whose
  lengths vary widely (unlike the citation-format candidates for the other two kinds, which are
  all near-uniform ~30-40 byte strings); TOTAL NLL, being length-weighted, systematically favors
  the shorter candidate regardless of which one the model is actually more confident in
  per-byte. Confirmed at scale: switching the same round-1d 2-epoch state's selection statistic
  from TOTAL to MEAN raised overall citation_hit_rate 0.433 -> 0.467, entirely from
  `law_lookup` (0.25 -> 0.375); `law_citation_retrieval` (0.556) and `law_cite_to_title` (0.462)
  were unchanged by the switch, exactly as expected since their candidates don't vary much in
  length.
- **Neither statistic is bias-free on its own:** TOTAL is biased toward short candidates (this
  finding); MEAN is biased toward a candidate that is easy to memorize as an exact repeated
  string regardless of context (F14's finding, when an abstain-like candidate is present).
  Removing the pathological candidate (F14) does not remove the general length-normalization
  problem -- it just changes which bias is active.
- **Status:** neither 1 nor 2 epochs of round-1d cleared the 0.5 in-sample gate under either
  statistic (best observed: 0.467 mean-based, 2 epochs). `evaluate.score_candidates` now takes
  a `by` parameter (`"total"` default, `"mean"` available) and always reports both regardless of
  which one selects, so this trade-off is inspectable rather than silently baked into one
  hard-coded statistic. Not resolved further without direction -- a length-normalized statistic
  (e.g. per-byte NLL is already that, but calibrated against candidate-specific priors) or a
  kind-specific selection rule (mean for `law_lookup`, total elsewhere) are candidates for a
  next attempt.

## F16. A specified compact-context budget (k=5, uncapped titles, 900 bytes) was arithmetically impossible; the corrected config fixed F15's law_lookup length bias and produced a working round (round-1e)

- **Context:** after F15 (candidates made uniform-length to remove `law_lookup`'s length-selection
  bias), the follow-up plan specified a compact context: k=5 passages, body truncated to 120 bytes,
  title uncapped, total prompt budget <=900 bytes. Measured on 100 real held-out items before
  spending any GPU time: ALL 100 exceeded the budget (948-1622 bytes). Root cause, measured
  directly: titles are real section headings, not short tags -- mean 54 bytes, median 51, p90 91,
  max 190 bytes (n=2269, every corpus passage). A single rendered block
  `"[act, section] title. body[:120]"` averages ~189 bytes; five of those (k=5) alone average
  ~945 bytes, before the ~154-byte original instruction line, the question, and separators are even
  added. The originally specified budget and passage count were incompatible with uncapped, real
  title lengths -- not a bug, an arithmetic mismatch caught before wasting a training run on it.
- **Corrected config (round-1e, `evaluate.PROMPT_CONFIG`):** a <=40-byte instruction ("Cite the
  correct provision below.", 33 bytes -- no in-prompt abstention wording, since abstention is now a
  calibrated harness decision, not something the model is asked to produce, see F14), k=4
  (recall@4 with titles = 0.772, measured), body capped at 60 bytes AND title capped at 90 bytes
  (both cut at the last space, never mid-word -- `evaluate.truncate_at_space`), 950-byte total
  budget. Verified on 200 real held-out items: mean prompt 728 bytes, max 944, zero drops, zero
  budget-assertion failures.
- **Last-resort fallback, added because "rare" still needs a defined behavior:**
  `evaluate.render_prompt` drops the lowest-ranked (last) shown passage and rebuilds if a prompt
  still exceeds the budget after truncation, repeating until it fits or one passage remains;
  `evaluate.assert_prompt_budget` hard-asserts afterward (a failure there means even a single
  passage overflowed, a genuine anomaly). Measured rate on the round-1e held-out run: 1/690
  (0.14%) -- rare, as expected.
- **Single shared config, enforced by identity:** `grounded_data.PROMPT_CONFIG is evaluate.PROMPT_CONFIG`
  (the exact same object, not a copy) is asserted at import time in `grounded_data.py` and checked by
  a standing test (`tests/test_evaluate.py::test_grounded_data_shares_the_same_prompt_config_object`),
  so training-time and eval-time prompt construction cannot silently drift apart -- both go through
  `evaluate.render_prompt`.
- **Outcome:** round-1e (uniform citation-string targets for every kind, 2 epochs, lr 3e-4, r16)
  passed every gate this pivot required: SFT regression-probe gate (probe_delta_rel 0.075, under
  0.15), in-sample scoring-mode sanity (0.767 hit rate vs a 0.5 bar and 0.25 chance; frozen baseline
  0.400), and abstention calibration (balanced accuracy 0.843 on a 500-example train-split dev
  slice, vs 0.580 for the frozen model). Held-out (690 items): gold-citation-selected rose from
  0.0029 (condition A, frozen closed-book) to 0.2594 (condition B', McNemar p=1.07e-50);
  `law_lookup` prefix-similarity-to-target rose from 0.059 to 0.626; abstention correctness rose
  from 0.556 to 0.889. Ephemeral TTT (condition C') did NOT beat B' (0.2507 vs 0.2594,
  McNemar p=0.345, not significant) -- reported plainly rather than treated as a win.

## F6. `load_megatron_blob`'s default config resolution can silently pick the wrong tree under a two-mount container layout

- **Symptom (found while writing the G0 allocator-fragmentation run plan, not yet hit in a
  real run):** `scripts/m4/eval_adapter.py::load_megatron_blob`'s `config_path=None` default
  calls `_find_repo_config(blob_path)`, which walks up from the **checkpoint's own path**
  looking for `configs/train/nemotron_h_1b.yaml` — it never looks relative to `--repo-root`.
  `scripts/g0/sft_megatron_batched.py` passes `args.config` straight through, so if a caller
  omits `--config`, resolution depends entirely on where `--checkpoint` happens to sit.
- **Why this matters for a container run specifically:** the G0 OOM test plan
  (`G0-OOM-RUN-PLAN.md`, this directory) mounts the checkpoint's real location
  (`~/fusion-project`, a full separate mirror of the repo, confirmed by `find`/`ls` to
  contain its own `configs/train/nemotron_h_1b.yaml`) read-only at `/fusion-project`, and the
  actual Track B git checkout (branch `h-ord/phase1`) read-only at `/trackB`. An unset
  `--config` would resolve against `/fusion-project`'s mirrored config, not `/trackB`'s
  checked-out one — silently, no error, no log line naming which tree was used. Verified by
  `diff` that the two `nemotron_h_1b.yaml` files are byte-identical right now, so this has not
  caused a wrong-config load yet, but nothing enforces that they stay in sync (the mirror is
  a separate, unversioned copy), and a future edit to `/trackB`'s config on `h-ord/phase1`
  would silently not apply to any run that omits `--config`.
- **What works:** always pass `--config` explicitly (e.g.
  `--config /trackB/configs/train/nemotron_h_1b.yaml`) in any container invocation that
  mounts the checkpoint's real (fusion-project) location separately from the repo checkout.
- **Owner action (Track B):** either make `sft_megatron_batched.py` require `--config`
  (drop the `default=None` convenience) when `--repo-root` and `--checkpoint` resolve to
  different filesystem trees, or have `load_megatron_blob` prefer a `repo_root`-relative
  config path when one is available instead of always deriving it from the checkpoint path.

## F7. Host RAM was the binding constraint, not the GPU (2026-09-13 ~11:45 BST)

- **Observed:** `free -g` = 30 total / 26 used / 3 available, swap 7/7 full (no active paging yet),
  with the GPU idle. The RAM was ~30 idle `mcp/server.py` processes (~0.5 GiB each: the
  `pratyabhijna-creative-engine` plugin server spawned once per desktop session, plus remote
  plugin servers) and the four `cli-*` team screens (`claude --model sonnet`, ~0.4 GiB each) plus
  `cli-lead`. This is the same shape as the 2026-09-10 collapse: many idle sessions, then one real job.
- **Action taken (operator instruction "all main sessions/agents/rsi heartbeat loops stopped for
  this… recover what is needed"):** quit `cli-watchdog` first (it respawns the team), then the
  `cli-web`/`cli-trackA`/`cli-trackB`/`cli-studio` screens. `cli-lead` and the desktop sessions were
  left alone. Result: 10 GiB available. `pravrudhi-heartbeat.service` was already `failed`
  (not running); `pravrudhi-gateway.service` and the two engine containers were left running.
- **Owner action:** when the team is restarted (`deploy/agents/cli-watchdog.sh`), budget ~0.5 GiB
  per session for the plugin MCP servers and consider not loading `pratyabhijna-creative-engine`
  in the headless CLI seats — it is a creativity tool no build agent uses.

## Cost log for the delegated work (for `pravrudhi-agent-cost-control`)

| worker | route | task | tokens | wall |
|---|---|---|---|---|
| retrieval + stats | codex `gpt-6-astra`, effort medium | retrieval.py, stats.py, 16 tests, recall@k | 27,014 | ~6 min |
| report renderer | opencode `alibaba-plan/qwen3.8-max` | report.py (md + html + inline SVG), 7 tests | 552,066 | 12 min |
| loader, ttt-gate, loop, g0-prep, surveys | Claude Sonnet subagents | model_io/baseline, ttt/gate, evaluate/loop, G0 plan | ~90–130k each | 2–8 min each |

The Qwen route spent 20× Codex's tokens on a comparable-size task (a tool loop re-reading files
each step); fine on the Lite Plan's quota for one mechanical file, wrong for anything iterative.

## F17. G0 (1.13B SFT) OOM: fragmentation FALSIFIED, batch 4 RUNS — Track B's blocker is closed

Both runs executed 2026-09-13 ~12:20 BST from `G0-OOM-RUN-PLAN.md` §1 in an isolated
`prabhasa/nemo-5090:26.02` container (`--memory 12g`), GPU otherwise idle, repo and checkpoint
mounted read-only. Raw outputs: `prototypes/nyaya_ttt_rsi/runs/g0_expandable/`
(`dry_run_1p13b_expandable.json`, `dry_run_1p13b_batch4.json`, both logs) — committed on this branch.

| run | flags (delta from `ed3f327`) | outcome |
|---|---|---|
| expandable | `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` (+ `PYTORCH_ALLOC_CONF`, the torch-2.10 name) | **OOM at step 1** again. Allocator report: 26.14 GiB allocated by PyTorch, only 0.51 GiB reserved-but-unallocated, 26.65 GiB allowed. Peak 26.78 GiB. → the pre-registered *falsify* criterion: the need is real allocation, not fragmentation. |
| batch 4 | `--batch-size 4` (everything else identical) | **40/40 steps completed.** Peak VRAM 23.77 GiB, host RSS 5.45 GiB, steady state 6.02 steps/s, loss 1.90 → 0.89 over 40 steps. Script's own projection: 3,103 steps/epoch, 9,309 steps for 3 epochs ≈ **26 min**. |

- **Owner action (Track B):** run the real G0 SFT at batch 4 (or batch 4 × grad-accum 2 once
  `sft_megatron_batched.py` grows a `--grad-accum` flag — it has none today, see the plan §3). Fold
  this into `research/journal.md` (the G0 saga currently lives only in `docs/plans/2026-09-12-g0-*.md`
  and commit messages). The 12 GiB host cap is a fail-fast: measured RSS is 5.45 GiB.
- **Image note:** torch 2.10 warns `PYTORCH_CUDA_ALLOC_CONF` is deprecated in favour of
  `PYTORCH_ALLOC_CONF`; set both until the scripts are updated.
