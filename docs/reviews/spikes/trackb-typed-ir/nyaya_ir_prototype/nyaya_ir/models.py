"""Immutable storage types for nyaya-law-v1 (Unicode code-point spans)."""
from __future__ import annotations
from dataclasses import dataclass, fields, is_dataclass
from enum import StrEnum
from typing import get_type_hints, get_origin, get_args, Union
import types

class PadarthaCategory(StrEnum):
    DRAVYA='DRAVYA'; GUNA='GUNA'; KRIYA='KRIYA'; SAMANYA='SAMANYA'
    VISESA='VISESA'; SAMAVAYA='SAMAVAYA'; ABHAVA='ABHAVA'
class KarakaRole(StrEnum):
    KARTR='KARTR'; KARMAN='KARMAN'; KARANA='KARANA'; SAMPRADANA='SAMPRADANA'
    APADANA='APADANA'; ADHIKARANA='ADHIKARANA'
class Sorta(StrEnum):
    provision='provision'; authority='authority'; holding='holding'; court='court'
    party='party'; act='act'; element='element'; fact='fact'; remedy='remedy'
class ClaimOp(StrEnum):
    provides='provides'; cites='cites'; establishes='establishes'; applies='applies'
    satisfies='satisfies'; binds='binds'; entitles='entitles'; absent='absent'
class Verdict(StrEnum):
    VALID='valid'; ASIDDHA='asiddha'; SAVYABHICARA='savyabhicara'
    VIRUDDHA='viruddha'; APRASIDDHA='aprasiddha'
@dataclass(frozen=True)
class Span:
    source_id: str
    start: int
    end: int
@dataclass(frozen=True)
class Fact:
    id: str
    text: str
@dataclass(frozen=True)
class LegalScope:
    act: str
    version: str
    mode: str
    incident_date: str | None = None
@dataclass(frozen=True)
class Passage:
    id: str
    act: str
    section: str
    version: str
    source_uri: str
    sha256: str
    text: str
@dataclass(frozen=True)
class Prompt:
    question: str
    facts: tuple[Fact, ...]
    jurisdiction: str
    legal_scope: LegalScope
    passages: tuple[Passage, ...]
    def __post_init__(self):
        object.__setattr__(self, 'facts', tuple(self.facts))
        object.__setattr__(self, 'passages', tuple(self.passages))
    def sources(self):
        return {x.id: x.text for x in (*self.facts, *self.passages)}
@dataclass(frozen=True)
class Node:
    id: str
    category: PadarthaCategory
    legal_sort: Sorta
    spans: tuple[Span, ...] = ()
@dataclass(frozen=True)
class Binding:
    event: str
    role: KarakaRole
    filler: str
@dataclass(frozen=True)
class Claim:
    id: str
    op: ClaimOp
    args: tuple[str, ...]
    evidence: tuple[Span, ...] = ()
    rule_id: str | None = None
    premise_ids: tuple[str, ...] = ()
@dataclass(frozen=True)
class Graph:
    nodes: tuple[Node, ...] = ()
    bindings: tuple[Binding, ...] = ()
    claims: tuple[Claim, ...] = ()
@dataclass(frozen=True)
class Element:
    id: str
    event: str
    status: str
    evidence: tuple[Span, ...] = ()
@dataclass(frozen=True)
class Pratijna:
    subject: str
    predicate: str
    polarity: bool
@dataclass(frozen=True)
class Hetu:
    element_ids: tuple[str, ...]
@dataclass(frozen=True)
class Udaharana:
    rule_id: str
    passage_ids: tuple[str, ...]
    example_id: str | None
@dataclass(frozen=True)
class Upanaya:
    substitution: dict[str, str]
    premise_ids: tuple[str, ...]
@dataclass(frozen=True)
class Nigamana:
    status: str
    claim_ids: tuple[str, ...]
@dataclass(frozen=True)
class Trace:
    pratijna: Pratijna
    hetu: Hetu
    udaharana: Udaharana
    upanaya: Upanaya
    nigamana: Nigamana
@dataclass(frozen=True)
class Hetvabhasa:
    profile: str
    verdict: Verdict | None
    satpratipaksa: bool | None
    badhita: bool | None
@dataclass(frozen=True)
class CitedSection:
    passage_id: str
    act: str
    section: str
@dataclass(frozen=True)
class Target:
    graph: Graph
    elements: tuple[Element, ...]
    trace: Trace
    hetvabhasa: Hetvabhasa
    cited_sections: tuple[CitedSection, ...]
    abstain: bool
    abstain_reason: str | None
    answer: str
@dataclass(frozen=True)
class Audit:
    contract_id: str
    contract_hash: str
    rule_registry_hash: str
    split_group: str
    checker_report: dict
@dataclass(frozen=True)
class Record:
    schema_version: str
    id: str
    kind: str
    prompt: Prompt
    target: Target
    audit: Audit

def to_dict(obj):
    if is_dataclass(obj): return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict): return {k: to_dict(v) for k,v in obj.items()}
    if isinstance(obj, (tuple,list)): return [to_dict(v) for v in obj]
    if isinstance(obj, StrEnum): return obj.value
    return obj

def decode(cls, value):
    """Strict JSON decoding: no coercion, ignored fields, or bool-as-integer."""
    origin, args = get_origin(cls), get_args(cls)
    if origin in (Union, types.UnionType):
        for arg in args:
            try: return decode(arg, value)
            except (ValueError, TypeError): pass
        raise ValueError('invalid union value')
    if origin is tuple:
        if not isinstance(value, (list,tuple)): raise ValueError('expected array')
        return tuple(decode(args[0], x) for x in value)
    if cls is dict or origin is dict:
        if type(value) is not dict: raise ValueError('expected object')
        return {decode(args[0],k):decode(args[1],v) for k,v in value.items()} if args else value.copy()
    if is_dataclass(cls):
        if type(value) is not dict: raise ValueError('expected object')
        hints = get_type_hints(cls)
        if set(value)-set(hints): raise ValueError('unknown fields')
        try: return cls(**{k:decode(hints[k],v) for k,v in value.items()})
        except TypeError as exc: raise ValueError(str(exc)) from exc
    if isinstance(cls,type) and issubclass(cls,StrEnum): return cls(value)
    if type(value) is not cls: raise ValueError(f'expected {cls.__name__}')
    return value

def from_dict(value): return decode(Record, value)
