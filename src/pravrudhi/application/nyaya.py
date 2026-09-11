"""Prabhasa-nyaya in the product: a question of Indian law, answered from sources, and checked.

This is the objective `prabhasa-nyaya` states -- "answers a question of law with the statute or precedent it
relied on, and says it does not know rather than inventing a citation" -- delivered as a surface a user can
open today, rather than as a benchmark the loop trains against. The two are joined, not the same: a night
improves the owned model; this module lets any model the user can reach answer, and verifies the answer the
way `prabhasa-nyaya` (the sibling repository) frames it. A verdict here is a *check of an answer against the
sources it was given*, never a statement about the law.

Three honesty rules, enforced in code rather than asked of the model:

* **A citation is checked against the corpus, mechanically.** Every `[IPC/Section N]` the answer names is
  either among the sources it was shown (`licensed`), elsewhere in the corpus but not shown (`unshown`), or
  nowhere (`invented`). An invented citation is the case the whole objective exists for, and it is found by
  string comparison, not by asking a model whether it looks real.
* **Abstention is a first-class outcome.** A reply that says the sources do not cover the question is
  recorded as `abstained`, not as a wrong answer.
* **Nothing here is evidence.** An ask is written under `research/nyaya/asks/` with provenance `agama`
  (testimony), and never to the ledger. Comparing vendors is one of the outputs -- the sibling repository's
  own framing -- and a comparison of a handful of asks is an anecdote, which the record says.

Retrieval is BM25 over the statute corpus shipped under `assets/nyaya/` plus any `research/nyaya/corpus/*.json`
the user adds in the same shape. No embedding model, no network, deterministic: the same question retrieves the
same sources, so a verdict can be reproduced.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.application import panel

ASSET_DIR = Path(__file__).resolve().parent.parent / "assets" / "nyaya"

#: How a source is named in a prompt and cited in a reply. The id is the corpus document id verbatim, so a
#: citation can be checked by equality and nothing has to be inferred from prose.
CITE = re.compile(r"\[([A-Za-z]+/Section [0-9]+[A-Za-z]?(?:\([0-9a-z]+\))?)\]")

ABSTAIN = re.compile(
    r"(?i)\b(I do not know|I don't know|do not cover|does not cover|cannot be answered from the sources|insufficient sources)"
)

#: Vendors the product offers for an ask, in the order the page lists them. All are `panel.VENDORS`; a vendor
#: whose credential or binary is missing is reported as unavailable rather than dropped.
DEFAULT_VENDORS = ("claude-cli", "codex-cli", "qwen-dashscope", "glm-local")

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class Document:
    id: str
    act: str
    section: str
    title: str
    text: str

    def as_source(self) -> str:
        return f"[{self.id}] {self.act}, {self.section} -- {self.title}: {self.text}"


@dataclass
class Corpus:
    """The statutes an answer may be grounded in, with where each came from."""

    documents: list[Document]
    sources: list[dict[str, Any]]
    expansions: dict[str, list[str]] = field(default_factory=dict, repr=False)
    _df: Counter[str] = field(default_factory=Counter, repr=False)
    _tf: list[Counter[str]] = field(default_factory=list, repr=False)
    _len: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        for d in self.documents:
            toks = _tokens(f"{d.section} {d.title} {d.text}")
            tf = Counter(toks)
            self._tf.append(tf)
            self._len.append(len(toks))
            self._df.update(tf.keys())

    @property
    def by_id(self) -> dict[str, Document]:
        return {d.id: d for d in self.documents}

    def retrieve(self, question: str, k: int = 6) -> list[tuple[Document, float]]:
        """BM25 (k1=1.5, b=0.75). A section number named in the question is a strong signal on its own, so a
        query token that is exactly a section id gets that document first."""
        n = len(self.documents)
        if n == 0:
            return []
        avg = sum(self._len) / n
        q = _tokens(expand(question, self.expansions))
        scores = [0.0] * n
        for i, tf in enumerate(self._tf):
            s = 0.0
            for t in q:
                if t not in tf:
                    continue
                idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
                f = tf[t]
                s += idf * (f * 2.5) / (f + 1.5 * (1 - 0.75 + 0.75 * self._len[i] / avg))
            scores[i] = s
        named = {m.group(0).lower() for m in re.finditer(r"section\s+[0-9]+[a-z]?", question.lower())}
        for i, d in enumerate(self.documents):
            if d.section.lower() in named:
                scores[i] += 100.0
        order = sorted(range(n), key=lambda i: -scores[i])
        return [(self.documents[i], round(scores[i], 4)) for i in order[:k] if scores[i] > 0]


def _load_file(path: Path) -> tuple[list[Document], dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    docs = [Document(**{k: str(d.get(k, "")) for k in ("id", "act", "section", "title", "text")}) for d in raw["documents"]]
    return docs, {"file": path.name, **(raw.get("source") or {}), "documents": len(docs)}


def load_corpus(root: Path | None = None) -> Corpus:
    """The shipped statutes plus the user's own additions under `research/nyaya/corpus/`."""
    files = sorted(p for p in ASSET_DIR.glob("*.json") if p.name != LEXICON.name)
    if root is not None:
        files += sorted((Path(root) / "research" / "nyaya" / "corpus").glob("*.json"))
    docs: list[Document] = []
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in files:
        d, meta = _load_file(f)
        docs.extend(x for x in d if x.id not in seen)
        seen.update(x.id for x in d)
        sources.append(meta)
    return Corpus(docs, sources, expansions=load_lexicon())


