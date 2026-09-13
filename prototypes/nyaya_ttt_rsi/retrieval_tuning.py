"""CPU-only, train-selected field BM25; run with ``python -m ...retrieval_tuning``.

Omitting field weights retains pooled BM25 exactly. Supplying any field weight
enables the weighted sum of independently normalized title/header/body BM25.
Equal field weights are NOT mathematically equivalent to pooled BM25.
Only corpus construction reads both splits before model selection; abbreviation
discovery, exclusion decisions, and configuration selection use train prompts.
"""

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from itertools import product
import json
import math
from pathlib import Path
import re
import time

import numpy as np

from .retrieval import DEFAULT_HELDOUT, DEFAULT_TRAIN, PassageStore, _key, _records


def discover_abbreviations(prompts, acts):
    """Keep only observed citation aliases and observed initials of real acts.

    No speculative act names or abbreviation expansions are added. Initials
    with/without connecting words cover, for example, an observed IPC or BNSS.
    """
    text = "\n".join(prompts)
    aliases = {}
    for short, full in (("Art.", "Article"), ("Sec.", "Section"), ("S.", "Section")):
        if re.search(r"(?<!\w)" + re.escape(short) + r"\s*\d", text, re.I):
            aliases[short] = full
    for act in sorted(set(acts)):
        words = re.findall(r"[^\W\d_]+", act)
        for selected in (words, [w for w in words if w.lower() not in {"of", "the", "and"}]):
            short = "".join(w[0] for w in selected).upper()
            if len(short) >= 2 and re.search(r"(?<!\w)" + re.escape(short) + r"(?!\w)", text, re.I):
                aliases[short] = act
    return aliases


class TunedStore(PassageStore):
    def __init__(self, passages, *, k1=1.5, b=0.75, w_title=None,
                 w_header=None, w_body=None, lowercase=True,
                 strip_punctuation=True, expand_abbreviations=False,
                 abbreviations=None, exact_boost=0.0):
        if k1 <= 0 or not 0 <= b <= 1 or exact_boost < 0:
            raise ValueError("Require k1 > 0, 0 <= b <= 1, and nonnegative boost")
        self.passages = list(passages)
        self._lookup = {_key(p.act, p.section): p for p in self.passages}
        self.lowercase = lowercase
        self.strip_punctuation = strip_punctuation
        self.expand_abbreviations = expand_abbreviations
        self.abbreviations = dict(abbreviations or {})
        self.exact_boost = exact_boost
        self.field_weights = None if all(w is None for w in (w_title, w_header, w_body)) else tuple(
            1.0 if w is None else float(w) for w in (w_title, w_header, w_body))
        if self.field_weights is not None and any(w < 0 for w in self.field_weights):
            raise ValueError("Field weights must be nonnegative")
        self._alias_patterns = []
        for short, full in sorted(self.abbreviations.items(), key=lambda item: -len(item[0])):
            citation_alias = full in {"Section", "Article"}
            tail = r"\s*(?=\d)" if citation_alias else r"(?!\w)"
            replacement = full + " " if citation_alias else full
            self._alias_patterns.append((re.compile(r"(?<!\w)" + re.escape(short) + tail, re.I), replacement))
        if self.field_weights is None:
            fields = [[f"{p.citation} {p.title} {p.text}" for p in self.passages]]
        else:
            fields = [[p.title for p in self.passages],
                      [f"{p.act} {p.section}" for p in self.passages],
                      [p.text for p in self.passages]]
        self._fields = [self._index(field, k1, b) for field in fields]
        self._header_ids = defaultdict(list)
        self._act_ids = defaultdict(list)
        for i, p in enumerate(self.passages):
            self._header_ids[_key(p.act, p.section)[1]].append(i)
            self._act_ids[" ".join(self._lex(p.act.lower(), force_plain=True))].append(i)

    def normalize_query(self, query):
        if self.expand_abbreviations:
            for pattern, replacement in self._alias_patterns:
                query = pattern.sub(replacement, query)
        return query.lower() if self.lowercase else query

    def _lex(self, text, force_plain=False):
        if self.lowercase:
            text = text.lower()
        pattern = r"[^\W_]+" if self.strip_punctuation or force_plain else r"[^\W_]+|[^\w\s]"
        return re.findall(pattern, text)

    def _index(self, documents, k1, b):
        postings = defaultdict(list)
        lengths = []
        for i, document in enumerate(documents):
            counts = Counter(self._lex(document))
            lengths.append(sum(counts.values()))
            for term, frequency in counts.items():
                postings[term].append((i, frequency))
        n = len(documents)
        average = float(np.mean(lengths)) if n else 1.0
        normalizer = k1 * (1 - b + b * np.asarray(lengths) / (average or 1))
        weights = {}
        for term, entries in postings.items():
            ids = np.asarray([i for i, _ in entries], dtype=int)
            tf = np.asarray([f for _, f in entries], dtype=float)
            idf = math.log1p((n - len(entries) + 0.5) / (len(entries) + 0.5))
            weights[term] = ids, idf * tf * (k1 + 1) / (tf + normalizer[ids])
        return weights

    def components(self, query):
        normalized = self.normalize_query(query)
        counts = Counter(self._lex(normalized))
        scores = np.zeros((len(self._fields), len(self)))
        for j, field in enumerate(self._fields):
            for term, count in counts.items():
                if term in field:
                    ids, weights = field[term]
                    scores[j, ids] += count * weights
        return scores, self._exact_matches(normalized)

    def _exact_matches(self, query):
        matches = np.zeros(len(self))
        plain = " " + " ".join(self._lex(query.lower(), force_plain=True)) + " "
        acts = [ids for act, ids in self._act_ids.items() if " " + act + " " in plain]
        allowed = {i for ids in acts for i in ids} if acts else None
        for label, number in re.findall(r"\b(Article|Section)\s+(\d+[a-zA-Z]*)(?!\w)", query, re.I):
            for i in self._header_ids.get(f"{label.lower()} {number.lower()}", []):
                if allowed is None or i in allowed:
                    matches[i] = 1.0
        return matches

    def search(self, query, k=5):
        if k < 0:
            raise ValueError("k must be nonnegative")
        fields, exact = self.components(query)
        scores = fields[0] if self.field_weights is None else sum(
            w * field for w, field in zip(self.field_weights, fields))
        scores = scores + self.exact_boost * exact
        order = np.argsort(-scores, kind="stable")
        return [self.passages[i] for i in order[:k] if scores[i] > 0]


