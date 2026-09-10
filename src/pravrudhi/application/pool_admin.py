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