LEXICON = ASSET_DIR / "lexicon.json"


def load_lexicon() -> dict[str, list[str]]:
    """Lay word -> the Code's words. A question says "dies"; Section 304 says "culpable homicide"."""
    if not LEXICON.exists():
        return {}
    raw = json.loads(LEXICON.read_text(encoding="utf-8"))
    return {str(k).lower(): [str(x) for x in v] for k, v in (raw.get("expansions") or {}).items()}


def expand(question: str, expansions: dict[str, list[str]]) -> str:
    """The question plus every expansion whose key appears in it. Deterministic; the added terms are recorded
    nowhere the model sees, so the prompt stays the user's own words."""
    q = question.lower()
    extra = [t for key, terms in expansions.items() if key in q for t in terms]
    return question if not extra else question + " " + " ".join(dict.fromkeys(extra))


def grounded_prompt(question: str, hits: list[Document]) -> str:
    sources = "\n".join(d.as_source() for d in hits) or "(no source matched the question)"
    return (
        "You are answering a question of Indian law for a reader who will check every citation.\n\n"
        f"SOURCES (the only authorities you may rely on):\n{sources}\n\n"
        f"QUESTION:\n{question}\n\n"
        "Rules:\n"
        "1. Rely only on the SOURCES above. Cite each one you rely on inline, exactly as its bracketed id, "
        "e.g. [IPC/Section 302]. Do not cite anything that is not listed.\n"
        "2. If the sources do not cover the question, reply exactly: "
        '"I do not know: the provided sources do not cover this." and then say in one sentence what kind '
        "of source would be needed.\n"
        "3. Structure: ANSWER (2-6 sentences, citing as you go), then CITATIONS: a comma-separated list of the "
        "ids you relied on, then CONFIDENCE: high | medium | low.\n"
        "4. No preamble, no disclaimers beyond rule 2."
    )


@dataclass
class Citation:
    id: str
    status: str  # licensed | unshown | invented


@dataclass
class VendorAnswer:
    vendor: str
    model: str
    text: str
    wall_s: float
    citations: list[Citation]
    verdict: str  # licensed | unlicensed | invented_citation | abstained | error
    confidence: str
    error: str | None = None
    audit: dict[str, Any] | None = None


def check_answer(text: str, shown: list[Document], corpus: Corpus) -> tuple[list[Citation], str, str]:
    """The mechanical half of the verification: citations against the corpus, abstention by its own words."""
    shown_ids = {d.id for d in shown}
    known = corpus.by_id
    cites: list[Citation] = []
    for cid in dict.fromkeys(CITE.findall(text)):
        status = "licensed" if cid in shown_ids else "unshown" if cid in known else "invented"
        cites.append(Citation(cid, status))
    m = re.search(r"(?i)CONFIDENCE:\s*(high|medium|low)", text)
    confidence = m.group(1).lower() if m else "unstated"
    if any(c.status == "invented" for c in cites):
        verdict = "invented_citation"
    elif ABSTAIN.search(text) and not any(c.status == "licensed" for c in cites):
        verdict = "abstained"
    elif any(c.status == "licensed" for c in cites):
        verdict = "licensed"
    else:
        verdict = "unlicensed"
    return cites, verdict, confidence


AUDIT_PROMPT = (
    "You are reviewing a legal answer for errors in its reasoning. Read the sources, then the answer.\n\n"
    "SOURCES:\n{sources}\n\nANSWER:\n{answer}\n\n"
    "Treat the facts as the answer states them. Decide whether the answer contains an error of legal "
    "reasoning: an authority that says something other than what is claimed, a required element skipped, an "
    "immaterial fact treated as decisive, a false analogy, or an authority relied on that the sources do not "
    "contain. Reply in exactly this form:\nVERDICT: ERROR or CORRECT\nSPAN: the exact words of the answer "
    "that carry the error, or NONE\nCLASS: one of invented_citation, wrong_authority, missing_element, "
    "immaterial_fact, false_analogy, none\nWHY: one sentence."
)

_VERDICT = re.compile(r"(?i)VERDICT:\s*(ERROR|CORRECT)")
_SPAN = re.compile(r"(?i)SPAN:\s*(.+)")
_CLASS = re.compile(r"(?i)CLASS:\s*([a-z_]+)")
_WHY = re.compile(r"(?i)WHY:\s*(.+)")


