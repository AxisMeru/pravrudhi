import json
from pathlib import Path
from dataclasses import replace, FrozenInstanceError
import pytest
from nyaya_ir import *
from nyaya_ir.codec import serialize_target

FILES=sorted((Path(__file__).resolve().parents[1]/'golden').glob('*.json'))
def load(path): return json.loads(path.read_text())
def registry(data):
    r=data.get('registry',{})
    return Registry(texts=r.get('texts',{}),citations={k:Cite(**v) for k,v in r.get('citations',{}).items()})
def first(): return from_dict(load(FILES[0])['record'])
@pytest.mark.parametrize('path',FILES,ids=lambda p:p.stem)
def test_goldens(path):
    data=load(path)
    if 'reject' in data:
        with pytest.raises(ParseError,match=data['reject']): parse(data['wire'],decode(Prompt,data['prompt']))
    else:
        record=from_dict(data['record']); reg=registry(data)
        assert parse(data['wire'],record.prompt,registry=reg)==record
        assert serialize(record,registry=reg)==data['wire']
        assert parse(serialize(record,registry=reg),record.prompt,registry=reg)==record
        assert validate_record(record,registry=reg)=={'structurally_valid':True,'certified':False}

def test_fixture_inventory():
    data=[load(p) for p in FILES]
    assert len(data)==20
    assert sum(d.get('record',{}).get('kind')=='law_apply' for d in data)==10
    assert sum(d.get('record',{}).get('kind')=='law_abstain' for d in data)==6
    assert {d['record']['target']['abstain_reason'] for d in data if d.get('record',{}).get('kind')=='law_abstain'}=={'missing_fact','missing_source','conflicting_authority','unsupported_rule'}

def test_spike_examples_verbatim():
    spike=(Path(__file__).resolve().parents[1]/'SPIKE.md').read_text()
    import re
    examples=re.findall(r'```text\n(NODE a DRAVYA party.*?\n)```',spike,re.S)
    assert len(examples)==2
    for wire,path in zip(examples,(FILES[0],FILES[10])):
        r=from_dict(load(path)['record'])
        assert parse(wire,r.prompt).target==r.target

def test_prompt_immutable_and_bound():
    r=first()
    with pytest.raises(FrozenInstanceError): r.prompt.facts[0].text='changed'
    p=replace(r.prompt,question='Different question')
    with pytest.raises(ParseError,match='changed prompt'): parse(serialize(r),p)

def test_unicode_and_json_escape_roundtrip():
    r=first(); text='Zoë, 😀: "pays"\n£5.\\done'
    p=replace(r.prompt,facts=(Fact('F1',text),r.prompt.facts[1]))
    nodes=tuple(replace(n,spans=(Span('F1',5,6),)) if n.id in ('a','b','e') else n for n in r.target.graph.nodes)
    r=replace(r,prompt=p,target=replace(r.target,graph=replace(r.target.graph,nodes=nodes),answer=text))
    assert p.sources()['F1'][5:6]=='😀'
    assert parse(serialize(r),p)==r
    assert r.target.graph.nodes[0]!=replace(r.target.graph.nodes[0],spans=())

def test_joint_agents_and_event_scope():
    r=from_dict(load(FILES[9])['record'])
    validate_record(r)
    validate_record(r,frames={'e':EventFrame({KarakaRole.KARTR:Cardinality(2,2)}),'e2':EventFrame({KarakaRole.SAMPRADANA:Cardinality(1,1)})})
    with pytest.raises(ValidationError,match='cardinality'): validate_record(r,frames={'e':EventFrame({KarakaRole.KARTR:Cardinality(0,1)})})
    with pytest.raises(ValidationError,match='cardinality'): validate_record(r,frames={'e2':EventFrame({KarakaRole.KARTR:Cardinality(1)})})
    with pytest.raises(ValidationError,match='sort'): validate_record(r,frames={'e':EventFrame(allowed_sorts={KarakaRole.KARTR:frozenset({Sorta.court})})})

@pytest.mark.parametrize('start,end',[(-1,2),(2,1),(0,999),(True,2),(0,1.5)])
def test_invalid_offsets(start,end):
    r=first(); n=replace(r.target.graph.nodes[0],spans=(Span('F1',start,end),))
    r=replace(r,target=replace(r.target,graph=replace(r.target.graph,nodes=(n,)+r.target.graph.nodes[1:])))
    with pytest.raises(ValidationError): validate_record(r)

