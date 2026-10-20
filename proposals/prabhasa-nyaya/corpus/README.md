# Prabhasa-Nyaya corpus proposal

Step `corpus` (capability `corpus`) for the Prabhasa-Nyaya objective: a legal-reasoning
assistant for Indian jurisprudence that cites the statute or precedent it relied on, says
"I don't know" rather than inventing a citation, and reasons over a **typed Nyaya meaning
graph** (not surface text), so a wrong answer traces back to the inference step that
produced it.

This is a **proposal**, not a ledger entry. Nothing here writes to `research/`, `gates/`,
`pravrudhi_kernel/`, or `.pravrudhi/`, and no number below is a measurement — every
quantity is a stated assumption, marked as such.

## Recipe choice: corpus-curation, not corpus-synthesis

We propose **corpus-curation** over corpus-synthesis. Two reasons specific to this
objective:

1. **"Says it does not know rather than inventing a citation"** is the whole point of the
   assistant. A corpus of LLM-synthesized statutes-and-holdings would train the model to
   produce fluent legal prose that *looks* like a citation. The only way to get a model
   that reliably distinguishes "grounded in a real source" from "plausible-sounding" is to
   train it on real sources with real, checkable provenance.
2. Pravrudhi's own product direction is LoRA-first with no synthetic stand-ins for
   evaluation-shaped data (see project memory `pravrudhi-product-direction`). A legal
   corpus is exactly the case that rule is aimed at: synthetic case law is indistinguishable
   from a hallucination at the point where it matters most.

Synthetic generation still has a place *downstream* — e.g. paraphrasing a real holding into
multiple question phrasings for training the retrieval/graph-construction step — but the
underlying legal facts (which statute, which case, which court, which holding) must always
trace to a primary or authoritative secondary source. `scripts/build_manifest.py` enforces
this by refusing to accept a document into the corpus without a source URL and a fetch
timestamp.

## Corpus size (proposed, not measured)

**Proposed target for this first slice: 6,000–8,000 source documents**, composed
approximately as:

| Bucket | Count (proposed) | Rationale |
|---|---:|---|
| Central Acts in force (Bare Acts) | ~300 | Small in count but each act contributes many sections; this covers the statutory backbone (IPC/BNS, CrPC/BNSS, Evidence Act/BSA, Contract Act, CPC, Constitution, and ~20 other high-traffic acts). |
| Supreme Court judgments | ~3,000 | Binding precedent nationwide (Art. 141); highest inference density per document for the meaning graph. |
| High Court judgments (curated, not exhaustive) | ~3,000 | Persuasive precedent, needed for domain coverage that the SC has not directly ruled on (e.g. routine tenancy, cheque-bounce fact patterns). |
| Law Commission of India reports | ~50 | Doctrinal/legislative-intent context that both statutes and judgments cite. |

Why this size and not "as much as can be scraped": a first corpus this size is small
enough that (a) a human reviewer can audit source provenance and license terms end to end
before anything downstream depends on it, and (b) the typed-graph extraction pipeline
(entities: Act, Section, Case, Court, Bench, Holding, Fact-pattern; relations: interprets,
overrules, follows, distinguishes, cites) can be validated by spot-check at this scale.
Scaling to the 10⁵–10⁶ document range is a follow-on step *after* this slice's extraction
schema and dedup/held-out discipline are validated — proposing that scale now would be
presenting an unmeasured number as if it were sized by evidence. `config/sources.yaml`
`target_counts` block carries these numbers so they can be revised without touching code.

## Source provenance

These are the planned sources, each to be recorded with the exact origin, access method, and
license/reuse terms in `config/sources.yaml`. The source and licence of the Supreme Court copy
actually held are **not confirmed**: the Supreme Court entry below is the plan, not a record of
where that copy came from.

- **India Code** (indiacode.nic.in) — official repository of Central Acts, published by
  the Legislative Department, Ministry of Law and Justice. Public domain / government
  work; used for statute text.
- **Supreme Court of India** judgments (main.sci.gov.in / judgments.ecourts.gov.in) — the
  planned source is the official judgment archive. Source of the copy actually held: not
  confirmed. Licence: not confirmed.