def parse_audit(text: str) -> dict[str, Any]:
    v, s, c, w = _VERDICT.search(text), _SPAN.search(text), _CLASS.search(text), _WHY.search(text)
    return {
        "verdict": v.group(1).upper() if v else "UNPARSED",
        "span": s.group(1).strip() if s else None,
        "class": c.group(1).lower() if c else None,
        "why": w.group(1).strip() if w else None,
        "raw": text,
    }


def available_vendors(root: Path, ids: tuple[str, ...] = DEFAULT_VENDORS) -> list[dict[str, Any]]:
    """Which vendors can be asked from this install, and why not when they cannot."""
    import shutil

    out: list[dict[str, Any]] = []
    for vid in ids:
        v = panel.VENDORS[vid]
        why = None
        if v.interface == "cli" and shutil.which(v.model) is None:
            why = f"{v.model} is not installed"
        elif v.interface == "openai_compat" and v.credential and not v.key(root):
            why = f"no key for {v.provider or v.credential}"
        elif v.interface == "local_gguf":
            why = "local weights are not served here yet"
        out.append({"id": vid, "model": v.model, "interface": v.interface, "available": why is None, "why": why, "note": v.note})
    return out


def _ask_one(vendor: panel.Vendor, prompt: str) -> tuple[str, float, str | None]:
    try:
        a = panel.ask_vendor(vendor, prompt)
        return a.text, a.wall_s, None
    except Exception as e:  # a vendor that fails is recorded, never dropped
        return "", 0.0, str(e)[-400:]


@dataclass
class Ask:
    id: str
    asked_at: str
    question: str
    sources: list[dict[str, Any]]
    answers: list[VendorAnswer]
    provenance: str = "agama"
    note: str = "A check of each answer against the sources shown to it; not a statement about the law, and not evidence."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def asks_dir(root: Path) -> Path:
    return Path(root) / "research" / "nyaya" / "asks"


def ask(
    root: Path,
    question: str,
    vendors: tuple[str, ...] = ("claude-cli",),
    *,
    k: int = 8,
    checker: str | None = None,
    ask_fn: panel.AskFn | None = None,
    corpus: Corpus | None = None,
) -> Ask:
    """Answer one question with each vendor, in parallel, and check every answer. Writes the record."""
    question = question.strip()
    if not question:
        raise ValueError("an empty question asks nothing")
    corpus = corpus or load_corpus(root)
    hits = [d for d, _ in corpus.retrieve(question, k=k)]
    prompt = grounded_prompt(question, hits)
    chosen = panel.load_vendors(vendors)
    fn = ask_fn or panel.ask_vendor

    def one(v: panel.Vendor) -> VendorAnswer:
        try:
            a = fn(v, prompt)
            text, wall, err = a.text, a.wall_s, None
        except Exception as e:
            text, wall, err = "", 0.0, str(e)[-400:]
        if err:
            return VendorAnswer(v.id, v.model, "", wall, [], "error", "unstated", error=err)
        cites, verdict, conf = check_answer(text, hits, corpus)
        return VendorAnswer(v.id, v.model, text, round(wall, 2), cites, verdict, conf)

    with ThreadPoolExecutor(max_workers=max(1, len(chosen))) as pool:
        answers = list(pool.map(one, chosen))

    if checker:
        cv = panel.load_vendors((checker,))[0]
        sources = "\n".join(d.as_source() for d in hits)
        for ans in answers:
            if ans.verdict in ("error", "abstained"):
                continue
            try:
                res = fn(cv, AUDIT_PROMPT.format(sources=sources, answer=ans.text))
                ans.audit = {"checker": cv.id, **parse_audit(res.text)}
            except Exception as e:
                ans.audit = {"checker": cv.id, "verdict": "UNAVAILABLE", "why": str(e)[-200:]}

    rec = Ask(
        id=f"ask-{uuid.uuid4().hex[:8]}",
        asked_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        question=question,
        sources=[{"id": d.id, "act": d.act, "section": d.section, "title": d.title} for d in hits],
        answers=answers,
    )
    out = asks_dir(root)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rec.id}.json").write_text(json.dumps(rec.to_dict(), indent=1, ensure_ascii=False) + "\n")
    return rec


def audit(
    root: Path, sources: str, answer: str, checker: str = "claude-cli", *, ask_fn: panel.AskFn | None = None
) -> dict[str, Any]:
    """The auditor on its own: a user's answer and sources, one checker, one structured verdict."""
    cv = panel.load_vendors((checker,))[0]
    fn = ask_fn or panel.ask_vendor
    res = fn(cv, AUDIT_PROMPT.format(sources=sources.strip(), answer=answer.strip()))
    rec = {"checker": cv.id, **parse_audit(res.text), "wall_s": round(res.wall_s, 2), "provenance": "agama"}
    out = Path(root) / "research" / "nyaya" / "audits"
    out.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((sources + answer).encode()).hexdigest()[:10]
    (out / f"audit-{digest}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
    return rec


def recent_asks(root: Path, limit: int = 20) -> list[dict[str, Any]]:
    d = asks_dir(root)
    if not d.exists():
        return []
    files = sorted(d.glob("ask-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    return [json.loads(p.read_text()) for p in files]
