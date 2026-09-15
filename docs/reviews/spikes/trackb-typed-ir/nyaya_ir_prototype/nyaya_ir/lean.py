"""Emit Lean constructor text, without claiming compilation or certification."""
from .validators import Registry, validate_record, ValidationError

def quote(text):
    out='"'
    for c in text:
        out += {'"':'\\"','\\':'\\\\','\n':'\\n','\r':'\\r','\t':'\\t'}.get(c, f'\\u{ord(c):04x}' if ord(c)<32 else c)
    return out+'"'

def export_reading(record, *, registry=None):
    registry=registry or Registry(); validate_record(record,registry=registry)
    nodes={n.id:n for n in record.target.graph.nodes}
    def ent(ref): return f'({{ name := {quote(ref)}, sort := .{nodes[ref].legal_sort} }} : PrabhasaNyaya.Entity)'
    def node(ref): return f'(PrabhasaNyaya.Node.ent {ent(ref)})'
    rows=[]
    for c in record.target.graph.claims:
        a,b=c.args
        if c.op=='provides':
            pids={s.source_id for s in nodes[a].spans}
            ps=[p for p in record.prompt.passages if p.id in pids]
            identities={(p.act,p.section) for p in ps}
            if len(identities)!=1: raise ValidationError('provision requires unambiguous passage metadata')
            act,section=next(iter(identities))
            args=f'{{ act := {quote(act)}, sectionNo := {quote(section)} }} {quote(registry.texts[b])}'
        elif c.op=='cites':
            cite=registry.citations[b]
            args=f'{ent(a)} {{ volume := {cite.volume}, reporter := {quote(cite.reporter)}, page := {cite.page} }}'
        elif c.op in ('establishes','binds'): args=f'{ent(a)} {ent(b)}'
        elif c.op=='applies': args=f'{node(a)} {node(b)}'
        elif c.op in ('satisfies','entitles'): args=f'{node(a)} {ent(b)}'
        else: args=f'{{ pratiyogin := {node(a)}, adhikarana := {node(b)} }}'
        rows.append(f'PrabhasaNyaya.Claim.{c.op} {args}')
    return '({ claims := [\n  '+',\n  '.join(rows)+'\n] } : PrabhasaNyaya.Reading)\n'
