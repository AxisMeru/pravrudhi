"""Training corpora for a choice-answer track: rows of `{question, answer}` the kernel scorer can verify.

The model track rejection-samples from a corpus and keeps only what the kernel scorer marks correct. For the
nyaya track that corpus cannot be the sealed pool, because training on the evaluation items would corrupt the
one instrument the loop selects with, and it cannot be GSM8K, whose answers are numbers `metrics.mmlu`
refuses outright.

**CaseHOLD** is what this builds: real US case-law holdings, five candidate holdings per item, about 45,000
training rows, ungated and public. Its items are drawn from a different corpus entirely from the evaluation
pool (MMLU law validation and dev), so disjointness is by construction rather than by a filter that could be
wrong.

Two alternatives were examined and are recorded here because the reasons outlast the decision:

* **LegalBench** (`nguha/legalbench`, CC-BY-4.0, 162 tasks) is a *benchmark*, not a corpus. Its `train.tsv`
  files hold 3 to 9 rows each -- `citation_prediction_classification` 3, `abercrombie` 6,
  `definition_classification` 9 -- because they are few-shot demonstrations. Nothing can be trained on that.
  It remains the strongest candidate for additional *benchmarks*.
* **IL-TUR** (`Exploration-Lab/IL-TUR`) is exactly the right domain, Indian law, with a 145 MB LSI training
  split covering statute identification. It is gated and the request is not yet authorised, so it cannot be
  fetched. Nothing here is CaseHOLD-specific below `casehold_rows`, so it drops in when access arrives.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import re
import sys
import urllib.request
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pravrudhi.application.choice import letter, render_choice_question

# RegLab's full "Hallucinating Law" dataset: 745,608 rows over eleven tasks, with the ground-truth citation
# beside each query and -- the part nothing else has -- 10,736 rows about cases that were INVENTED.
REGLAB_REPO = "reglab/legal_hallucinations"
REGLAB_ORIGIN = "https://huggingface.co/datasets/reglab/legal_hallucinations (Stanford RegLab)"
REGLAB_URL = "https://huggingface.co/datasets/{repo}/resolve/main/dataset.csv"
REGLAB_REAL_TASKS = ("case_existence", "citation_retrieval")
REGLAB_FAKE_TASKS = ("fake_case_existence", "fake_dissent", "fake_year_overruled")
EXISTENCE_INSTRUCTION = "Is this a real case?"
CITATION_INSTRUCTION = (
    'What is the citation for the given case? Provide ONLY the citation in "<volume> <reporter> <page>" '
    "format, nothing else."
)
EXISTENCE_OPTIONS = ("yes", "no")

CASEHOLD_REPO = "casehold/casehold"
CASEHOLD_ORIGIN = "https://huggingface.co/datasets/casehold/casehold (US case-law holdings, Apache-2.0)"
CASEHOLD_URL = "https://huggingface.co/datasets/{repo}/resolve/main/data/all/{split}.csv"
CASEHOLD_HOLDINGS = 5

# The columns, positionally, because the published header names them "0".."11" with no meaning attached.
_ID, _PROMPT, _FIRST_HOLDING, _LABEL = 0, 1, 2, 12

CASEHOLD_INSTRUCTION = (
    "The passage below cites a case whose holding has been masked as (<HOLDING>). "
    "Which of the following is the masked holding?"
)


def fetch_casehold(dest: Path, split: str = "train") -> Path:
    """Download one CaseHOLD split and return the local path.

    Fetching is a separate act from building for the reason `fetch_apps` gives: a builder that downloaded its
    own source would let a corpus depend on whatever the network returned that day, with nothing in the
    manifest able to tell afterwards.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"casehold-{split}.csv"
    url = CASEHOLD_URL.format(repo=CASEHOLD_REPO, split=split)
    with urllib.request.urlopen(url) as response:  # noqa: S310 - a pinned https URL on the Hub
        target.write_bytes(response.read())
    return target


def casehold_rows(source: Path) -> list[dict[str, str]]:
    """CaseHOLD as `{question, answer}` rows, rendered the way the evaluation pool renders its items."""
    csv.field_size_limit(sys.maxsize)  # a citing prompt is longer than the default field cap
    out: list[dict[str, str]] = []
    with Path(source).open(newline="") as fh:
        reader = csv.reader(fh)
        next(reader, None)  # the header names its columns "0".."11"
        for record in reader:
            if len(record) <= _LABEL:
                continue
            holdings = record[_FIRST_HOLDING : _FIRST_HOLDING + CASEHOLD_HOLDINGS]
            stem = f"{CASEHOLD_INSTRUCTION}\n\n{record[_PROMPT].strip()}"
            out.append(
                {
                    "id": str(record[_ID]),
                    "question": render_choice_question(stem, holdings),
                    # Raises on a label the item has no option for, rather than mapping it to some letter:
                    # a gold the scorer cannot read would fail a night rather than degrade it.
                    "answer": letter(int(record[_LABEL]), CASEHOLD_HOLDINGS),
                }
            )
    return out