@dataclass(frozen=True)
class Config:
    k1: float
    b: float
    w_title: float
    w_header: float
    w_body: float
    expand_abbreviations: bool
    exact_boost: float
    lowercase: bool = True
    strip_punctuation: bool = True


KS = (4, 8, 16, 32)


def eligible_rows(rows, store):
    """Exclude queries containing their gold passage's entire nonempty body.

    Case/whitespace normalization makes the literal-containment filter robust;
    section-title quotations stay eligible, as required by the task definition.
    """
    result = []
    for row in rows:
        passage = store.lookup(row["act"], row["section"])
        body = " ".join(passage.text.lower().split()) if passage else ""
        if body and body in " ".join(row["prompt"].lower().split()):
            continue
        result.append(row)
    return result


def summarize(rows, hits):
    kinds = np.asarray([r["kind"] for r in rows])
    result = {}
    for kind in ["overall", *sorted(set(kinds))]:
        mask = np.ones(len(rows), dtype=bool) if kind == "overall" else kinds == kind
        recalls = hits[mask].mean(axis=0) if mask.any() else np.zeros(len(KS))
        result[kind] = {"n": int(mask.sum()),
                        **{f"recall@{k}": float(v) for k, v in zip(KS, recalls)},
                        "objective": float((recalls[0] + recalls[2]) / 2)}
    return result


def evaluate(store, rows):
    hits = np.zeros((len(rows), len(KS)), dtype=bool)
    for i, row in enumerate(rows):
        gold = _key(row["act"], row["section"])
        retrieved = [_key(p.act, p.section) for p in store.search(row["prompt"], max(KS))]
        rank = retrieved.index(gold) + 1 if gold in retrieved else math.inf
        hits[i] = [rank <= k for k in KS]
    return summarize(rows, hits)


