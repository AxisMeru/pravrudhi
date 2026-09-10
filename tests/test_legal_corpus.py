"""The nyaya track's training corpus.

The model track rejection-samples from a training corpus and keeps what the kernel scorer verifies. For the
law track that corpus cannot be the sealed pool -- training on the evaluation items would corrupt the
selection instrument -- and it cannot be GSM8K, whose answers are numbers the choice scorer refuses.

CaseHOLD is the corpus: real US case-law holdings, five options, ~45k training rows, ungated. LegalBench was
considered and rejected as a corpus rather than as a benchmark, because its `train.tsv` files hold 3 to 9 rows
each -- few-shot demonstrations, not training data. IL-TUR is the right domain (Indian law) and is blocked on
dataset access, so the builder is written to take either.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from pravrudhi.application.corpus import (
    CASEHOLD_HOLDINGS,
    build_case_existence,
    build_casehold,
    case_existence_rows,
    casehold_rows,
)


# example_id, citing_prompt, holding_0..4, five similarity floats, label
def _row(idx: int, label: int) -> list[str]:
    return (
        [str(idx), f"context {idx} with a masked (<HOLDING>) statement"]
        + [f"holding {idx}.{j}" for j in range(CASEHOLD_HOLDINGS)]
        + ["0.5", "0.5", "0.5", "0.5", "0.5"]
        + [str(label)]
    )


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "train.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Unnamed: 0", *[str(i) for i in range(12)]])
        for i in range(10):
            w.writerow(_row(i, i % CASEHOLD_HOLDINGS))
    return path


def test_rows_render_five_lettered_options_and_a_letter_answer(source: Path) -> None:
    rows = casehold_rows(source)
    assert len(rows) == 10
    first = rows[0]
    assert "A. holding 0.0" in first["question"]
    assert "E. holding 0.4" in first["question"]
    assert first["answer"] == "A"
    assert rows[3]["answer"] == "D"


def test_the_answer_is_readable_by_the_kernel_scorer_that_will_score_it(source: Path) -> None:
    # The whole point of the corpus: rejection sampling scores these with metrics.mmlu, so every gold here
    # must be one the choice scorer accepts. A row it refuses would crash a night, not degrade it.
    from pravrudhi_kernel.metrics.mmlu import gold_answer

    for row in casehold_rows(source):
        assert gold_answer(row["answer"]) == row["answer"]


def test_a_malformed_label_is_refused_rather_than_silently_mapped(source: Path) -> None:
    with source.open("a", newline="") as fh:
        csv.writer(fh).writerow(_row(99, 9))  # only five holdings exist
    with pytest.raises(ValueError, match="outside the 5 options"):
        casehold_rows(source)


def test_build_writes_a_parquet_and_a_manifest_recording_the_source_hash(source: Path, tmp_path: Path) -> None:
    out = tmp_path / "casehold-train.parquet"
    manifest = build_casehold(source, out, count=6, seed=0)
    assert out.exists()
    assert manifest["n_rows"] == 6
    assert manifest["answer_kind"] == "choice"
    assert len(manifest["source"]["sha256"]) == 64
    assert manifest["disjoint_from"]
    written = json.loads(out.with_suffix(".manifest.json").read_text())
    assert written == manifest


def test_the_parquet_is_in_the_shape_the_night_loader_reads(source: Path, tmp_path: Path) -> None:
    import pyarrow.parquet as pq

    from pravrudhi.application.night import load_train_rows

    out = tmp_path / "c.parquet"
    build_casehold(source, out)
    assert pq.read_table(out).column_names == ["question", "answer"]
    rows = load_train_rows(out)
    assert set(rows[0]) == {"question", "answer"}


def test_the_draw_is_deterministic_for_a_seed(source: Path, tmp_path: Path) -> None:
    a = build_casehold(source, tmp_path / "a.parquet", count=5, seed=7)
    b = build_casehold(source, tmp_path / "b.parquet", count=5, seed=7)
    c = build_casehold(source, tmp_path / "c.parquet", count=5, seed=8)
    assert a["item_ids"] == b["item_ids"]
    assert a["item_ids"] != c["item_ids"]


class TestCaseExistence:
    """The corpus that targets the number the objective most wants moved: abstention 0.0000.

    Measured at n=2444 across three tasks, Qwen3-1.7B never once said it did not know. CaseHOLD cannot teach
    that -- it is a discrimination task where one of five given holdings is always right, so declining is
    never correct. RegLab's full dataset has what does: 108,344 `citation_retrieval` rows over real cases and
    10,736 `fake_case_existence` rows over cases that were invented. Asked "is this a real case?", the real
    ones answer yes and the invented ones no, which is a verifiable two-option item the existing choice scorer
    reads without any kernel change.
    """

    HEADER = [
        "id", "task", "court_level", "prompt_style", "llm", "temperature", "case_source",
        "court_slug", "citation", "year", "query", "llm_output", "correctness_score", "hallucination",
    ]

    @classmethod
    def _csv(cls, tmp_path: Path, rows: list[dict[str, str]]) -> Path:
        path = tmp_path / "reglab.csv"
        with path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cls.HEADER)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in cls.HEADER})
        return path

    @classmethod
    def _rows(cls) -> list[dict[str, str]]:
        real = [
            {"task": "case_existence", "case_source": "cap", "citation": f"{100 + i} F.2d {i}",
             "query": f'Is the case Real v. Case{i}, {100 + i} F.2d {i} (1950), a real case? Say "yes" or "no" only.'}
            for i in range(4)
        ]
        fake = [
            {"task": "fake_case_existence", "case_source": "fake", "citation": f"{700 + i} F.3d {i}",
             "query": f'Is the case Made v. Up{i}, {700 + i} F.3d {i}, a real case? Say "yes" or "no" only.'}
            for i in range(4)
        ]
        # The same question asked again of a different model: the published dataset repeats every query across
        # LLMs, temperatures and prompt styles, so unique ITEMS are far fewer than rows.
        return real + fake + [{**real[0], "llm": "gpt-4"}, {**fake[0], "llm": "gpt-4"}]

    def test_real_cases_answer_yes_and_invented_ones_no(self, tmp_path: Path) -> None:
        rows = case_existence_rows(self._csv(tmp_path, self._rows()))
        answers = {r["answer"] for r in rows}
        assert answers == {"A", "B"}
        yes = [r for r in rows if r["answer"] == "A"]
        no = [r for r in rows if r["answer"] == "B"]
        assert len(yes) == 4 and len(no) == 4, "the repeated rows are one item each, not two"
        assert all("A. yes" in r["question"] and "B. no" in r["question"] for r in rows)

    def test_the_choice_scorer_reads_every_gold(self, tmp_path: Path) -> None:
        from pravrudhi_kernel.metrics.mmlu import gold_answer

        for row in case_existence_rows(self._csv(tmp_path, self._rows())):
            assert gold_answer(row["answer"]) == row["answer"]

    def test_the_evaluation_items_are_excluded_by_citation(self, tmp_path: Path) -> None:
        # The eval subset is a sample of this same dataset. Training on an item the external proof is scored
        # on would put selection inside its own proof, which is the one thing the delegation forbids.
        held_out = {"100 F.2d 0", "700 F.3d 0"}
        rows = case_existence_rows(self._csv(tmp_path, self._rows()), exclude_citations=held_out)
        assert len(rows) == 6
        assert not any(c in r["question"] for r in rows for c in held_out)

    def test_exclusion_ignores_spacing_and_case(self, tmp_path: Path) -> None:
        rows = case_existence_rows(self._csv(tmp_path, self._rows()), exclude_citations={"100 f. 2d 0."})
        assert len(rows) == 7, "a citation is the same citation however it is punctuated"

    def test_build_records_what_was_held_out(self, tmp_path: Path) -> None:
        out = tmp_path / "existence.parquet"
        manifest = build_case_existence(
            self._csv(tmp_path, self._rows()), out, exclude_citations={"100 F.2d 0"}
        )
        assert manifest["answer_kind"] == "choice"
        assert manifest["n_excluded"] == 1
        assert manifest["n_before_balancing"] == 7
        # Balanced by default: 3 real survive the exclusion against 4 invented, so both sides come to 3.
        assert manifest["n_rows"] == 6
        assert manifest["balance"] == {"A": 3, "B": 3}
        assert out.exists()

    def test_the_answers_are_balanced_so_the_prior_is_not_the_lesson(self, tmp_path: Path) -> None:
        """The raw corpus is 50,240 yes against 5,169 no -- 91% yes.

        A model trained on that learns "assume the case exists", which is exactly the failure being measured:
        `citation_abstention` 0.0000. It could also score 0.907 on the raw set while never declining once.
        """
        rows = [
            {"task": "case_existence", "case_source": "cap", "citation": f"{100 + i} F.2d {i}",
             "query": f'Is the case Real v. Case{i}, {100 + i} F.2d {i} (1950), a real case? Say "yes" or "no" only.'}
            for i in range(20)
        ] + [
            {"task": "fake_case_existence", "case_source": "fake", "citation": f"{700 + i} F.3d {i}",
             "query": f'Is the case Made v. Up{i}, {700 + i} F.3d {i}, a real case? Say "yes" or "no" only.'}
            for i in range(3)
        ]
        manifest = build_case_existence(self._csv(tmp_path, rows), tmp_path / "b.parquet")
        assert manifest["balance"] == {"A": 3, "B": 3}
        assert manifest["n_before_balancing"] == 23
        assert manifest["balanced"] is True

    def test_balancing_can_be_declined_and_says_so(self, tmp_path: Path) -> None:
        manifest = build_case_existence(
            self._csv(tmp_path, self._rows()), tmp_path / "u.parquet", balance_answers=False
        )
        assert manifest["balanced"] is False
