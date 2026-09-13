"""Local provision retrieval and citation checks; no model dependencies.

The index includes lookup provisions from both files, not abstention examples.
BM25 indexes citation metadata, the provision TITLE, and the provision text.
Grounding checks citation membership only; it does not establish semantic
entailment.

2026-09-13 pivot fix (F13 in
docs/research-spikes/2026-09-13-ttt-rsi-track-b/FIXES-FOR-MAIN-SESSIONS.md,
diagnosed by the main session): `Passage.text` used to be the `law_lookup`
body ONLY. But `law_citation_retrieval` asks "Which provision states:
'<TITLE>'?" and `law_cite_to_title` asks for the title outright -- and the
section TITLE is a separate field (the `law_cite_to_title` record's own
`target` for the same (act, section)), not part of the body text. A passage
therefore carried no information a citation-kind question could actually
match against: a forced-in gold passage and a random one were observationally
identical for those two kinds, the training label was uncorrelated with
anything visible in context, and (as the round-1/1b SFT runs demonstrated)
the model rationally learned to prefer the one thing that WAS a reliable,
low-loss signal -- abstaining. `Passage` now carries a `title` field (looked
up from the `law_cite_to_title` record sharing the same (act, section)); it
is rendered into both the BM25 index and every built prompt, and is never
truncated (`evaluate.truncate_passages` only ever shortens `.text`).

Disclosure: `law_cite_to_title` records exist for both the train AND
held-out splits, so a held-out item's own title is now part of the shared
corpus (available for retrieval on ANY query, not just its own). This is the
same decision already made for `law_lookup` bodies (`from_law_files` already
pooled passages from both splits) -- section headings, like section bodies,
are part of the statute text itself, not a training-set-only construct, so
including a held-out section's heading in the corpus is not different in
kind from including its body. It does mean a `law_cite_to_title` held-out
item's own gold answer is now retrievable verbatim as a title match; this is
disclosed here and in the report rather than left implicit.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re

import numpy as np


ABSTAIN_PHRASE = "Not found in the provided corpus"
# Track B checkout root. Override with TRACKB_ROOT (e.g. /trackB inside the ttt-lab container).
TRACKB_ROOT = Path(os.environ.get("TRACKB_ROOT", str(Path.home() / "projects/prabhasa-samskrutam")))
DEFAULT_TRAIN = TRACKB_ROOT / "data/sft/law_v3_train.jsonl"
DEFAULT_HELDOUT = TRACKB_ROOT / "data/eval/law_qa_heldout_v3.jsonl"


@dataclass(frozen=True)
class Passage:
    act: str
    section: str
    text: str
    citation: str
    source_id: str | None
    title: str = ""


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
            counts = Counter(_tokens(f"{passage.citation} {passage.title} {passage.text}"))
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
        # Titles come from law_cite_to_title records (both splits -- see the
        # module docstring's disclosure) sharing the same (act, section) as a
        # law_lookup body; a passage's title is a property of the statute
        # section, not of the split it happened to be sampled into.
        titles: dict[tuple[str, str], str] = {}
        for path in (train_jsonl, heldout_jsonl):
            for row in _records(path):
                if row["kind"] == "law_cite_to_title":
                    titles.setdefault(_key(row["act"], row["section"]), row["target"].strip())

        passages = {}
        for path in (train_jsonl, heldout_jsonl):
            for row in _records(path):
                if row["kind"] != "law_lookup":
                    continue
                act, section = row["act"], row["section"]
                text = re.sub(r"(?:\r?\n)+Citation:[^\r\n]*\s*\Z", "", row["target"]).strip()
                title = titles.get(_key(act, section), "")
                passages.setdefault(_key(act, section), Passage(
                    act, section, text, f"{act}, {section}", row["source_id"], title
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


def _passage_body(p: Passage) -> str:
    """Title + body, title never truncated (truncation only ever shortens
    `.text` -- see `evaluate.truncate_passages`). Backward compatible with a
    title-less `Passage` (title="" default): renders as before."""
    return f"{p.title}. {p.text}" if p.title else p.text


_DEFAULT_INSTRUCTION = ("Answer only from the provisions below, cite as '<act>, <section>', "
                        f"or reply exactly '{ABSTAIN_PHRASE}' if they do not answer the question.")


def build_grounded_prompt(question: str, passages: list[Passage], instruction: str | None = None) -> str:
    """`instruction` defaults to the original long form (mentions abstention
    in the prompt text itself); the compact-context pivot
    (`evaluate.PROMPT_CONFIG`) passes a short instruction instead, since
    abstention is now a calibrated harness decision (F14) rather than
    something the model is asked to produce in free text."""
    instruction = _DEFAULT_INSTRUCTION if instruction is None else instruction
    blocks = [instruction, *(f"[{p.act}, {p.section}] {_passage_body(p)}" for p in passages),
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
