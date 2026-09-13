# nyaya_ttt_rsi — RSI + test-time-training prototype for prabhasa-nyaya (Track B)

Goal: prove, on Track B's real law-tuned 370M checkpoint and the real 690-item held-out law eval,
that a retrieval-grounded + ephemeral-TTT + gated-consolidation loop moves the MVP numbers
(citation precision / recall, correct abstention) beyond the frozen model — with Wilson intervals
and paired McNemar, never a bare point estimate. Design rationale:
`docs/ttt-rsi-track-b-research-and-implementation.md`.

Runs inside the isolated `ttt-lab` container (image `prabhasa/nemo-5090:26.02`, 8 GiB RAM cap):
`docker exec ttt-lab bash -lc "cd /lab && python3 -m prototypes.nyaya_ttt_rsi.<module> ..."`.
Mounts inside it: this worktree at `/lab`; Track B repo (read-only) at `/trackB`;
`~/.local/share/prabhasa-samskrutam` (read-only) at `/trackB-local` — the 370M law-tuned
checkpoint is `/trackB-local/m7/m7_retry_checkpoint.pt`; `/fusion-project` (read-only).

All outputs go under `prototypes/nyaya_ttt_rsi/runs/<run-name>/` (gitignored except `report.json`
and `report.md`). Nothing here writes to `research/`, `gates/`, or `pravrudhi_kernel/`.

## Module contract (each module is owned by one agent; keep interfaces exactly as below)

| module | owner | provides |
|---|---|---|
| `model_io.py` | loader agent | `load_model(ckpt_path, device) -> (model, ByteTokenizer)`; `generate(model, tok, prompts: list[str], max_new_tokens=256, stop: list[str]) -> list[str]` (batched by exact prompt length — Mamba2 has no attention mask, never pad); `sequence_nll(model, tok, prompt, continuation) -> float` (mean NLL over continuation bytes only); `linear_module_names(model) -> list[str]` (dotted names of every `nn.Linear`, for LoRA targeting) |
| `retrieval.py` | retrieval agent | `PassageStore.from_law_files(train_jsonl, heldout_jsonl)` (one passage per (act, section): article text + citation string); `store.search(query, k) -> list[Passage]` (BM25, pure python/numpy); `build_grounded_prompt(question, passages) -> str`; `parse_answer(text) -> Answer(citations: list[(act, section)], abstained: bool)`; `grounded(answer, passages) -> bool` |
| `stats.py` | retrieval agent | `wilson(k, n) -> (lo, hi)`; `mcnemar(b, c) -> p` (exact binomial, paired); `paired_table(a: list[bool], b: list[bool]) -> (b, c)` |
| `ttt.py` | ttt-gate agent | `inject_lora(model, target_regex, r, alpha) -> list[LoRALinear]`; `LoRALinear.reset()`; `snapshot(loras)`/`restore(loras, snap)`; `adapt(model, tok, text, loras, steps, lr) -> float` (self-supervised next-byte NLL on `text` only) |
| `gate.py` | ttt-gate agent | `RegressionProbe.from_files(...)` (fixed canary set: general-domain held-out slice + settled-law items); `probe.nll(model, tok) -> float`; `decide(probe_before, probe_after, grounded_ok, threshold) -> GateDecision` (dataclass, JSON-serializable, fields: `accepted`, `reason`, `probe_delta`, `grounded`) |
| `evaluate.py` | lead / loop agent | runs conditions A–D over the held-out set, writes `answers_<cond>.jsonl` and scores them with Track B's own `/trackB/scripts/eval/score_law_qa.py` (import it, do not reimplement) plus `stats.py` paired comparisons |
| `loop.py` | loop agent | the RSI controller: per-query TTT → gate → accept/reject ledger → consolidation SFT of a persistent LoRA on accepted, checker-verified pairs → re-evaluate → next round; writes `report.json`/`report.md` |

Conditions: **A** frozen closed-book (Track B's published baseline, must reproduce ≈ 1/227 citation
recall, 5/9 abstention); **B** frozen + retrieval-grounded prompt; **C** B + ephemeral TTT on the
retrieved passages (reset per query); **D** C + gated consolidation LoRA after each round.

Rules: no `git commit` from agents (the lead commits); no writes outside `prototypes/nyaya_ttt_rsi/`;
one GPU job at a time — check `nvidia-smi` before any GPU run and stop if another process is on it;
never restart a crashed GPU job without reporting first.
