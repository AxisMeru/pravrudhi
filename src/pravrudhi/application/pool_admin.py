"""Seal a benchmark pool from a parquet file into the kernel's pools directory (an operator act, done
once)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pravrudhi.application.choice import LETTERS, render_choice_question
from pravrudhi_kernel.metrics import seal_pool
from pravrudhi_kernel.sandbox import ensure_kernel_state
from pravrudhi_kernel.sandbox.runner import docker_available

APPS_REPO = "codeparrot/apps"
APPS_PARQUET_REVISION = "refs/convert/parquet"
APPS_PARQUET_FILE = "all/{split}/0000.parquet"
APPS_ORIGIN = "https://huggingface.co/datasets/codeparrot/apps (MIT), held-out slice"
APPS_DIFFICULTIES = ("introductory", "interview")
APPS_MIN_TESTS = 2
APPS_MAX_HIDDEN_TESTS = 12
APPS_SOLVE_INSTRUCTION = (
    "Implement `def solve(stdin: str) -> str` that reads the whole input as one string "
    "and returns the exact output."
)

# ADR-REF: ADR-0035. The product objective's proxy, sealed as the loop's selection instrument.
MMLU_REPO = "cais/mmlu"
MMLU_ORIGIN = "https://huggingface.co/datasets/cais/mmlu (MIT)"
MMLU_LAW_SUBJECTS = ("jurisprudence", "professional_law")
# The test splits are what lm-eval scores on the external proof tier, and the operator's delegation makes the
# external number the proof of improvement while the internal pool is the selection instrument only. A pool
# containing the proof items would let selection show up inside its own proof, so only validation and dev are
# sealed. The price is a small pool (191 real items), which the noise-floor study measures rather than assumes.
MMLU_INTERNAL_SPLITS = ("validation", "dev")
MMLU_EXTERNAL_SPLITS = ("test",)
MMLU_LETTERS = LETTERS  # kept as a name here; the rendering itself lives in `choice`


def seal_gsm8k(root: Path, parquet: Path, bench: str = "gsm8k-test", offset: int = 0, count: int | None = None) -> dict[str, Any]:
    import pyarrow.parquet as pq

    state = ensure_kernel_state(root, docker_available=docker_available())
    rows = pq.read_table(parquet).to_pylist()
    rows = rows[offset : (offset + count) if count else None]
    src = {
        "file": parquet.name,
        "sha256": hashlib.sha256(parquet.read_bytes()).hexdigest(),
        "n_rows": len(rows),
        "origin": "https://huggingface.co/datasets/openai/gsm8k (MIT)",
    }
    return seal_pool(Path(state.pools_dir) / bench, bench, rows, src)


def seal_mbpp_plus(root: Path, cache: Path, bench: str = "mbppplus") -> dict[str, Any]:
    """Seal EvalPlus MBPP+ (378 problems) as a kernel pool from the exported JSONL in the external cache: question =
    the EvalPlus prompt (with its visible example assert), answer = JSON naming the task and entry point; hidden tests
    run only inside the sandbox scorer job."""
    src_file = Path(cache) / f"{bench}.jsonl"
    rows = []
    for line in src_file.read_text().splitlines():
        if line.strip():
            pr = json.loads(line)
            rows.append(
                {
                    "question": pr["prompt"],
                    "answer": json.dumps(
                        {k: pr[k] for k in ("task_id", "entry_point", "canonical_solution", "n_base", "n_plus")}
                    ),
                }
            )
    state = ensure_kernel_state(root, docker_available=docker_available())
    src = {
        "file": src_file.name,
        "sha256": hashlib.sha256(src_file.read_bytes()).hexdigest(),
        "n_rows": len(rows),
        "origin": "EvalPlus MBPP+ v0.2.0 (Apache-2.0), exported from the evalplus package",
    }
    return seal_pool(Path(state.pools_dir) / bench, bench, rows, src)


def _mmlu_parquet(cache: Path, subject: str, split: str) -> Path:
    """The cached parquet for one MMLU subject and split.

    The hub layout is `hub/datasets--cais--mmlu/snapshots/<rev>/<subject>/<split>-*.parquet`. The revision is
    globbed rather than pinned because the cache directory is the record of what was fetched; the manifest
    pins the file by sha256, which is the thing that has to be reproducible."""
    matches = sorted(cache.glob(f"hub/datasets--cais--mmlu/snapshots/*/{subject}/{split}-*.parquet"))
    if not matches:
        raise FileNotFoundError(
            f"no cached {MMLU_REPO} parquet for {subject}/{split} under {cache}; fetch it once with "
            f"`huggingface-cli download {MMLU_REPO} --repo-type dataset --include '{subject}/*'` "
            f"(HF_HOME={cache}) and seal from the file on disk"
        )
    return matches[-1]


def seal_mmlu(
    root: Path,
    cache: Path,
    bench: str = "mmlu-law-val",
    *,
    subjects: Sequence[str] = MMLU_LAW_SUBJECTS,
    splits: Sequence[str] = MMLU_INTERNAL_SPLITS,
) -> dict[str, Any]:
    """Seal a law slice of MMLU as the first internal multiple-choice pool (ADR-0035).

    Question = the stem with its options under letters; answer = the option letter. The manifest declares
    `answer_kind: choice`, which is what makes the pool scoreable at all: before ADR-0035 the kernel had one
    scorer and `gold_answer("A")` raised.
    """
    import pyarrow.parquet as pq

    rows: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for subject in sorted(subjects):
        for split in splits:
            src_file = _mmlu_parquet(Path(cache), subject, split)
            table = pq.read_table(src_file).to_pylist()
            files.append(
                {
                    "subject": subject,
                    "split": split,
                    "file": src_file.name,
                    "sha256": hashlib.sha256(src_file.read_bytes()).hexdigest(),
                    "n_rows": len(table),
                }
            )
            for r in table:
                options = list(r["choices"])
                rows.append(
                    {
                        "question": render_choice_question(str(r["question"]), options),
                        "answer": MMLU_LETTERS[int(r["answer"])],
                    }
                )
    state = ensure_kernel_state(root, docker_available=docker_available())
    src = {
        "origin": MMLU_ORIGIN,
        "subjects": sorted(subjects),
        "splits": list(splits),
        "files": files,
        "n_rows": len(rows),
        "held_out_for_external_proof": list(MMLU_EXTERNAL_SPLITS),
    }
    return seal_pool(Path(state.pools_dir) / bench, bench, rows, src, answer_kind="choice")


#: CaseHOLD's own validation split. Disjoint from `train` by construction, which is the whole reason to use
#: it: the model track does rejection sampling on `casehold-train`, and an evaluation pool drawn from those
#: same rows would have the loop selecting on what it trained on.
CASEHOLD_INTERNAL_SPLIT = "val"

#: Reserved for the external proof tier, never sealed as an internal pool.
CASEHOLD_EXTERNAL_SPLITS = ("test",)


#: Longest question a `casehold-val` item may carry, in characters.
#:
#: Not a taste judgement about difficulty -- a cliff in the data. The distribution is p99 = 2,596 chars and
#: p99.5 = 3,033, and then **23 items of 5,314 jump to as much as 115,303** with essentially nothing in
#: between: the same 23 are excluded by any cut from 4,000 to 8,000. At ~4 chars per token that outlier is
#: ~29k tokens, and `agent_choice.py` batches 16 prompts with `padding=True`, so one of them pads the whole
#: batch to its length and the job dies with CUDA OOM. That is how the first casehold floor lost every run of
#: rotation 0 while rotations 1 and 2 passed -- rotation 0 had drawn one.
#:
#: An item the model cannot be ASKED is not an evaluation item; it scores 0 for a context reason and reads as
#: a legal-reasoning failure. Dropping 0.43% of the pool to remove items 40x the median is not a slice off the
#: hard end of the distribution, and `n_dropped_too_long` records exactly how many went.
CASEHOLD_MAX_QUESTION_CHARS = 4000


def seal_casehold(
    root: Path, source: Path, bench: str = "casehold-val", *, max_chars: int = CASEHOLD_MAX_QUESTION_CHARS
) -> dict[str, Any]:
    """Seal CaseHOLD's validation split as the law tracks' internal choice pool (ADR-0041).

    `mmlu-law-val` is spent: 191 items, 115 of them at exposure cap 16, leaving 76 eligible against a draw of
    96 — so the nyaya harness night died at `draw_rotation` before evaluating anything. Raising the cap again
    (it went 8 -> 16 on 2026-09-10) buys a night or two and re-uses the same 191 items harder; this is the
    same move ADR-0029 made when MBPP+ ran out, which is to get a bigger pool rather than more re-use.

    Five options rather than MMLU's four, and the answer distribution is near-uniform across A-E, so chance is
    0.20 here against 0.25 there. That is not comparable to `mmlu-law-val` and is not meant to be: a pass rate
    on this pool is a different quantity, which is exactly why `live_candidates` filters by bench.

    The rows are rendered by `application.choice`, the same renderer the training corpus uses, so an adapter
    is never asked in a format it did not train in.
    """
    from pravrudhi.application.corpus import CASEHOLD_ORIGIN, casehold_rows

    source = Path(source)
    every = [{"question": r["question"], "answer": r["answer"]} for r in casehold_rows(source)]
    rows = [r for r in every if len(r["question"]) <= max_chars]
    dropped = len(every) - len(rows)
    if not rows:
        raise ValueError(f"{source} yielded no rows; a pool of nothing would refuse every draw")
    if dropped > len(every) // 20:
        # A bound that removes more than 5% is cutting into the distribution rather than clipping outliers,
        # and that is a different decision from this one -- it would change what the bench measures.
        raise ValueError(
            f"max_chars={max_chars} would drop {dropped} of {len(every)} items ({100 * dropped / len(every):.1f}%). "
            f"That is a slice of the distribution, not an outlier clip; choose the bound deliberately."
        )
    state = ensure_kernel_state(root, docker_available=docker_available())
    src = {
        "origin": CASEHOLD_ORIGIN,
        "split": CASEHOLD_INTERNAL_SPLIT,
        "files": [
            {
                "file": source.name,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "n_rows": len(rows),
            }
        ],
        "n_rows": len(rows),
        "n_rows_in_split": len(every),
        "max_question_chars": max_chars,
        # Reported, not assumed. A reader has to be able to see how much of the split this pool is and why the
        # rest is missing, or the exclusion becomes invisible and then becomes folklore.
        "n_dropped_too_long": dropped,
        "dropped_note": (
            "Questions longer than max_question_chars are excluded because `agent_choice.py` batches 16 "
            "prompts with padding=True, so a single outlier pads the batch to its own length and the job "
            "dies with CUDA OOM -- which is what cost every run of rotation 0 on the first floor attempt. "
            "The distribution has p99 2596 and p99.5 3033 chars and then jumps to 115303, so this clips "
            "outliers rather than trimming the hard end."
        ),
        "held_out_for_external_proof": list(CASEHOLD_EXTERNAL_SPLITS),
        "disjoint_from": [
            "casehold-train, which the model track trains on -- CaseHOLD's own split boundary, not a filter "
            "applied here",
            "casehold test, which is the external proof",
        ],
    }
    return seal_pool(Path(state.pools_dir) / bench, bench, rows, src, answer_kind="choice")


#: IL-TUR's own validation split. `train` is for the model track's rejection sampling and `test` is the
#: external proof, so selecting on either would have the loop score itself on what it learned from or is
#: judged by.
LSI_INTERNAL_SPLIT = "dev"
LSI_EXTERNAL_SPLITS = ("test",)

#: Characters of case text kept per item. **The decision ADR-0043 records.**
#:
#: LSI case text is median 3,418 characters but p90 17,900 and max 442,434 — a continuous heavy tail, not
#: CaseHOLD's cliff of 23 pathological outliers past a p99.5 of 3,033. A length BOUND that excluded the tail
#: would drop 37% of the split, and `seal_casehold` rightly refuses anything over 5% as slicing the
#: distribution. So this truncates instead of excluding, and the difference is the whole decision:
#:
#: * **Truncating keeps every case**, so the pool is not selected on length. Excluding would bias it in
#:   exactly the dimension being measured, because a longer judgment carries more sections — the median case
#:   has three and the largest 28.
#: * It is what IL-TUR's own published baselines do (BERT-family models at 512 tokens), so the number stays
#:   comparable to the literature rather than being a private variant.
#: * It **lowers the achievable ceiling**, because a truncated case can drop the passage that identifies a
#:   section. That is a real cost and belongs in the manifest beside the number, not discovered later.
#:
#: 6,000 characters is ~1,500 tokens, leaving room for the 1,301-character label block every prompt carries
#: plus the generation budget, at a batch of 16 that pads to its longest member.
LSI_MAX_CASE_CHARS = 6000

#: Head truncation, matching the baselines. An Indian judgment states the facts and the charge early and the
#: holding late; taking the head keeps what the offence is, which is what the sections follow from.
LSI_INSTRUCTION = (
    "Identify every section of the Indian Penal Code that applies to the case below. Choose only from the "
    "listed sections. Answer with the section names separated by commas and nothing else."
)


def seal_lsi(
    root: Path,
    source: Path,
    statutes: Path,
    bench: str = "iltur-lsi-dev",
    *,
    max_case_chars: int = LSI_MAX_CASE_CHARS,
) -> dict[str, Any]:
    """Seal IL-TUR's LSI validation split as the first internal `set` pool (ADR-0043).

    The label space is given in the prompt. IL-TUR's baselines are classifiers over the 100 sections, so a
    generative model asked to invent the space from nothing would be measured on a harder and different task
    — and would produce unparseable answers rather than wrong ones.

    Gold is written as section NAMES, not the corpus's integer indices, so the kernel's `set` scorer keeps
    receiving what it already parses. The index-to-name mapping is the sealer's job and lives here.
    """
    import pyarrow.parquet as pq

    from pravrudhi_kernel.metrics.labels import canonical

    names = [str(r["id"]) for r in pq.read_table(Path(statutes)).to_pylist()]
    if len(names) != 100:
        raise ValueError(f"expected IL-TUR's 100-section label space, got {len(names)} from {statutes}")
    label_block = "\n".join(f"- {n}" for n in names)

    rows: list[dict[str, Any]] = []
    truncated = 0
    for record in pq.read_table(Path(source)).to_pylist():
        gold = {names[i] for i in record["labels"]}
        if not gold:
            continue  # an item with no applicable section has no answer for `set` to score
        case = " ".join(record["text"]).strip()
        if len(case) > max_case_chars:
            case = case[:max_case_chars]
            truncated += 1
        rows.append(
            {
                "question": f"{LSI_INSTRUCTION}\n\nSections:\n{label_block}\n\nCase:\n{case}",
                # The canonical form itself, so the sealed gold IS what the scorer's `gold_answer` produces
                # and cannot drift from it. `canonical` already orders numerically, so nothing re-sorts here.
                "answer": canonical(gold),
            }
        )
    if not rows:
        raise ValueError(f"{source} yielded no rows; a pool of nothing would refuse every draw")

    state = ensure_kernel_state(root, docker_available=docker_available())
    src = {
        "origin": "https://huggingface.co/datasets/Exploration-Lab/IL-TUR (LSI task, gated, CC-BY-NC-SA-4.0)",
        "split": LSI_INTERNAL_SPLIT,
        "files": [
            {"file": Path(f).name, "sha256": hashlib.sha256(Path(f).read_bytes()).hexdigest()}
            for f in (source, statutes)
        ],
        "n_rows": len(rows),
        "label_space": names,
        "max_case_chars": max_case_chars,
        # Reported, because truncation lowers the ceiling and a reader must be able to see how much of the
        # corpus was affected. Every case is KEPT -- that is the difference from a length bound, and the
        # reason this is truncation and not exclusion.
        "n_truncated": truncated,
        "truncation": (
            f"Head truncation at {max_case_chars} characters, as IL-TUR's own BERT-family baselines truncate. "
            f"No case is excluded, so the pool is not selected on length -- which matters because a longer "
            f"judgment carries more sections. A truncated case can lose the passage that identifies a "
            f"section, so this lowers the achievable ceiling and that is part of the number."
        ),
        "held_out_for_external_proof": list(LSI_EXTERNAL_SPLITS),
        "disjoint_from": ["IL-TUR lsi train, which is for training", "IL-TUR lsi test, the external proof"],
    }
    return seal_pool(Path(state.pools_dir) / bench, bench, rows, src, answer_kind="set")


def fetch_apps(dest: Path, split: str = "test") -> Path:
    """Download one APPS split's parquet into `dest` and return the local path.

    Sealing used to be the only step, because MBPP+ arrives through the evalplus package. APPS arrives from the
    Hub, and a sealer that downloaded its own source would let a sealed pool depend on whatever the network
    returned that night, with nothing in the manifest able to tell the difference. So the fetch is a separate
    operator act: fetch once, inspect, then seal from the file on disk whose sha256 the manifest records."""
    try:
        from huggingface_hub import hf_hub_download  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            f"huggingface_hub is not installed; download {APPS_PARQUET_FILE.format(split=split)} by hand from "
            f"https://huggingface.co/datasets/{APPS_REPO}/tree/{APPS_PARQUET_REVISION} "
            "and pass the local path to seal_apps"
        ) from e
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    return Path(
        hf_hub_download(
            repo_id=APPS_REPO,
            repo_type="dataset",
            revision=APPS_PARQUET_REVISION,
            filename=APPS_PARQUET_FILE.format(split=split),
            local_dir=str(dest),
        )
    )


def _apps_rows(source: Path) -> list[dict[str, Any]]:
    """The raw APPS rows from a local parquet or JSONL export, in file order."""
    if source.suffix == ".parquet":
        import pyarrow.parquet as pq

        return [dict(r) for r in pq.read_table(source).to_pylist()]
    return [json.loads(line) for line in source.read_text().splitlines() if line.strip()]


def _apps_task_id(row: dict[str, Any]) -> str | None:
    """APPS identifies a problem as `problem_id` in the parquet conversion and as `id` in the JSONL export the
    dataset actually ships. Sealing read only the first and died on the file a fetch produces."""
    for key in ("problem_id", "id"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


def _apps_io(problem: Mapping[str, Any]) -> tuple[list[str], list[str]] | None:
    """The stdin/stdout pairs of one APPS problem, or None when it cannot be posed as `solve(stdin) -> str`.

    APPS carries two test formats. Call-based problems name an `fn_name` and pass argument lists, which the
    stdin contract cannot express; sealing one anyway would produce a question whose visible example is not the
    thing the scorer runs, and the harness would be selecting on a check that does not predict the hidden
    verdict. Those problems are dropped instead."""
    raw = problem.get("input_output") or ""
    try:
        io = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(io, dict) or io.get("fn_name"):
        return None
    ins, outs = list(io.get("inputs") or []), list(io.get("outputs") or [])
    if len(ins) != len(outs) or len(ins) < APPS_MIN_TESTS:
        return None
    if not all(isinstance(x, str) for x in [*ins, *outs]):
        return None
    return ins, outs


def _draw_key(seed: int, task_id: str) -> str:
    """Which problems the seed picks: HMAC-free but seed-keyed and reproducible from the manifest alone."""
    return hashlib.sha256(f"{seed}|{task_id}".encode()).hexdigest()


def seal_apps(
    root: Path,
    source: Path,
    bench: str = "apps",
    *,
    count: int,
    seed: int,
    difficulties: Sequence[str] = APPS_DIFFICULTIES,
    max_hidden: int = APPS_MAX_HIDDEN_TESTS,
) -> dict[str, Any]:
    """Seal a held-out APPS slice as the harness track's SECOND internal code pool (ADR-0029).

    The track had one internal pool, MBPP+, and 378 problems at exposure cap 8 is a finite budget that is now
    spent: `draw_rotation` raises PoolExhausted and a night ends before it evaluates anything. HumanEval+ is the
    external check the track reports against and must never become an internal pool, or the number that is
    supposed to be held out would be the number being optimised against.

    Every APPS problem is rewritten as a function problem so that nothing downstream has to change: the question
    is the APPS prompt, the `solve(stdin) -> str` instruction, and ONE visible example rendered as an `assert`
    line, which is exactly what `docker/jobs/checks.py` already knows how to select on. The remaining pairs are
    hidden in the answer and only ever read by the scorer job. `max_hidden` bounds how many of them are sealed,
    because a rotation whose scorer job hits the kernel's wall clock loses every item's score, not just the slow
    one; the cap is recorded in the manifest so the bound is auditable rather than folklore."""
    source = Path(source)
    eligible: list[tuple[int, str, str, list[str], list[str]]] = []
    for idx, pr in enumerate(_apps_rows(source)):
        if str(pr.get("difficulty", "")) not in tuple(difficulties):
            continue
        io = _apps_io(pr)
        if io is None:
            continue
        task_id = _apps_task_id(pr)
        if task_id is None:
            continue
        eligible.append((idx, task_id, str(pr["question"]), io[0], io[1]))
    if len(eligible) < count:
        raise ValueError(f"{source}: {len(eligible)} eligible APPS problems < requested count {count}")
    drawn = sorted(sorted(eligible, key=lambda e: _draw_key(seed, e[1]))[:count])
    rows = [
        {
            "question": (
                f"{question.rstrip()}\n\n{APPS_SOLVE_INSTRUCTION}\n\nassert solve({ins[0]!r}) == {outs[0]!r}\n"
            ),
            "answer": json.dumps(
                {
                    "task_id": task_id,
                    "inputs": ins[1 : 1 + max_hidden],
                    "outputs": outs[1 : 1 + max_hidden],
                    "fn_name": "solve",
                }
            ),
        }
        for _, task_id, question, ins, outs in drawn
    ]
    state = ensure_kernel_state(root, docker_available=docker_available())
    src = {
        "file": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "n_rows": len(rows),
        "origin": APPS_ORIGIN,
        "draw": {
            "seed": seed,
            "count": count,
            "difficulties": list(difficulties),
            "min_tests": APPS_MIN_TESTS,
            "max_hidden_tests": max_hidden,
            "n_eligible": len(eligible),
        },
    }
    return seal_pool(Path(state.pools_dir) / bench, bench, rows, src)