- **High Court** judgment portals (per-state eCourts endpoints) — official archive per
  jurisdiction.
- **Law Commission of India** (lawcommissionofindia.nic.in) — official reports archive.
- **Indian Kanoon** (indiakanoon.org) — planned only as a *fallback fetch mirror* when the
  official portal's document is unreachable or malformed, never as the provenance-of-record;
  a document's manifest entry would still name the official court/act as the authority and
  record Indian Kanoon only as `mirror_used: true`. Indian Kanoon's terms restrict bulk
  scraping — see `config/sources.yaml: indian_kanoon.rate_limit_notice` — so this source is
  capped and rate-limited, and the recipe must not depend on it for coverage.

Every fetch writes one manifest row (`scripts/build_manifest.py`) with: `source_id`,
`origin_url`, `fetch_timestamp_utc`, `sha256` of the raw fetched bytes, `court_or_authority`,
and `license_note`. A document with no manifest row is not part of the corpus — this is the
mechanism that keeps "no invented citation" true at the data layer, not just at the model
layer.

## Domain coverage

`config/domain_coverage.yaml` enumerates the doctrinal areas the corpus must sample from,
so curation does not silently collapse onto whatever is easiest to scrape (routine
cheque-bounce and bail orders dominate raw SC/HC dockets by volume). The areas are:

constitutional law (fundamental rights, writ jurisdiction, federalism), criminal law
(IPC/BNS, CrPC/BNSS, Evidence Act/BSA), contract and civil obligations, property and
transfer of property, family law (personal-law-neutral framing: marriage, succession,
maintenance), tax (direct and indirect), corporate and insolvency, labour and industrial
law, intellectual property, environmental law, consumer protection, and arbitration.

Each area gets a minimum floor (see `min_share` in the YAML) so that, e.g., IP or
environmental law is not crowded out to zero by the sheer docket volume of criminal bail
matters. The curation script reports the realized distribution against these floors; it
does not silently pass if a floor is missed — `scripts/validate_corpus.py` exits non-zero.

## Deduplication

Two layers, both offline and auditable:

1. **Exact duplicate**: `sha256` of normalized text (whitespace-collapsed, citation-header
   stripped) in `scripts/dedupe.py::exact_duplicates`. Catches the same judgment fetched
   twice from different mirrors.
2. **Near duplicate**: shingled MinHash + LSH (`scripts/dedupe.py::near_duplicate_clusters`,
   using the `datasketch` library — declared but not vendored, see
   `scripts/requirements.txt`) over 5-gram shingles of normalized text, Jaccard threshold
   0.85. This catches: (a) an SC judgment reproduced verbatim inside a later HC judgment
   that "sets out the relevant portion in full," and (b) successive official reprints of a
   Bare Act that changed only a footer or a single amended section — these must be
   versioned as *one* Act with multiple dated sections, not duplicated whole documents.

Within a near-duplicate cluster, the highest-authority source wins (official portal over
mirror; later-in-force statute version over earlier, but the earlier version is *kept* as a
distinct dated node in the graph, not discarded — repeal history is itself a fact the
Nyaya graph needs, e.g. "IPC §375 as it stood before the 2013 amendment").

## Separation from held-out evaluation

Two disjoint held-out slices are carved out **before** any downstream extraction or
training touches the corpus, and both are content-hashed so a later re-fetch of the same
document cannot silently re-enter it into the training pool:

1. **Temporal holdout**: all judgments with a decision date on or after the cutoff in
   `config/sources.yaml: holdout.temporal_cutoff` (proposed: 18 months before the corpus
   build date) are reserved for evaluation. This tests generalization to law the model's
   training slice could not have memorized by co-occurrence, and it is the natural test for
   "does the model correctly say it doesn't know" on genuinely novel fact patterns.
2. **Topic-stratified holdout**: within each domain-coverage bucket, a fixed 10% sample
   (seeded, see `holdout.stratified_seed`) is reserved regardless of date, so evaluation
   coverage does not collapse to whatever domain happens to be recent.

