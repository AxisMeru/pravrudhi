# Variation evidence

The API `generate(archive, budget=8, rng=rng, blocklist=kernel_hashes)` targets
16 candidates. Uniform selection includes pruned parents. It uses
archive.parent_map and the same canonical JSON/hash identity as propose.py.
It does not use incomparable historical deltas; future score weighting should
use anchor.py scores with a shared difficulty epoch.

The nightly caller is outside the permitted edit scope. It must opt into this
API, append Offspring.payload() as propose rows with its existing writer and
metadata, and supply the actual kernel blocklist. No nightly behavior changed.
Both direct crossover parents appear in rationale and variation_parents;
lineage names the primary parent because archive.py interprets lineage as a
chain. No kernel or live-pool exclusion changes were made.

## Measurements

Seed 713, 300 calls for each operator/grammar, using synthetic valid parents
with contrasting strategies and parameters, validated by the real parsers:

| Grammar | Operator | Draws | Invalid | Duplicate | Accepted | Rejected |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| LoRA | Mutation | 336 | 14 | 22 | 300 | 10.71% |
| Harness | Mutation | 391 | 68 | 23 | 300 | 23.27% |
| LoRA | Block crossover | 614 | 295 | 19 | 300 | 51.14% |
| Harness | Block crossover | 524 | 207 | 17 | 300 | 42.75% |
| LoRA | Field crossover | 621 | 319 | 2 | 300 | 51.69% |
| Harness | Field crossover | 524 | 207 | 17 | 300 | 42.75% |

No calls exhausted 32 attempts. High crossover rejection reflects strategy/family
and prompt-only constraints, costing about two validation draws per acceptance
for these fixtures. No repair occurs. This table tests novelty against parents,
with a fresh seen set each call; it does not claim 300 globally unique children.
Separate batch tests share canonical identities, block an earlier seeded batch,
and obtain 16 distinct non-blocklisted children for an eight-run budget for each
grammar. Forced invalid, duplicate and blocked draws stop at exactly 32 attempts.
The fixture ledger's archive.selection_pressure confirms a binding budget.
No private ledger was accessed; these are not production rejection rates.

Harness has no nested blocks, so its two crossover variants coincide. Mutation
inherits free-form text (no numeric resampling domain); field crossover can
inherit text from either parent. Integer scale draws are rounded log-uniform
samples; unchanged draws are rejected. Other counts are discrete uniform,
continuous fields linear, Literals uniform among remaining values, booleans
flipped. Rationale is excluded from the mutation genome and deduplication.
Short batches remain possible for sparse or saturated archives.

## Validation

10 tests passed. The default uv cache was read-only; a new environment could
not download dependencies due to unavailable DNS. The existing environment ran
the required test file successfully without syncing dependencies:

```
UV_CACHE_DIR=/tmp/pravrudhi-variation-uv \
UV_PROJECT_ENVIRONMENT=/home/ss/projects/pravrudhi/.venv \
PYTHONPATH=src UV_NO_SYNC=1 uv run pytest tests/test_variation.py -q
```

Add `-s` to reproduce the table's raw counts. The automatically created unused
worktree environment was moved to /tmp after the failed dependency download.
