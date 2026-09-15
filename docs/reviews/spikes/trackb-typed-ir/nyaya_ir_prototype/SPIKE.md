# Design spike: English legal reasoning through a typed IR

**Recommendation:** reuse the graph vocabulary and deterministic checker components; extend the graph with English span grounding, event-scoped roles, explicit propositions, and legal claim types. Rebuild the SFT supervision. Neither the current five-member prose template nor the finite-world verifier establishes that an English legal conclusion follows from retrieved legislation.

No files modified. This is source inspection and design, not a runtime validation.

## 1. Existing schemas, exactly as implemented

### 1.1 Meaning graph

There is no declared JSON Schema in the inspected graph package. The following describes the **canonical JSON emitted by `MeaningGraph.to_dict()`**, including its string restrictions.

Sources: enums and dataclasses in [types.py:19](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/types.py:19); serialization in [types.py:135](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/types.py:135).

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "required": ["nodes", "edges"],
  "additionalProperties": false,
  "properties": {
    "nodes": {
      "type": "array",
      "items": {"$ref": "#/$defs/node"}
    },
    "edges": {
      "type": "array",
      "items": {"$ref": "#/$defs/edge"}
    },
    "inference_roles": {
      "type": "object",
      "minProperties": 1,
      "additionalProperties": {"type": "string"}
    }
  },
  "$defs": {
    "token": {
      "type": "string",
      "minLength": 1,
      "pattern": "^[^(),: .!\\[\\]=]+$"
    },
    "category": {
      "enum": [
        "DRAVYA", "GUNA", "KRIYA", "SAMANYA",
        "VISESA", "SAMAVAYA", "ABHAVA"
      ]
    },
    "role": {
      "enum": [
        "KARTR", "KARMAN", "KARANA",
        "SAMPRADANA", "APADANA", "ADHIKARANA"
      ]
    },
    "sansa": {
      "enum": [
        "VISAYATA", "PRAKARATA", "VISESYATA",
        "AVACCHEDAKA", "SAMAVAYA", "SAMYOGATA"
      ]
    },
    "node": {
      "type": "object",
      "required": ["id", "category"],
      "additionalProperties": false,
      "properties": {
        "id": {"$ref": "#/$defs/token"},
        "category": {"$ref": "#/$defs/category"},
        "role": {"$ref": "#/$defs/role"},
        "label": {"$ref": "#/$defs/token"}
      }
    },
    "edge": {
      "type": "object",
      "required": ["src", "dst", "sansa"],
      "additionalProperties": false,
      "properties": {
        "src": {"type": "string"},
        "dst": {"type": "string"},
        "sansa": {"$ref": "#/$defs/sansa"},
        "qualifier": {"$ref": "#/$defs/token"}
      }
    }
  }
}
```

**Important distinctions:**

- Python defaults: `nodes=[]`, `edges=[]`, `inference_roles={}`; node `role`/`label` and edge `qualifier` default to `None`.
- Serialization **omits** `None` fields and empty `inference_roles`.
- `from_dict()` is more permissive than this canonical schema: missing collections default empty, several values are coerced with `str()`, falsey optional values become `None`, and extra fields are ignored. It does **not** call `validate_graph()`. [types.py:174](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/types.py:174)
- `inference_roles` keys are arbitrary strings—there is **no** enum restricting them to Nyāya members.

Semantic validation adds:

| Constraint | Implementation |
|---|---|
| Unique node IDs; existing edge endpoints; no self-loops | [validators.py:35](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:35) |
| All roles require `DRAVYA`, except `ADHIKARANA` permits `DRAVYA` or `GUNA` | [validators.py:18](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:18) |
| `VISAYATA` must originate at `KRIYA` | [validators.py:29](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:29) |
| Per action’s `VISAYATA` participants: at most one filler per role, except two `KARMAN` fillers | [validators.py:67](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:67) |
| Every inference-role target must identify a node | [validators.py:88](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:88) |

The renderer’s separate object is:

```json
{"ascii": "string", "iast": "string"}
```

Its canonical grammar is:

```text
SANSA(CATEGORY[.ROLE]:id,CATEGORY[.ROLE]:id[,qualifier])
CATEGORY[.ROLE]:isolated_id
INFERENCE[arbitrary_role=node_id,...]
```

Labels are deliberately dropped. Graph equality also ignores labels. This matters for evidence fidelity. [renderer.py:1](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/renderer.py:1), [types.py:79](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/types.py:79)

### 1.2 Nyāya objects

These are Python dataclasses and generated strings, **not an existing structured JSON trace protocol**. Their exact field-level JSON representations, if serialized, are:

```text
Syllogism = {
  paksa: string,
  sadhya: string,
  hetu: string,
  sapaksa: string
}