def tune(passages, rows, aliases):
    """144 variants; reuse component scores in bounded 128-query batches.

    Gold ranks count greater scores plus earlier tied documents, exactly matching
    stable search order, without sorting every query/configuration combination.
    """
    weights = ((1, 1, 1), (3, 1, 1), (1, 3, 1), (3, 3, 1),
               (6, 3, 0.5), (6, 6, 0.25))
    configs = [Config(k1, b, *w, expand, boost)
               for k1, b, expand, w, boost in product(
                   (0.8, 1.5), (0.25, 0.75), (False, True), weights, (0.0, 10.0, 50.0))]
    keys = {_key(p.act, p.section): i for i, p in enumerate(passages)}
    gold = np.asarray([keys.get(_key(r["act"], r["section"]), -1) for r in rows])
    doc_ids = np.arange(len(passages))[None, :]
    results = []
    for k1, b, expand in product((0.8, 1.5), (0.25, 0.75), (False, True)):
        group = [c for c in configs if (c.k1, c.b, c.expand_abbreviations) == (k1, b, expand)]
        store = TunedStore(passages, **asdict(group[0]), abbreviations=aliases)
        hits = [np.zeros((len(rows), len(KS)), dtype=bool) for _ in group]
        for start in range(0, len(rows), 128):
            batch = rows[start:start + 128]
            components = [store.components(r["prompt"]) for r in batch]
            fields = np.stack([c[0] for c in components], axis=1)
            exact = np.stack([c[1] for c in components])
            ids = gold[start:start + len(batch)]
            for c, h in zip(group, hits):
                scores = (c.w_title * fields[0] + c.w_header * fields[1]
                          + c.w_body * fields[2] + c.exact_boost * exact)
                gold_scores = scores[np.arange(len(batch)), np.maximum(ids, 0)][:, None]
                rank = 1 + np.sum((scores > gold_scores) | (
                    (scores == gold_scores) & (doc_ids < ids[:, None])), axis=1)
                valid = (ids >= 0) & (gold_scores[:, 0] > 0)
                h[start:start + len(batch)] = valid[:, None] & (rank[:, None] <= np.asarray(KS))
        results.extend({"config": asdict(c), "metrics": summarize(rows, h)} for c, h in zip(group, hits))
        print(f"Train group k1={k1} b={b} expansion={expand} complete", flush=True)
    # Stable generation order breaks objective ties, without consulting held-out.
    return sorted(results, key=lambda r: -r["metrics"]["overall"]["objective"])


def main():
    started = time.monotonic()
    baseline = PassageStore.from_law_files(DEFAULT_TRAIN, DEFAULT_HELDOUT)
    raw_train = list(_records(DEFAULT_TRAIN))
    train = eligible_rows(raw_train, baseline)
    aliases = discover_abbreviations((r["prompt"] for r in raw_train), (r["act"] for r in raw_train))
    print(f"Corpus={len(baseline)}; train={len(train)}; excluded={len(raw_train)-len(train)}; aliases={aliases}", flush=True)
    before = evaluate(baseline, train)
    ranked = tune(baseline.passages, train, aliases)
    print("Best five on train (objective = mean recall@4, recall@16):", flush=True)
    for rank, result in enumerate(ranked[:5], 1):
        print(f"{rank}: {result['metrics']['overall']['objective']:.6f} {result['config']}", flush=True)
        print("   " + " ".join(f"{kind}={values['objective']:.6f}" for kind, values in result["metrics"].items() if kind != "overall"), flush=True)
    # Selection is frozen above. This is the first access to held-out prompts.
    selected = TunedStore(baseline.passages, **ranked[0]["config"], abbreviations=aliases)
    train_check = evaluate(selected, train)
    if train_check != ranked[0]["metrics"]:
        raise AssertionError("Batched tuning metrics disagree with public search")
    raw_heldout = list(_records(DEFAULT_HELDOUT))
    heldout = eligible_rows(raw_heldout, baseline)
    heldout_baseline = evaluate(baseline, heldout)
    heldout_tuned = evaluate(selected, heldout)
    elapsed = time.monotonic() - started
    output = {
        "configurations_evaluated": len(ranked), "passages": len(baseline),
        "abbreviations": aliases, "train_baseline": before,
        "train_top5": ranked[:5], "best_config": ranked[0]["config"],
        "heldout_baseline": heldout_baseline, "heldout_tuned": heldout_tuned,
        "excluded_full_body_queries": {"train": len(raw_train)-len(train), "heldout": len(raw_heldout)-len(heldout)},
        "wall_seconds": elapsed,
        "protocol": "144 train-only variants; objective is micro mean of recall@4 and recall@16; stable grid order resolves ties. Single selected variant evaluated once on held-out alongside pooled baseline. Both splits supply corpus bodies and titles. Absent gold/abstention rows count as misses. Case/whitespace-normalized full gold body containment excluded in both splits. No held-out prompt informs aliases or selection.",
    }
    directory = Path(__file__).parent / "runs" / "retrieval_tuning"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "results.json"
    if destination.exists():
        raise FileExistsError("Refusing to overwrite existing results.json")
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2)
        stream.write("\n")
    print("Held-out                       n       @4 base/tuned      @8 base/tuned     @16 base/tuned     @32 base/tuned")
    for kind, metric in heldout_baseline.items():
        values = "  ".join(f"{metric[f'recall@{k}']:.4f}/{heldout_tuned[kind][f'recall@{k}']:.4f}" for k in KS)
        print(f"{kind:28s} {metric['n']:4d}  {values}")
    print(f"Train objective {before['overall']['objective']:.6f} -> {ranked[0]['metrics']['overall']['objective']:.6f}; wall={elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