def _citation_key(text: str) -> str:
    """A citation as one comparable token: `470 F.2d 798`, `470 F. 2d 798` and `470 f.2d 798.` are one thing."""
    return re.sub(r"[\s.,]", "", text).lower()


def _case_name(query: str) -> str:
    """The case as named in a RegLab existence prompt, which reads `Is the case X, <cite> (year), a real case?`"""
    match = re.search(r"Is the case (.+?), a real case\?", query)
    return match.group(1).strip() if match else query.strip()


def _cited_case_name(query: str) -> str:
    """The case named in a RegLab citation prompt, which reads `What is the citation for ... case X? Provide...`

    The published prompts come in several shapes -- with and without a few-shot block, with the case in the
    sentence or on its own `Case:` line -- so the name is taken from whichever form matched rather than by
    assuming one.

    The LAST `Case:` block, not the first. A few-shot prompt lists worked examples before the real question, so
    the first match is an example -- and pairing an example's NAME with the target's CITATION would have
    written a systematically mislabelled corpus that nothing downstream could detect.
    """
    blocks = re.findall(r"(?s)\bCase:\s*(.+?)\s*(?:\nAnswer:|$)", query)
    if blocks:
        return str(blocks[-1]).strip()
    inline = re.search(r"citation for (?:the )?(?:\w+ )*?case (.+?)\?", query)
    return inline.group(1).strip() if inline else ""


def case_existence_rows(
    source: Path, *, exclude_citations: Iterable[str] = ()
) -> list[dict[str, str]]:
    """"Is this a real case?" over real and invented cases, as two-option items.

    This is the corpus for the number the objective most wants moved. Measured over 2,444 items on three
    tasks, the base model's `citation_abstention` was 0.0000: it never once said it did not know. CaseHOLD
    cannot teach that, because one of its five given holdings is always correct and declining is never right.
    RegLab's invented cases can: asked whether a case exists, the real ones answer yes and the fabricated ones
    no, which is decidable and which the existing choice scorer reads with no kernel change.

    `exclude_citations` holds out the evaluation items. The published eval subset is a SAMPLE of this same
    dataset, so without the exclusion the loop would train on what its own external proof is scored against.
    Every query is repeated across models, temperatures and prompt styles, so rows are deduplicated to items.
    """
    csv.field_size_limit(sys.maxsize)
    held = {_citation_key(c) for c in exclude_citations}
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    with Path(source).open(newline="") as fh:
        for record in csv.DictReader(fh):
            task = str(record.get("task") or "")
            real = task in REGLAB_REAL_TASKS
            if not real and task not in REGLAB_FAKE_TASKS:
                continue
            citation = str(record.get("citation") or "")
            if not citation or _citation_key(citation) in held:
                continue
            name = _case_name(str(record.get("query") or ""))
            key = (name, _citation_key(citation))
            if key in seen:
                continue
            seen.add(key)
            stem = f"{EXISTENCE_INSTRUCTION}\n\n{name}, {citation}"
            out.append(
                {
                    "id": f"{'real' if real else 'fake'}-{len(out):06d}",
                    "question": render_choice_question(stem, EXISTENCE_OPTIONS),
                    "answer": letter(0 if real else 1, len(EXISTENCE_OPTIONS)),
                }
            )
    return out


def _balanced(rows: list[dict[str, str]], seed: int) -> list[dict[str, str]]:
    """Equal numbers of each answer, by sampling the larger side down.

    The raw corpus is 50,240 real cases against 5,169 invented ones: 91% of the answers are "yes". Training on
    that teaches the prior, not the distinction -- and the prior it teaches is "assume it exists", which is
    precisely the failure being measured. A model can score 0.907 on the raw set while never once declining.
    """
    by_answer: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        by_answer.setdefault(row["answer"], []).append(row)
    if len(by_answer) < 2:
        return rows
    smallest = min(len(group) for group in by_answer.values())
    rng = random.Random(seed)
    out: list[dict[str, str]] = []
    for answer in sorted(by_answer):
        group = by_answer[answer]
        out.extend(group if len(group) == smallest else rng.sample(group, smallest))
    return sorted(out, key=lambda r: r["id"])