World = {
  <locus:string>: string[]
}
# Python representation: Mapping[str, frozenset[str]]

VerificationResult = {
  verdict: "valid" | "asiddha" | "savyabhicara" | "viruddha" | "aprasiddha",
  counterexamples: string[],  # Python tuple; default ()
  supporting: string[]       # Python tuple; default ()
}
```

Sources: [nyaya.py:30](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/nyaya.py:30), [verify.py:26](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/verify.py:26).

`build_syllogism()` requires nonempty `paksa`, `sadhya`, `hetu`; `sapaksa` defaults to `"kitchen"` and is not checked for emptiness. Direct dataclass construction bypasses those checks. [nyaya.py:61](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/nyaya.py:61)

`members()` returns exactly:

```text
{
  pratijna:  "{paksa} has {sadhya}",
  hetu:      "because of {hetu}",
  udaharana: "wherever there is {hetu} there is {sadhya}, as in {sapaksa}",
  upanaya:   "{paksa} likewise has {hetu}",
  nigamana:  "therefore {paksa} has {sadhya}"
}
```

The five keys are the lowercase `Avayava` enum. `format_en()` returns those strings in that order. [nyaya.py:22](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/nyaya.py:22)

**Missing today:** machine-readable rule premises, variable substitution, evidence references, section citations, explicit polarity, abstention, and a link from the graph to the syllogism.

### 1.3 Actual SFT and law fixtures

The current record is only:

```text
{kind: string, prompt: string, target: string}
```

There is no enforced `kind` enum. [build_instruct_v1.py:39](/home/ss/projects/prabhasa-samskrutam/scripts/m6/build_instruct_v1.py:39)

Two inspected records:

- [instruct_v1.jsonl:1](/home/ss/projects/prabhasa-samskrutam/data/sft/instruct_v1.jsonl:1): `nyaya_check`; prompt supplies an empty-property world and asks whether cloth has softness via touch; target is `asiddha\n`.
- [instruct_v1.jsonl:2](/home/ss/projects/prabhasa-samskrutam/data/sft/instruct_v1.jsonl:2): `reason_chain`; target starts with a `come/APADANA` graph, then reasons from `"mentioned"` to `"present"`, declares `valid`, and answers “Yes”.

These are unsuitable as legal gold without rebuilding:

1. `reason_chain` hard-codes both `valid` and “Yes”; it never verifies that inference. [build_instruct_v1.py:434](/home/ss/projects/prabhasa-samskrutam/scripts/m6/build_instruct_v1.py:434)
2. `nyaya_check` computes labels over the full world, but presents only its first three loci, or omits the world entirely. [build_instruct_v1.py:385](/home/ss/projects/prabhasa-samskrutam/scripts/m6/build_instruct_v1.py:385)
3. Deduplication uses `(prompt,target)`, so conflicting targets for an identical prompt survive. [build_instruct_v1.py:462](/home/ss/projects/prabhasa-samskrutam/scripts/m6/build_instruct_v1.py:462)

`law_apply` supplies:

```text
item = {act, facts, id, kind, question, section}    # strings
gold = {
  elements: string[],
  id: string,
  is_control: boolean,
  missing_element: string | null,
  note: string
}
```

It contains **33 records: 23 challenge items and 10 controls**, explicitly labelled internal, team-authored evaluation. It has no retrieved statute passages, span annotations, or formal traces. [manifest.json:1](/home/ss/projects/prabhasa-samskrutam/data/eval/law_apply/manifest.json:1), [items.jsonl:3](/home/ss/projects/prabhasa-samskrutam/data/eval/law_apply/items.jsonl:3), [gold.jsonl:3](/home/ss/projects/prabhasa-samskrutam/data/eval/law_apply/gold.jsonl:3)

Keep these fixtures held out; use their design pattern to author new training cases.

## 2. Proposed English-law SFT record

### 2.1 Storage schema

Below is a **proposed typed schema**, not existing code. `ID` is an ASCII identifier. References must resolve within the record or its versioned checker registry.

```text
Record = {
  schema_version: "nyaya-law-v1",
  id: ID,
  kind: "law_apply" | "law_abstain",
  prompt: {
    question: string,
    facts: [{id: ID, text: string}],
    jurisdiction: string,
    legal_scope: {
      act: string,
      version: string,
      mode: "stipulated_statute" | "date_applicable",
      incident_date: string | null
    },
    passages: [{
      id: ID,
      act: string,
      section: string,
      version: string,
      source_uri: string,
      sha256: string,
      text: string
    }]
  },
  target: {
    graph: {
      nodes: [{
        id: ID,
        category: ExistingPadarthaCategory,
        legal_sort: "provision" | "authority" | "holding" | "court" |
                    "party" | "act" | "element" | "fact" | "remedy",
        spans: Span[]
      }],
      bindings: [{
        event: ID,
        role: ExistingKarakaRole,
        filler: ID
      }],
      claims: [{
        id: ID,
        op: "provides" | "cites" | "establishes" | "applies" |
            "satisfies" | "binds" | "entitles" | "absent",
        args: ID[],
        evidence: Span[]
      }]
    },
    elements: [{
      id: ID,
      event: ID,
      status: "established" | "negated" | "unknown",
      evidence: Span[]
    }],
    trace: {
      pratijna: {subject: ID, predicate: ID, polarity: boolean},
      hetu: {element_ids: ID[]},
      udaharana: {rule_id: ID, passage_ids: ID[], example_id: ID | null},
      upanaya: {
        substitution: {<variable:string>: ID},
        premise_ids: ID[]
      },
      nigamana: {
        status: "proved" | "not_proved",
        claim_ids: ID[]
      }
    },
    hetvabhasa: {
      profile: "legal-rules-v1",
      verdict: "valid" | "asiddha" | "savyabhicara" |
               "viruddha" | "aprasiddha" | null,
      satpratipaksa: boolean | null,
      badhita: boolean | null
    },
    cited_sections: [{passage_id: ID, act: string, section: string}],
    abstain: boolean,
    abstain_reason: null | "missing_fact" | "missing_source" |
                    "conflicting_authority" | "unsupported_rule",
    answer: string
  },
  audit: {
    contract_id: ID,
    contract_hash: string,
    rule_registry_hash: string,
    split_group: string,
    checker_report: object
  }
}

