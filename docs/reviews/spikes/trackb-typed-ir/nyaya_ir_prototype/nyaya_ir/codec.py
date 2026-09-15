"""Canonical line format; RECORD/AUDIT are storage-only envelope lines."""
import hashlib
import json
import re
from .models import *
from .validators import validate_record, ValidationError

class ParseError(ValidationError): pass

def js(value): return json.dumps(to_dict(value), ensure_ascii=False, separators=(',', ':'), allow_nan=False)
def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise ParseError('duplicate JSON key')
        result[key] = value
    return result

def json_load(text):
    def invalid(value): raise ParseError('non-finite JSON number')
    return json.loads(text, object_pairs_hook=json_object, parse_constant=invalid)

def digest(prompt): return hashlib.sha256(js(prompt).encode()).hexdigest()
def span_text(s): return f'@{s.source_id}:{s.start}:{s.end}'
def serialize(record, *, registry=None):
    validate_record(record, registry=registry)
    meta={'schema_version':record.schema_version,'id':record.id,'kind':record.kind,'prompt_sha256':digest(record.prompt)}
    return 'RECORD '+js(meta)+'\n'+serialize_target(record,registry=registry)+'AUDIT '+js(record.audit)+'\n'

def serialize_target(record, *, registry=None):
    validate_record(record,registry=registry)
    t=record.target; lines=[]
    def emit(*args): lines.append(' '.join(str(a) for a in args))
    for n in t.graph.nodes: emit('NODE',n.id,n.category,n.legal_sort,*map(span_text,n.spans))
    for b in t.graph.bindings: emit('ROLE',b.event,b.role,b.filler)
    for e in t.elements: emit('ELEMENT',e.id,e.event,e.status,*map(span_text,e.evidence))
    for c in t.graph.claims:
        args=['CLAIM',c.id,c.op,*c.args,*map(span_text,c.evidence)]
        if c.rule_id: args+=['BY',c.rule_id,*c.premise_ids]
        emit(*args)
    tr=t.trace
    emit('PRATIJNA',tr.pratijna.subject,tr.pratijna.predicate,js(tr.pratijna.polarity))
    emit('HETU',*tr.hetu.element_ids)
    emit('UDAHARANA',tr.udaharana.rule_id,*('@'+p for p in tr.udaharana.passage_ids),'example='+str(tr.udaharana.example_id or 'null'))
    emit('UPANAYA',*(f'{k}={v}' for k,v in tr.upanaya.substitution.items()),'premises='+(','.join(tr.upanaya.premise_ids) or '[]'))
    emit('NIGAMANA',tr.nigamana.status,*tr.nigamana.claim_ids)
    h=t.hetvabhasa
    emit('HETVABHASA',h.profile,h.verdict or 'null','satpratipaksa='+js(h.satpratipaksa),'badhita='+js(h.badhita))
    for c in t.cited_sections: emit('CITE',c.passage_id,js(c.act),js(c.section))
    emit('ABSTAIN',js(t.abstain),*([t.abstain_reason] if t.abstain else []))
    emit('ANSWER',js(t.answer))
    return '\n'.join(lines)+'\n'

def tokens(line):
    out=[]; decoder=json.JSONDecoder(); pos=0
    while pos<len(line):
        if line[pos].isspace(): pos+=1; continue
        if line[pos]=='"':
            value,end=decoder.raw_decode(line[pos:]); pos+=end
            if pos<len(line) and not line[pos].isspace(): raise ParseError('missing token separator')
            out.append(value)
        else:
            end=pos
            while end<len(line) and not line[end].isspace(): end+=1
            out.append(line[pos:end]); pos=end
    return out

def parse(text, prompt, *, registry=None):
    if isinstance(prompt,dict): prompt=decode(Prompt,prompt)
    try: return _parse(text,prompt,registry)
    except (ValueError,TypeError,KeyError,IndexError) as exc: raise ParseError(str(exc)) from exc

