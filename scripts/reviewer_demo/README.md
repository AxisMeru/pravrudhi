# Reviewer structural check (local demo)

A lawyer maps each required ingredient of IPC 415 (cheating, delivery of property) to a fact and an exact quote, and
confirms it. The engine's real quote checker verifies each quote; the real, sha256-pinned Lean `score` binary checks
whether every required ingredient is covered. No model is called, and no hosted engine or login is needed.

Reviewer confirmations are human decisions and are labelled so. The recorded model judgments for the same facts are
shown separately, read-only, and never feed the check. The automated analyse-facts workflow is unchanged.

## Launch

Needs: this branch, `uv`, and the Lean `score` binary whose sha256 is
`700de3aadf50b482f4cf23499a03fd518cf72734bf8f51b950b5983555dea229` (CI's `prabhasa-nyaya-score-bin` artifact).

```bash
git clone https://github.com/AxisMeru/pravrudhi && cd pravrudhi && git checkout claude/awesome-hopper-pyabd6 && uv sync --frozen
```

```bash
chmod +x /abs/path/to/score && PRABHASA_NYAYA_SCORE_BIN=/abs/path/to/score uv run --frozen python scripts/reviewer_demo/app.py
```

Open http://127.0.0.1:8765. The server refuses to start if the binary's sha256 differs from
`configs/nyaya_agent.yaml`'s `pinned_score_sha256`. Every check is appended to `scripts/reviewer_demo/review_record.jsonl`
(override with `REVIEWER_DEMO_RECORD`; port with `REVIEWER_DEMO_PORT`).

## What the check does and does not say

- `Lean: grounded`: every required ingredient was confirmed by the reviewer AND its quote was found verbatim, once, in
  the named fact. It is a check of the structure of the reviewer's mapping.
- `Lean: flagged`: at least one required ingredient is not covered; `omitted_claims` names it.
- It does not decide whether the alleged facts are true, or whether an offence is made out.

Tests over the real binary: `PRABHASA_NYAYA_SCORE_BIN=/abs/path/to/score uv run --frozen pytest tests/test_reviewer_demo.py`.