@pytest.mark.parametrize('old,new',[
    ('ABSTAIN false','ABSTAIN maybe'),('NIGAMANA proved c2','NIGAMANA proved ghost'),
    ('ROLE e KARTR a','ROLE a KARTR b'),('ROLE e KARTR a','ROLE e KARTR a extra'),
    ('PRATIJNA e applies_s416 false','PRATIJNA e unknown false'),
    ('HETU personation','HETU ghost'),('R416_NEG','UNKNOWN_RULE'),
    ('premises=personation','premises=ghost'),('example=null','example=ghost'),
    ('personation e negated','personation e unknown'),('personation e negated','personation e invalid'),
    ('CLAIM c1 absent personation e','CLAIM c1 binds personation e'),
    ('CLAIM c1 absent personation e','CLAIM c1 absent personation'),
    ('BY R416_NEG c1','BY R416_NEG c2'),
    ('CITE P1 "IPC" "416"','CITE P1 "IPC" "999"'),
    ('HETU personation','HETU personation\nHETU personation'),
    ('UPANAYA x=e','UPANAYA x=e x=e'),
])
def test_rejections(old,new):
    r=first()
    with pytest.raises(ParseError): parse(serialize(r).replace(old,new),r.prompt)

def test_abstention_cannot_certify():
    r=from_dict(load(FILES[10])['record'])
    r=replace(r,target=replace(r.target,hetvabhasa=replace(r.target.hetvabhasa,verdict=Verdict.VALID)))
    with pytest.raises(ValidationError,match='cannot certify'): validate_record(r)

def test_hash_and_json_strictness():
    r=first(); p=replace(r.prompt,passages=(replace(r.prompt.passages[0],text='tampered'),))
    with pytest.raises(ValidationError,match='hash'): validate_record(replace(r,prompt=p))
    d=to_dict(r); d['surprise']=1
    with pytest.raises(ValueError): from_dict(d)
    d=to_dict(r); d['target']['abstain']='false'
    with pytest.raises(ValueError): from_dict(d)

@pytest.mark.parametrize('sapaksa', ['invented','mountain','empty','only_fire'])
def test_named_sapaksa_required(sapaksa):
    world={'mountain':{'smoke','fire'},'kitchen':{'smoke','fire'},'empty':set(),'only_fire':{'fire'}}
    assert evaluate(world,Syllogism('mountain','fire','smoke',sapaksa)).verdict==Verdict.APRASIDDHA

def test_syllogism_verdicts():
    s=Syllogism('mountain','fire','smoke','kitchen')
    assert evaluate({'mountain':set()},s).verdict==Verdict.ASIDDHA
    assert evaluate({'mountain':{'smoke'}},s).verdict==Verdict.VIRUDDHA
    assert evaluate({'mountain':{'smoke'},'kitchen':{'smoke','fire'}},s).verdict==Verdict.SAVYABHICARA
    assert evaluate({'mountain':{'smoke','fire'},'kitchen':{'smoke','fire'}},s)==VerificationResult(Verdict.VALID,supporting=('kitchen',))
    with pytest.raises(ValueError): Syllogism('mountain','fire','smoke','')

@pytest.mark.parametrize('path',FILES[1:9],ids=lambda p:p.stem)
def test_lean_all_constructors(path):
    data=load(path); r=from_dict(data['record']); out=export_reading(r,registry=registry(data))
    op=r.target.graph.claims[0].op
    assert f'PrabhasaNyaya.Claim.{op} ' in out
    assert out.endswith('] } : PrabhasaNyaya.Reading)\n')
    if op=='provides': assert 'sectionNo := "416"' in out
    if op=='cites': assert 'volume := 470, reporter := "F.2d", page := 798' in out
    if op=='absent': assert 'pratiyogin :=' in out and 'adhikarana :=' in out

def test_target_excludes_audit():
    r=first(); wire=serialize_target(r)
    assert 'AUDIT' not in wire and 'RECORD' not in wire
    assert parse(wire,r.prompt).target==r.target

def test_json_duplicate_metadata_and_inference_boundary():
    r=first(); wire=serialize(r)
    with pytest.raises(ParseError,match='duplicate JSON'):
        parse(wire.replace('"id":"example_a"','"id":"example_a","id":"other"'),r.prompt)
    with pytest.raises(ParseError,match='supervisor metadata'): parse_target(wire,r.prompt)
    assert parse_target(serialize_target(r),r.prompt)==r.target

def test_unicode_line_separators_are_text():
    r=first(); r=replace(r,target=replace(r.target,answer='paragraph\u2028line\u2029next'))
    assert parse(serialize(r),r.prompt)==r

def test_leans_string_escaping():
    from nyaya_ir.lean import quote
    assert quote('a"b\\c\nd')=='"a\\"b\\\\c\\nd"'

def test_audit_is_losslessly_stored_but_not_certified():
    r=first()
    audit=replace(r.audit,split_group='a group with "quotes"',checker_report={'nested': {'message':'a "quoted" string', 'values':[True,None,3]}})
    r=replace(r,audit=audit)
    assert parse(serialize(r),r.prompt)==r
    assert not validate_record(r)['certified']
