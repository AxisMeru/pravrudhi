"""Local provision retrieval and citation checks; no model dependencies.

The index includes lookup provisions from both files, not abstention examples.
BM25 indexes citation metadata as well as the provision text. Grounding checks
citation membership only; it does not establish semantic entailment.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re

import numpy as np


ABSTAIN_PHRASE = "Not found in the provided corpus"
DEFAULT_TRAIN = Path("/home/ss/projects/prabhasa-samskrutam/data/sft/law_v3_train.jsonl")
DEFAULT_HELDOUT = Path("/home/ss/projects/prabhasa-samskrutam/data/eval/law_qa_heldout_v3.jsonl")


@dataclass(frozen=True)
class Passage:
    act: str
    section: str
    text: str
    citation: str
    source_id: str | None


@dataclass
class Answer:
    citations: list[tuple[str, str]]
    abstained: bool


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.lower())


def _key(act: str, section: str) -> tuple[str, str]:
    section = re.sub(r"^\s*(?:article|art\.?)(?=\s|\d)", "Article ", section, flags=re.I)
    section = re.sub(r"^\s*(?:section|sec\.?|s\.?)(?=\s|\d)", "Section ", section, flags=re.I)
    return " ".join(_tokens(act)), " ".join(_tokens(section))


def _records(path):
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


class PassageStore:
    def __init__(self, passages: list[Passage]):
        self.passages = list(passages)
        self._lookup = {_key(p.act, p.section): p for p in passages}
        postings = defaultdict(list)
        lengths = []
        for i, passage in enumerate(passages):
            counts = Counter(_tokens(f"{passage.citation} {passage.text}"))
            lengths.append(sum(counts.values()))
            for term, frequency in counts.items():
                postings[term].append((i, frequency))
        self._weights = {}
        n = len(passages)
        average = float(np.mean(lengths)) if n else 1.0
        normalizer = 1.5 * (1 - 0.75 + 0.75 * np.asarray(lengths) / (average or 1))
        for term, entries in postings.items():
            ids = np.asarray([i for i, _ in entries], dtype=int)
            tf = np.asarray([f for _, f in entries], dtype=float)
            idf = math.log1p((n - len(entries) + 0.5) / (len(entries) + 0.5))
            self._weights[term] = (ids, idf * tf * 2.5 / (tf + normalizer[ids]))

    def __len__(self):
        return len(self.passages)

    @classmethod
    def from_law_files(cls, train_jsonl, heldout_jsonl):
        passages = {}
        for path in (train_jsonl, heldout_jsonl):
            for row in _records(path):
                if row["kind"] != "law_lookup":
                    continue
                act, section = row["act"], row["section"]
                text = re.sub(r"(?:\r?\n)+Citation:[^\r\n]*\s*\Z", "", row["target"]).strip()
                passages.setdefault(_key(act, section), Passage(
                    act, section, text, f"{act}, {section}", row["source_id"]
                ))
        return cls(list(passages.values()))

    def lookup(self, act: str, section: str) -> Passage | None:
        return self._lookup.get(_key(act, section))

    def search(self, query: str, k: int = 5) -> list[Passage]:
        if k < 0:
            raise ValueError("k must be nonnegative")
        scores = np.zeros(len(self.passages))
        for term, count in Counter(_tokens(query)).items():
            if term in self._weights:
                ids, weights = self._weights[term]
                scores[ids] += count * weights
        # Stable ties preserve file order; no lexical matches return no evidence.
        order = np.argsort(-scores, kind="stable")
        return [self.passages[i] for i in order[:k] if scores[i] > 0]


def build_grounded_prompt(question: str, passages: list[Passage]) -> str:
    instruction = ("Answer only from the provisions below, cite as '<act>, <section>', "
                   f"or reply exactly '{ABSTAIN_PHRASE}' if they do not answer the question.")
    blocks = [instruction, *(f"[{p.act}, {p.section}] {p.text}" for p in passages),
              f"Question: {question}\nAnswer:"]
    return "\n\n".join(blocks)


_ACTS = (
    "Constitution of India", "Indian Penal Code", "Indian Evidence Act, 1872",
    "Indian Contract Act", "Bharatiya Nyaya Sanhita", "Bharatiya Nagarik Suraksha Sanhita",
)


def parse_answer(text: str) -> Answer:
    """Recognize act-first citations and 'Section N (Act)' / 'Section N of Act'."""
    matches = []
    for act in _ACTS:
        act_pattern = re.escape(act).replace(r"\ ", r"\s+").replace(",", r",?")
        prefix = r"(?:Article|Art\.?)" if act == _ACTS[0] else r"(?:Section|Sec\.?|s\.?)"
        provision = rf"{prefix}\s*(?P<number>\d+[a-zA-Z]*)(?!\w)"
        for pattern in (
            rf"\b{act_pattern}\s*[,;:]?\s*{provision}",
            rf"\b{provision}\s*(?:[,;(]\s*|of\s+(?:the\s+)?|under\s+(?:the\s+)?){act_pattern}\b",
        ):
            for match in re.finditer(pattern, text, re.I):
                section = ("Article " if act == _ACTS[0] else "Section ") + match["number"].upper()
                matches.append((match.start(), (act, section)))
    citations = list(dict.fromkeys(citation for _, citation in sorted(matches)))
    return Answer(citations, ABSTAIN_PHRASE.casefold() in text.casefold())


def grounded(answer: Answer, passages: list[Passage]) -> bool:
    available = {_key(p.act, p.section) for p in passages}
    return bool(answer.citations or answer.abstained) and all(
        _key(act, section) in available for act, section in answer.citations
    )


def retrieval_recall(store: PassageStore, heldout_jsonl, k: int) -> float:
    """Recall over every held-out item, including unretrievable abstention items."""
    hits = total = 0
    for row in _records(heldout_jsonl):
        total += 1
        hits += _key(row["act"], row["section"]) in {
            _key(p.act, p.section) for p in store.search(row["prompt"], k)
        }
    return hits / total if total else 0.0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", type=Path, default=DEFAULT_TRAIN)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    args = parser.parse_args()
    store = PassageStore.from_law_files(args.train, args.heldout)
    print(f"Passages: {len(store)}; recall denominator includes all held-out items")
    for k in (1, 3, 5, 10):
        print(f"recall@{k}: {retrieval_recall(store, args.heldout, k):.6f}")