def _parse(text,prompt,registry):
    nodes=[]; bindings=[]; elements=[]; claims=[]; cites=[]; slots={}; meta=None; audit=None
    order=['RECORD','NODE','ROLE','ELEMENT','CLAIM','PRATIJNA','HETU','UDAHARANA','UPANAYA','NIGAMANA','HETVABHASA','CITE','ABSTAIN','ANSWER','AUDIT']
    last=-1
    def spans(args):
        result=[]
        for arg in args:
            m=re.fullmatch(r'@([A-Za-z_][A-Za-z0-9_]*)(?::([0-9]+):([0-9]+))?',arg)
            if not m: raise ParseError('invalid span token')
            source,start,end=m.groups()
            if source not in prompt.sources(): raise ParseError('unknown span source')
            result.append(Span(source,int(start) if start else 0,int(end) if end else len(prompt.sources()[source])))
        return tuple(result)
    for line in text.split('\n'):
        if not line.strip(): continue
        key,_,body=line.partition(' ')
        if key not in order or order.index(key)<last: raise ParseError('unknown or out-of-order line')
        last=order.index(key)
        a=[] if key in ('RECORD','AUDIT') else tokens(body)
        if key in ('RECORD','AUDIT'):
            if key in slots: raise ParseError('duplicate singleton')
            slots[key]=True
            if key=='RECORD': meta=json_load(body)
            else: audit=decode(Audit,json_load(body))
        elif key=='NODE': nodes.append(Node(a[0],PadarthaCategory(a[1]),Sorta(a[2]),spans(a[3:])))
        elif key=='ROLE': bindings.append(Binding(*a[:1],KarakaRole(a[1]),*a[2:]))
        elif key=='ELEMENT': elements.append(Element(*a[:3],spans(a[3:])))
        elif key=='CLAIM':
            rule=None; premises=()
            if 'BY' in a:
                at=a.index('BY'); rule=a[at+1]; premises=tuple(a[at+2:]); a=a[:at]
            at=next((i for i in range(2,len(a)) if a[i].startswith('@')),len(a))
            claims.append(Claim(a[0],ClaimOp(a[1]),tuple(a[2:at]),spans(a[at:]),rule,premises))
        elif key=='CITE': cites.append(CitedSection(*a))
        else:
            if key in slots: raise ParseError('duplicate singleton')
            slots[key]=a
    def take(key,n=None):
        a=slots[key]
        if n is not None and len(a)!=n: raise ParseError('wrong token count: '+key)
        return a
    def boolean(value, nullable=False):
        if value=='true': return True
        if value=='false': return False
        if nullable and value=='null': return None
        raise ParseError('invalid boolean')
    a=take('PRATIJNA',3); pratijna=Pratijna(a[0],a[1],boolean(a[2]))
    a=take('UDAHARANA'); rule=a[0]
    if not a[-1].startswith('example='): raise ParseError('missing example')
    example=a[-1][8:]
    pids=[]
    for x in a[1:-1]:
        if not re.fullmatch(r'@[A-Za-z_][A-Za-z0-9_]*',x): raise ParseError('invalid passage reference')
        pids.append(x[1:])
    ud=Udaharana(rule,tuple(pids),None if example=='null' else example)
    a=take('UPANAYA'); subst={}
    if not a[-1].startswith('premises='): raise ParseError('missing premises')
    for x in a[:-1]:
        k,v=x.split('=')
        if k in subst: raise ParseError('duplicate substitution')
        subst[k]=v
    premises=a[-1][9:]; up=Upanaya(subst,() if premises=='[]' else tuple(premises.split(',')))
    a=take('NIGAMANA'); ni=Nigamana(a[0],tuple(a[1:]))
    a=take('HETVABHASA',4)
    if not a[2].startswith('satpratipaksa=') or not a[3].startswith('badhita='): raise ParseError('invalid diagnostic fields')
    h=Hetvabhasa(a[0],None if a[1]=='null' else Verdict(a[1]),boolean(a[2].split('=',1)[1],True),boolean(a[3].split('=',1)[1],True))
    a=take('ABSTAIN'); abstain=boolean(a[0])
    if len(a)!=(2 if abstain else 1): raise ParseError('invalid abstention')
    # ANSWER is always JSON, never an unquoted token.
    answer_line=next(x for x in text.split('\n') if x.startswith('ANSWER '))
    answer=json_load(answer_line[7:])
    if not isinstance(answer,str): raise ParseError('answer must be JSON string')
    target=Target(Graph(tuple(nodes),tuple(bindings),tuple(claims)),tuple(elements),Trace(pratijna,Hetu(tuple(take('HETU'))),ud,up,ni),h,tuple(cites),abstain,a[1] if abstain else None,answer)
    if meta is None:
        meta={'schema_version':'nyaya-law-v1','id':'inference','kind':'law_abstain' if abstain else 'law_apply','prompt_sha256':digest(prompt)}
    if set(meta)!={'schema_version','id','kind','prompt_sha256'} or meta['prompt_sha256']!=digest(prompt): raise ParseError('invalid envelope or changed prompt')
    audit=audit or Audit('unassigned','','','inference',{})
    record=Record(meta['schema_version'],meta['id'],meta['kind'],prompt,target,audit)
    validate_record(record,registry=registry)
    return record

def parse_target(text, prompt, *, registry=None):
    """Inference boundary: reject supervisor envelope fields."""
    if any(line.split(' ', 1)[0] in ('RECORD', 'AUDIT') for line in text.split('\n')):
        raise ParseError('supervisor metadata is not model output')
    return parse(text, prompt, registry=registry).target