Span = {
  source_id: ID,
  start: integer >= 0,
  end: integer >= start
}
```

**Decisions that make this checkable:**

- Spans use **Unicode code-point offsets, half-open**, into immutable prompt text. They are independent of the pretrained model’s tokenizer.
- Kā­raka roles attach to **event–participant bindings**, allowing one person to be agent in one event and recipient in another.
- Each claim operator has a registry-defined signature matching Lean’s constructors; `args` is not an unrestricted predicate escape hatch.
- `absent` requires an explicitly identified absent object and locus. Missing evidence produces `unknown`, not an absence claim.
- `rule_id` selects a trusted, versioned rule with typed premises, conclusion, exceptions, and source mapping. The model cannot supply authoritative rules or contract axioms.
- `audit` is supervisor/checker metadata, excluded from the model’s answer. Its gold report must be computed, not supplied by the model.
- For refused grounding, `hetvabhasa.verdict=null`. An unsupported conclusion must not receive a fabricated formal verdict.

### 2.2 Compact emission format

Use a fixed line grammar with ASCII keys, short IDs, and JSON-quoted text. A deterministic parser expands it into the target schema. At inference, assign stable span IDs to input segments; `@F1` below means the complete immutable `F1` span.

This is a design for bounded generation; reliability on a 4B model still needs measurement. Keep the base model’s native tokenizer. Train on target tokens only.

### Example A — IPC section application, explicit missing element

Adapted from the **pattern** in the held-out section 416 example, not proposed as a training copy.

The supplied section is evaluated as a stipulated historical statute. Section 416 requires cheating through personation; the person represented can be real or imaginary. [India Code, IPC §416](https://www.indiacode.nic.in/bitstream/123456789/4219/1/THE-INDIAN-PENAL-CODE-1860.pdf)

**Prompt serialization**

```text
SCOPE act=IPC mode=stipulated_statute
Q Does section 416 apply to event E?
F1 "A deceives B into paying money."
F2 "A uses only A's true identity and makes no false identity representation about anyone."
P1 act=IPC section=416 version=fixture-v1
"Section 416 requires cheating through personation."
```

`P1` is a shortened explanatory passage for this worked example. Production records must carry the exact retrieved text, including relevant alternatives and explanation, plus its source hash.

Trusted rule `R416_NEG`: explicit absence of every relevant personation alternative defeats section 416 application. Its necessity premise is grounded in the full provision, not learned from a made-up analogy.

**Target serialization**

```text
NODE a DRAVYA party @F1
NODE b DRAVYA party @F1
NODE e KRIYA act @F1
NODE s416 SAMANYA provision @P1
NODE personation GUNA element @P1
ROLE e KARTR a
ROLE e KARMAN b
ELEMENT personation e negated @F2
CLAIM c1 absent personation e @F2
CLAIM c2 absent s416 e BY R416_NEG c1
PRATIJNA e applies_s416 false
HETU personation
UDAHARANA R416_NEG @P1 example=null
UPANAYA x=e premises=personation
NIGAMANA proved c2
HETVABHASA legal-rules-v1 valid satpratipaksa=null badhita=null
CITE P1 IPC 416
ABSTAIN false
ANSWER "Section 416 does not apply on these facts: personation is explicitly absent."
```

Here `absent s416 e` denotes absence of **applicability at this event**, not nonexistence of the provision. The adapter constructs the corresponding typed `Abhava`.

`valid` is the proposed rule-checker verdict for the **negative conclusion**. It is not a claim that the current finite-world checker derives this legal result.

### Example B — abstain because a necessary fact is unknown

**Prompt serialization**

```text
SCOPE act=IPC mode=stipulated_statute
Q Does section 416 apply to event E?
F1 "A deceives B into paying money."
F2 "The account gives no information about any identity representation."
P1 act=IPC section=416 version=fixture-v1
"Section 416 requires cheating through personation."
```

**Target serialization**

```text
NODE a DRAVYA party @F1
NODE b DRAVYA party @F1
NODE e KRIYA act @F1
NODE s416 SAMANYA provision @P1
NODE personation GUNA element @P1
ROLE e KARTR a
ROLE e KARMAN b
ELEMENT personation e unknown @F2
PRATIJNA e applies_s416 true
HETU personation
UDAHARANA R416_POS @P1 example=null
UPANAYA x=e premises=[]
NIGAMANA not_proved
HETVABHASA legal-rules-v1 null satpratipaksa=null badhita=null
CITE P1 IPC 416
ABSTAIN true missing_fact
ANSWER "I cannot determine whether section 416 applies: the account does not establish or negate personation."
```

The attempted thesis is separate from asserted claims. No positive application claim or negative personation claim is emitted. The five slots describe the attempted application and where it stops.

**Do not label this `viruddha`.** Unknown personation is not proof of its absence. A supplementary finite-world diagnostic might call an unestablished hetu `asiddha`, but Track A’s grounding refusal should withhold certification.

## 3. Portability

| Module | Dependency evidence | New-repo treatment |
|---|---|---|
| `domain/graph/types.py` | Standard-library dataclasses, enum, typing: [line 13](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/types.py:13) | Port vocabulary and structural utilities; version extensions. |
| `domain/graph/validators.py` | Graph-domain imports only: [line 5](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:5) | Port structural checks; replace hard-coded cardinalities with legal/event frame rules. |
| `domain/graph/renderer.py` | Dataclasses and graph imports: [line 20](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/renderer.py:20) | Portable canonical syntax; extend to preserve spans and propositions. |
| `reason/nyaya.py`, `verify.py`, `self_consistency.py` | Standard library plus local reason imports: [nyaya:16](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/nyaya.py:16), [verify:18](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/verify.py:18), [self_consistency:13](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/self_consistency.py:13) | Portable; retain finite-world semantics as a separate checker profile. |
| `reason/z3_verify.py` | Lazy external `z3` import, pure fallback: [line 19](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/z3_verify.py:19) | No torch coupling; requires `z3-solver` for actual solver execution. |
| `bridge/templates.py` | `re`, dataclasses, graph: [line 23](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/templates.py:23) | Portable code, inadequate grammar for legal English. Replace parser/frames. |
| `bridge/lexicon.py` | Graph enum only: [line 13](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/lexicon.py:13) | Pure Python but Sanskrit-only functionality; omit. |
| `bridge/realizer.py` | Pure controlled path; external client through `Protocol`: [line 15](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/realizer.py:15), [line 62](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/realizer.py:62) | Omit Sanskrit generation; replace English answer renderer. |
| `domain/contracts/closure.py` | Imports Pydantic: [line 24](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/contracts/closure.py:24) | Python, but not dependency-free; concerns project closure, not legal trace validation. |
| `scripts/m6/build_instruct_v1.py` | Standard library, bridge, reason, graph imports: [line 18](/home/ss/projects/prabhasa-samskrutam/scripts/m6/build_instruct_v1.py:18) | Reuse JSONL plumbing only; rebuild labels, evidence, and splitting. |
| `application/tokenizer/bytelevel.py` | Pure Python, fixed 256-byte vocabulary and no EOS: [line 17](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/tokenizer/bytelevel.py:17) | Architecturally coupled despite no torch; do not use with pretrained HF weights. |
| `infrastructure/ml/inference.py` | Loads custom `NemotronH` from `scripts/m2/train_130m.py`; torch checkpoint and three-channel forward: [line 22](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/infrastructure/ml/inference.py:22), [line 61](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/infrastructure/ml/inference.py:61) | Replace with the selected pretrained model’s HF adapter. |
| `scripts/m4/sft_megatron.py` | Torch, Megatron loader, raw UTF-8 encoding: [line 28](/home/ss/projects/prabhasa-samskrutam/scripts/m4/sft_megatron.py:28) | Replace training loop; incompatible tokenizer/checkpoint assumptions. |

The existing custom `NemotronH` runner is not evidence of compatibility with the proposed NVIDIA pretrained base.

## 4. Mechanical validation through Track A

### 4.1 Existing Lean contract interface

Lean already defines:

| Object | Exact fields / constructors |
|---|---|
| `Entity` | `name : String`, `sort : Sorta` |
| `Sorta` | `provision`, `authority`, `holding`, `court`, `party`, `act`, `element`, `fact`, `remedy` |
| `Node` | `ent Entity` or recursive `rel String Node Node` |
| `Abhava` | `pratiyogin : Node`, `adhikarana : Node` |
| `Provision` | `act : String`, `sectionNo : String` |
| `Cite` | `volume : Nat`, `reporter : String`, `page : Nat` |
| `Reading` | `claims : List Claim` |
| `Contract` | `axioms`, `denials`, `required : List Claim`; `notFormalisable : List String` |
| `Inference` | `paksa`, `sadhya`, `hetu : String` |
| `World` | `List (String × List String)` |

Sources: [Ontology.lean:16](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Ontology.lean:16), [Adequacy.lean:13](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Adequacy.lean:13), [Adequacy.lean:94](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Adequacy.lean:94), [Hetvabhasa.lean:18](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Hetvabhasa.lean:18).

`Claim` has these constructor signatures:

```text
provides    Provision String
cites       Entity Cite
establishes Entity Entity
applies     Node Node
satisfies   Node Entity
binds       Entity Entity
entitles    Node Entity
absent      Abhava
```

### 4.2 Validation pipeline and required fields

1. **Parse and resolve references.**  
   Needs `schema_version`, node IDs/types, event bindings, claim signatures, spans, passage IDs. Reject unknown operators, invalid offsets, dangling references, and conflicting duplicate IDs.

2. **Ground the formalization.**  
   Needs passage `act/section/version/hash`, claim evidence, element status/evidence, trusted `contract_id/hash`. Verify quoted spans exactly; separately validate the semantic mapping against an authored contract or reviewed extraction.

   A correct substring match proves where text came from; it does not prove that the text entails an element.

3. **Apply Lean’s existing refusal order.**  
   Compile asserted claims to `Reading`; load the independently supplied `Contract`. Existing `check` evaluates:

   ```text
   explicitly denied → refuted
   unlicensed        → refusedUngrounded
   required omitted  → refusedIncomplete
   otherwise         → grounded (Verdict.of world inference)
   ```

   Only `grounded valid` is certified. [Verdict.lean:24](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Verdict.lean:24), [Verdict.lean:54](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Verdict.lean:54)

4. **Check the actual legal inference—extension required.**  
   Needs `rule_id`, typed variables, `substitution`, all required premises, explicit negative facts, exceptions/defeaters, and conclusion claim IDs.

   **Current Lean entailment does not derive `applies` from `satisfies`.** Apart from special handling for `binds`, it checks claim membership in `axioms`. Existing contracts can certify authored outcomes; general legal rule application needs new rule semantics and proofs. [Adequacy.lean:50](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Adequacy.lean:50)

5. **Check Nyāya member consistency.**  
   Ensure the hetu names the rule’s premises; udāharaṇa names the licensed rule and, if used, an independently grounded illustration; upanaya supplies a type-correct substitution; nigamana equals the instantiated conclusion. The present `Syllogism.members()` only formats strings.

6. **Check abstention and answer consistency.**  
   Unknown necessary facts or unavailable rules must block a positive certificate. Render the final answer from checked claims, or verify its assertions against them. Otherwise a valid trace can accompany an unsupported prose answer.

### 4.3 What present Z3 actually establishes

It needs:

```text
world: locus → set of properties
syllogism.paksa
syllogism.hetu
syllogism.sadhya
```

It fixes each Boolean to membership in the supplied world, then searches for a locus with `hetu ∧ ¬sadhya`. Missing properties are false. It does not infer statute application from textual premises. [z3_verify.py:38](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/z3_verify.py:38)

Additional limitations:

- Both Python checkers **ignore `syllogism.sapaksa`**; any supporting locus other than paksa suffices. A fabricated named udāharaṇa can go unchecked. [verify.py:62](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/reason/verify.py:62)
- Supplying the desired conclusion as a world fact is not deriving it.
- An illustrative example cannot establish a statutory universal by itself.
- For an open-world legal extension, check consistency first, then whether `premises ∧ rules ∧ ¬conclusion` is unsatisfiable. Do not encode absent evidence as false; distinguish solver `unknown` from a counterexample.

Lean additionally implements `isSatpratipaksa(w1,i1,w2,i2)` and `isBadhita(w,i,observations)`, outside the five-valued verdict. The former tests different sādhya strings, **not logical opposition or subject equality**; the latter checks an explicit absent observation. Legal use needs subject alignment, opposition typing, and an authority policy. These checks are not integrated into `check`. [Hetvabhasa.lean:80](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Hetvabhasa.lean:80), [Hetvabhasa.lean:96](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Hetvabhasa.lean:96)

## 5. Sanskrit assumptions and English failure points

| Issue | Evidence and concrete consequence |
|---|---|
| **No vibhakti, sandhi, or Devanagari requirement in the graph schema itself** | Node IDs are strings with punctuation restrictions, not Sanskrit tokens. The main risks are surrounding components and incomplete semantics. [types.py:57](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/types.py:57) |
| **English spans cannot live in labels** | Spaces, periods, commas, brackets, etc. are forbidden; `"dishonest conversion"` fails construction. Labels also disappear during rendering/equality. Store immutable spans separately. [types.py:19](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/types.py:19), [renderer.py:13](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/renderer.py:13) |
| **Roles are global node attributes** | A single entity cannot carry different roles across events. Bridge construction leaves the agent disconnected; rendering selects agents globally and the first action. Multi-event legal facts become ambiguous. [templates.py:162](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/templates.py:162), [templates.py:184](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/templates.py:184) |
| **Pāṇinian cardinalities are hard-coded** | Multiple joint agents fail the one-`KARTR` limit; legal facts can involve groups, coordinated objects, and multiple recipients. Introduce group entities or configurable event frames. [validators.py:67](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:67) |
| **Participant ontology is too narrow** | Most roles require `DRAVYA`; propositional objects, rights, duties, and conduct-as-object need explicit legal sorts and signatures. [validators.py:18](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:18) |
| **Controlled English is not legal English** | Slots match only `[a-z]+`; seven frames; lowercasing; repeated participant words rejected. Names, section numbers, passive clauses, negation, and embedded clauses are unsupported. [templates.py:37](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/templates.py:37), [templates.py:143](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/templates.py:143) |
| **Vibhakti and Sanskrit morphology enter through realization** | Fixed role→case mapping, a-stem declension, restricted noun/verb lexicon, SOV output. Arbitrary English legal words fail lookup. Omit this path. [lexicon.py:45](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/lexicon.py:45), [realizer.py:35](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/bridge/realizer.py:35) |
| **Devanagari/WX assumptions enter structural training channels** | Role extraction recognizes Sanskrit/WX labels and lakāra markers, aligned to bytes. These are not English role annotations or HF token positions. [structure.py:24](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/application/corpus/structure.py:24) |
| **`ABHAVA` is only a category in Python** | It has no required counterpositive/locus fields or polarity semantics. Lean’s `Abhava` is a stronger starting point. [types.py:23](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/types.py:23), [Ontology.lean:47](/home/ss/projects/prabhasa-nyaya/lean/PrabhasaNyaya/Ontology.lean:47) |
| **Ākāṅkṣā/yogyatā are not fully implemented** | Existing validation checks category compatibility and maximum role counts; it does not require all arguments, resolve scope, or enforce legal selection restrictions. Implement required typed rule slots and event bindings explicitly. [validators.py:35](/home/ss/projects/prabhasa-samskrutam/src/prabhasa/domain/graph/validators.py:35) |
| **Paribhāṣā remains a small structural-rule layer** | No inspected graph/reason object represents statutory exceptions, precedence, jurisdiction, or temporal applicability. These require a versioned rule registry and explicit unresolved-rule states. |

**Practical boundary:** Sanskrit supplies useful names and structural ideas. Certification comes from grounded premises, explicit rule semantics, and deterministic checks—not from Sanskrit surface generation or five well-formed sentences.