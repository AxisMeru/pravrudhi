"""Structural checks, deliberately not a legal entailment certificate."""
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from .models import *

class ValidationError(ValueError): pass

def require(ok, message):
    if not ok: raise ValidationError(message)
def identifier(value):
    require(isinstance(value,str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',value), f'invalid ID: {value!r}')
@dataclass(frozen=True)
class Cardinality:
    minimum: int = 0
    maximum: int | None = None
    def __post_init__(self):
        require(type(self.minimum) is int and self.minimum >= 0, 'invalid minimum')
        require(self.maximum is None or (type(self.maximum) is int and self.maximum >= self.minimum), 'invalid maximum')
@dataclass(frozen=True)
class EventFrame:
    roles: dict[KarakaRole, Cardinality] = field(default_factory=dict)
    allowed_sorts: dict[KarakaRole, frozenset[Sorta]] = field(default_factory=dict)
@dataclass(frozen=True)
class Cite:
    volume: int
    reporter: str
    page: int
@dataclass(frozen=True)
class Registry:
    """Trusted caller-owned adapters; never populated from model output."""
    texts: dict[str, str] = field(default_factory=dict)
    citations: dict[str, Cite] = field(default_factory=dict)
    predicates: frozenset[str] = frozenset({'applies_s416'})
    rules: frozenset[str] = frozenset({'R416_NEG', 'R416_POS'})

SIGNATURES = {
    'provides': ('provision','text'), 'cites': ('authority','cite'),
    'establishes': ('authority','holding'), 'applies': ('provision|holding','act'),
    'satisfies': ('act','element'), 'binds': ('court','court'),
    'entitles': ('node','remedy'), 'absent': ('node','node'),
}

def validate_record(record, *, frames=None, registry=None):
    registry = registry or Registry()
    frames = frames or {}
    # Also check direct dataclass construction, not just JSON inputs.
    try: decode(Record, to_dict(record))
    except (ValueError,TypeError) as exc: raise ValidationError(str(exc)) from exc
    require(record.schema_version == 'nyaya-law-v1','unknown schema version')
    identifier(record.id); identifier(record.audit.contract_id)
    p,t = record.prompt,record.target
    require(p.legal_scope.mode in ('stipulated_statute','date_applicable'),'invalid scope mode')
    if p.legal_scope.mode == 'date_applicable':
        try: date.fromisoformat(p.legal_scope.incident_date)
        except (ValueError,TypeError): raise ValidationError('incident_date required')
    sources = p.sources()
    require(len(sources)==len(p.facts)+len(p.passages),'duplicate source ID')
    for source in (*p.facts,*p.passages): identifier(source.id)
    passages = {x.id:x for x in p.passages}
    for passage in p.passages:
        require(hashlib.sha256(passage.text.encode('utf-8')).hexdigest()==passage.sha256,'passage hash mismatch')
    def spans(items):
        for s in items:
            require(s.source_id in sources,'unknown span source')
            require(type(s.start) is int and type(s.end) is int and 0 <= s.start <= s.end <= len(sources[s.source_id]),'invalid span offsets')
    nodes = {n.id:n for n in t.graph.nodes}
    claims = {c.id:c for c in t.graph.claims}
    elements = {e.id:e for e in t.elements}
    require(len(nodes)==len(t.graph.nodes),'duplicate node ID')
    require(len(claims)==len(t.graph.claims),'duplicate claim ID')
    require(len(elements)==len(t.elements),'duplicate element ID')
    require(not set(nodes)&set(claims),'claim/node ID collision')
    for n in nodes.values(): identifier(n.id); spans(n.spans)
    def node(ref):
        identifier(ref); require(ref in nodes, f'unknown node: {ref}')
        return nodes[ref]
    def event(ref): require(node(ref).category == PadarthaCategory.KRIYA and nodes[ref].legal_sort == Sorta.act,'event must be KRIYA act')
    seen = set()
    for b in t.graph.bindings:
        event(b.event); node(b.filler)
        key=(b.event,b.role,b.filler)
        require(key not in seen,'duplicate role binding'); seen.add(key)
        require(b.event!=b.filler,'self binding')
    for event_id,frame in frames.items():
        event(event_id)
        for role,card in frame.roles.items():
            count=sum(b.event==event_id and b.role==role for b in t.graph.bindings)
            require(count>=card.minimum and (card.maximum is None or count<=card.maximum),'event frame cardinality violated')
        for b in t.graph.bindings:
            if b.event==event_id and b.role in frame.allowed_sorts:
                require(nodes[b.filler].legal_sort in frame.allowed_sorts[b.role],'event frame sort violated')
    for e in elements.values():
        require(node(e.id).legal_sort==Sorta.element,'element requires element node'); event(e.event)
        require(e.status in ('established','negated','unknown'),'invalid element status'); spans(e.evidence)
        require(e.status=='unknown' or bool(e.evidence),'known element requires evidence')
    prior=set(elements)
    for c in t.graph.claims:
        identifier(c.id); spans(c.evidence)
        sig=SIGNATURES[c.op]
        require(len(c.args)==len(sig),'claim arity mismatch')
        for arg,sort in zip(c.args,sig):
            identifier(arg)
            if sort=='text': require(arg in registry.texts,'unknown text adapter')
            elif sort=='cite':
                require(arg in registry.citations,'unknown citation adapter')
                cit=registry.citations[arg]
                require(type(cit.volume) is int and type(cit.page) is int and min(cit.volume,cit.page)>=0 and isinstance(cit.reporter,str),'invalid citation')
            else: require(sort=='node' and bool(node(arg)) or node(arg).legal_sort in sort.split('|'),'claim sort mismatch')
        require(bool(c.evidence) or c.rule_id is not None,'claim requires evidence or provenance')
        if c.rule_id is not None:
            require(c.rule_id in registry.rules,'unknown rule'); require(bool(c.premise_ids),'derived claim requires premises')
        else: require(not c.premise_ids,'premises without rule')
        for ref in c.premise_ids: require(ref in prior,'unknown or forward premise')
        if c.op==ClaimOp.absent and c.args[0] in elements and elements[c.args[0]].event==c.args[1]:
            require(elements[c.args[0]].status=='negated','unknown/established element cannot be absent')
        prior.add(c.id)
    tr=t.trace
    node(tr.pratijna.subject)
    require(tr.pratijna.predicate in nodes or tr.pratijna.predicate in registry.predicates,'unknown predicate')
    for ref in tr.hetu.element_ids: require(ref in elements,'unknown hetu element')
    require(tr.udaharana.rule_id in registry.rules,'unknown rule')
    for ref in tr.udaharana.passage_ids: require(ref in passages,'unknown rule passage')
    if tr.udaharana.example_id is not None: node(tr.udaharana.example_id)
    for var,ref in tr.upanaya.substitution.items(): identifier(var); node(ref)
    for ref in tr.upanaya.premise_ids: require(ref in prior,'unknown application premise')
    require(tr.nigamana.status in ('proved','not_proved'),'invalid conclusion status')
    for ref in tr.nigamana.claim_ids: require(ref in claims,'unknown conclusion claim')
    require(tr.nigamana.status!='proved' or bool(tr.nigamana.claim_ids),'proved conclusion requires claims')
    require(t.hetvabhasa.profile=='legal-rules-v1','unknown checker profile')
    for cite in t.cited_sections:
        require(cite.passage_id in passages,'unknown citation passage')
        passage=passages[cite.passage_id]
        require((cite.act,cite.section)==(passage.act,passage.section),'citation metadata mismatch')
    require(record.kind == ('law_abstain' if t.abstain else 'law_apply'),'kind/abstain mismatch')
    if t.abstain:
        require(t.abstain_reason in ('missing_fact','missing_source','conflicting_authority','unsupported_rule'),'invalid abstain reason')
        require(t.hetvabhasa.verdict is None and tr.nigamana.status=='not_proved' and not tr.nigamana.claim_ids,'abstention cannot certify')
    else: require(t.abstain_reason is None,'unexpected abstain reason')
    return {'structurally_valid': True, 'certified': False}