def build_case_existence(
    source: Path,
    out: Path,
    *,
    exclude_citations: Iterable[str] = (),
    count: int | None = None,
    seed: int = 0,
    balance_answers: bool = True,
) -> dict[str, Any]:
    """Write the existence training parquet, recording what was held out and how balanced it came out."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    excluded = list(exclude_citations)
    everything = case_existence_rows(source)
    rows = case_existence_rows(source, exclude_citations=excluded)
    n_excluded = len(everything) - len(rows)
    if balance_answers:
        rows = _balanced(rows, seed)
    if count is not None and count < len(rows):
        rows = sorted(random.Random(seed).sample(rows, count), key=lambda r: r["id"])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"question": [r["question"] for r in rows], "answer": [r["answer"] for r in rows]}), out
    )
    balance: dict[str, int] = {}
    for row in rows:
        balance[row["answer"]] = balance.get(row["answer"], 0) + 1
    manifest = {
        "corpus": "reglab-case-existence",
        "answer_kind": "choice",
        "n_options": len(EXISTENCE_OPTIONS),
        "n_rows": len(rows),
        "n_excluded": n_excluded,
        "balance": dict(sorted(balance.items())),
        "balanced": balance_answers,
        "n_before_balancing": len(everything) - n_excluded,
        "seed": seed,
        "source": {
            "file": Path(source).name,
            "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "origin": REGLAB_ORIGIN,
        },
        "held_out_citations": sorted(excluded)[:50],
        "note": (
            "Teaches declining, which is the behaviour the objective names and whose baseline is "
            "citation_abstention 0.0000 at n=2444. A is yes (the case is real), B is no (it was invented)."
        ),
    }
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def citation_rows(source: Path, *, exclude_citations: Iterable[str] = ()) -> list[dict[str, str]]:
    """Case name -> citation, as free-text items (ADR-0036).

    The corpus for the metric itself rather than for its proxy. `citation_precision` is 0.0000 at n=1000 and
    neither of the other two corpora can move it: CaseHOLD asks which of five given holdings is correct, and
    recall is not discrimination. RegLab's `citation_retrieval` rows carry the ground-truth citation in their
    own column, 13,531 distinct (case, citation) pairs once the repeats across models and temperatures are
    collapsed.

    Verified at training time by `metrics.citation`, which is why ADR-0036 had to add it: `gsm8k.gold_answer`
    rejects a citation for lacking '####' and `mmlu.gold_answer` for not being one letter, so rejection
    sampling could not check a single row of this.
    """
    csv.field_size_limit(sys.maxsize)
    held = {_citation_key(c) for c in exclude_citations}
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    with Path(source).open(newline="") as fh:
        for record in csv.DictReader(fh):
            if str(record.get("task") or "") != "citation_retrieval":
                continue
            citation = str(record.get("citation") or "").strip()
            if not citation or _citation_key(citation) in held:
                continue
            name = _cited_case_name(str(record.get("query") or ""))
            if not name:
                continue
            key = (name, _citation_key(citation))
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "id": f"cite-{len(out):06d}",
                    "question": f"{CITATION_INSTRUCTION}\n\nCase: {name}",
                    "answer": citation,
                }
            )
    return out


def build_citation_recall(
    source: Path, out: Path, *, exclude_citations: Iterable[str] = (), count: int | None = None, seed: int = 0
) -> dict[str, Any]:
    """Write the citation-recall training parquet, recording what was held out."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    excluded = list(exclude_citations)
    everything = citation_rows(source)
    rows = citation_rows(source, exclude_citations=excluded)
    n_excluded = len(everything) - len(rows)
    if count is not None and count < len(rows):
        rows = sorted(random.Random(seed).sample(rows, count), key=lambda r: r["id"])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"question": [r["question"] for r in rows], "answer": [r["answer"] for r in rows]}), out
    )
    manifest = {
        "corpus": "reglab-citation-recall",
        "answer_kind": "text",
        "n_rows": len(rows),
        "n_excluded": n_excluded,
        "seed": seed,
        "source": {
            "file": Path(source).name,
            "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "origin": REGLAB_ORIGIN,
        },
        "held_out_citations": sorted(excluded)[:50],
        "note": (
            "Recall, not discrimination: the model is given a case name and no options. Verified by "
            "metrics.citation (ADR-0036). One citation per case, so a case reported in two reporters "
            "undercounts -- the same limit the external metric has."
        ),
    }
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def build_casehold(
    source: Path, out: Path, *, count: int | None = None, seed: int = 0
) -> dict[str, Any]:
    """Write the training parquet the night loader reads, plus a manifest recording where it came from."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    rows = casehold_rows(source)
    if count is not None and count < len(rows):
        # A deterministic subset rather than the head of the file: CaseHOLD is ordered, and a prefix would be
        # a slice of one region of case law.
        rows = sorted(random.Random(seed).sample(rows, count), key=lambda r: r["id"])
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"question": [r["question"] for r in rows], "answer": [r["answer"] for r in rows]}), out
    )
    manifest = {
        "corpus": "casehold",
        "answer_kind": "choice",
        "n_options": CASEHOLD_HOLDINGS,
        "n_rows": len(rows),
        "item_ids": [r["id"] for r in rows],
        "seed": seed,
        "source": {
            "file": Path(source).name,
            "sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
            "origin": CASEHOLD_ORIGIN,
        },
        "disjoint_from": [
            "mmlu-law-val (MMLU professional_law and jurisprudence, validation and dev)",
            "mmlu_professional_law test and mmlu_pro_law test, which are the external proof",
        ],
        "note": (
            "Training rows for rejection sampling. Rendered by application.choice, the same renderer the "
            "evaluation pool uses, so an adapter is not learning a format it will not be asked in."
        ),
    }
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest
