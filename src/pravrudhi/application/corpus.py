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
import sys
import urllib.request
from pathlib import Path
from typing import Any

from pravrudhi.application.choice import letter, render_choice_question

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