`scripts/split_holdout.py` writes `manifest/train.jsonl` and `manifest/heldout.jsonl` as
lists of `source_id` (not document bodies), and `scripts/validate_corpus.py` asserts the two
ID sets are disjoint and that no near-duplicate cluster (per the dedup step above) spans
both sets — a judgment and the HC judgment that quotes it at length must land on the same
side of the split, or the holdout leaks.

## What would count as success

- Every document in the corpus has a manifest row with a resolvable `origin_url`,
  `sha256`, and non-empty `court_or_authority`.
- Realized domain coverage meets every `min_share` floor in `config/domain_coverage.yaml`.
- Zero exact duplicates; near-duplicate clusters are resolved (one canonical member kept,
  cluster recorded, not silently dropped).
- `train.jsonl` ∩ `heldout.jsonl` = ∅, and no near-duplicate cluster spans both.
- The corpus_size actually realized is reported next to the proposed target above with a
  written reason for any material gap (e.g. a state HC portal being unreachable), not
  presented as having met the target by default.

None of the above is asserted as already true in this proposal — the scripts below are how
a future run would produce and check it.

## How to run this (as configured, not yet executed)

```bash
# 1. Fetch raw documents per config/sources.yaml, write manifest rows.
uv run python proposals/prabhasa-nyaya/corpus/scripts/fetch_sources.py \
    --config proposals/prabhasa-nyaya/corpus/config/sources.yaml \
    --out proposals/prabhasa-nyaya/corpus/manifest/raw/

# 2. Build/refresh the provenance manifest from fetched documents.
uv run python proposals/prabhasa-nyaya/corpus/scripts/build_manifest.py \
    --raw-dir proposals/prabhasa-nyaya/corpus/manifest/raw/ \
    --out proposals/prabhasa-nyaya/corpus/manifest/manifest.jsonl

# 3. Deduplicate (exact + near-duplicate clustering).
uv run python proposals/prabhasa-nyaya/corpus/scripts/dedupe.py \
    --manifest proposals/prabhasa-nyaya/corpus/manifest/manifest.jsonl \
    --out proposals/prabhasa-nyaya/corpus/manifest/dedup_clusters.jsonl

# 4. Split train / held-out (temporal + stratified), disjoint by dedup cluster.
uv run python proposals/prabhasa-nyaya/corpus/scripts/split_holdout.py \
    --manifest proposals/prabhasa-nyaya/corpus/manifest/manifest.jsonl \
    --dedup-clusters proposals/prabhasa-nyaya/corpus/manifest/dedup_clusters.jsonl \
    --config proposals/prabhasa-nyaya/corpus/config/sources.yaml \
    --out-dir proposals/prabhasa-nyaya/corpus/manifest/

# 5. Validate against the success criteria above; exits non-zero on any violation.
uv run python proposals/prabhasa-nyaya/corpus/scripts/validate_corpus.py \
    --manifest proposals/prabhasa-nyaya/corpus/manifest/manifest.jsonl \
    --domain-coverage proposals/prabhasa-nyaya/corpus/config/domain_coverage.yaml \
    --train proposals/prabhasa-nyaya/corpus/manifest/train.jsonl \
    --heldout proposals/prabhasa-nyaya/corpus/manifest/heldout.jsonl \
    --dedup-clusters proposals/prabhasa-nyaya/corpus/manifest/dedup_clusters.jsonl
```

`scripts/requirements.txt` lists the extra dependencies (`pyyaml`, `datasketch`,
`requests`) this recipe would need beyond the base environment; none are installed as part
of this proposal.

## Explicit non-goals of this step

- This step does not build the typed Nyaya meaning graph itself (entity/relation
  extraction over the curated documents) — that is downstream of `corpus` and consumes
  `prepared_corpus` as an input, per the pipeline this step belongs to.
- This step does not fine-tune or evaluate a model.
- This step does not claim network access has been exercised; `fetch_sources.py` is a
  runnable scaffold against the sources named above, not a record of documents already
  fetched.
