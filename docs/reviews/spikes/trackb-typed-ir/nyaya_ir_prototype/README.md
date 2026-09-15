# nyaya_ir

Offline, Python 3.11+ storage/grammar spike for `nyaya-law-v1`. Runtime uses
only the standard library. Tests require pytest. No reference project is imported.

```python
from nyaya_ir import from_dict, serialize, parse, validate_record, export_reading
record = from_dict(storage_dictionary)
wire = serialize(record)
assert parse(wire, record.prompt) == record
report = validate_record(record)  # structural validity; never certification
lean_expression = export_reading(record)
```

Use tuples for sequence fields in direct dataclass construction. `from_dict`
converts JSON arrays to tuples and rejects unknown fields and scalar coercion.
Prompt, facts, passages, and scope are frozen dataclasses; source text is never
normalized. `Span(source_id, start, end)` slices Python strings by code point,
half-open, with zero-length spans permitted by the proposed schema. Spans take
part in equality and are independent of labels and roles.

## Wire grammar

Lines occur in the following order. `*` means zero or more, `+` means one or
more. Each trace line, ABSTAIN, and ANSWER occurs exactly once. ASCII IDs match
`[A-Za-z_][A-Za-z0-9_]*`. JSON strings handle quotes, backslashes, and newlines.

```text
RECORD <JSON object>                                  # optional storage envelope
NODE <id> <PadarthaCategory> <Sorta> <span>*           # repeated
ROLE <event> <KarakaRole> <filler>                    # repeated
ELEMENT <id> <event> established|negated|unknown <span>*
CLAIM <id> <op> <arg-id> <arg-id> <span>* [BY <rule-id> <premise-id>+]
PRATIJNA <subject> <predicate> true|false
HETU <element-id>*
UDAHARANA <rule-id> @<passage-id>* example=<id>|null
UPANAYA <variable>=<node-id>* premises=<comma-separated-ids>|[]
NIGAMANA proved|not_proved <claim-id>*
HETVABHASA legal-rules-v1 <verdict>|null satpratipaksa=<bool>|null badhita=<bool>|null
CITE <passage-id> <JSON act> <JSON section>             # repeated
ABSTAIN false | true <reason>
ANSWER <JSON string>
AUDIT <JSON object>                                  # optional storage envelope
```

ELEMENT and CLAIM may repeat. `<span>` is `@F1` (entire source) or
`@F1:3:12` (explicit offsets). Canonical serialization always emits explicit
offsets. Legacy unquoted single-token CITE text in examples A/B is accepted.
Blank lines are accepted. Noncanonical accepted text is canonicalized; record
field values and ordering round-trip exactly. Full-record serialization binds
the entire prompt with a SHA-256 envelope digest; passage hashes separately
validate UTF-8 passage bytes. No prompt normalization or replacement occurs.

`RECORD` carries schema_version/id/kind/prompt_sha256. `AUDIT` preserves supervisor
metadata for storage round trips. **Neither is training output:** use
`serialize_target(record)` and `parse_target(text, prompt)` at the model boundary.
The latter rejects envelope fields. Bare `parse` of an example supplies id
`inference` and empty, unassigned audit metadata. The caller computes checker
reports with `validate_record`; preserved audit reports are not trusted.

## Validators and adapters

`validate_record(record, frames={event_id: EventFrame(...)}, registry=Registry(...))`
checks references, span bounds/hashes, signatures, duplicate IDs/bindings,
citations, provenance references, and abstention consistency. Elements share
IDs with element nodes by design. Elements currently require unique IDs across
the record. Claim IDs use a separate nonoverlapping namespace. Forward/cyclic
claim provenance is rejected.

Default event frames impose no role count or participant-sort limit. A caller
can set `Cardinality(minimum, maximum)` and `allowed_sorts` per role and event;
this supports joint agents and conduct-as-object. Event nodes must be KRIYA/act.

`Registry` is supplied independently by the caller. Its defaults recognize
only the two example rule names and the example predicate, without implementing
their inference semantics. It maps literal IDs for `provides` to strings and
for `cites` to structured `Cite(volume, reporter, page)` values. This resolves
the proposed schema's ID-only arguments without inventing a citation sort.
`provides` resolves its provision's act/section from node passage spans and
rejects ambiguous identities. Remaining arguments resolve typed graph nodes.
`absent` takes object and locus IDs and exports a typed Lean Abhava. Claim
`rule_id`/`premise_ids` are explicit extensions preserving the example's BY line.

`export_reading` emits a fully qualified Lean Reading expression containing
all eight constructor forms. Add it after importing `PrabhasaNyaya.Adequacy`.
This spike does not invoke Lean or claim the expression has been compiled.

`evaluate(world, Syllogism(...))` retains the separate closed-world diagnostic:
asiddha first, then viruddha/savyabhicara for counterexamples; otherwise the
**named** sapaksa must be distinct from paksa and have both hetu and sadhya.
A nonexistent, unsupported, or self example yields aprasiddha. Another valid
supporting locus cannot substitute for the named example.

## Fixtures and testing

`golden/` contains 20 JSON files: 10 law_apply, 6 law_abstain covering all four
specified reasons, and 4 malformed wires with expected rejection diagnostics.
Example A is fixture 01; Example B is fixture 11 (its required kind is
law_abstain). Operator cases are authored synthetic constructor/grounding
fixtures, not evaluated legal decisions; they withhold a formal verdict.
The worked A verdict is preserved as specified, while the computed audit
report explicitly says `certified: false`. Sources use fixture URIs and
placeholder contract/registry hashes, not production authority claims.

Run `python -m pytest -q`. Rebuild fixtures using
`PYTHONPATH=. python tests/build_golden.py` from this directory.
