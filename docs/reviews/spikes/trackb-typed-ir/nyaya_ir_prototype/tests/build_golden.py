"""Rebuild authored synthetic fixtures offline; never reads held-out law data."""
import hashlib
import json
from pathlib import Path
from dataclasses import replace
from nyaya_ir import *

ROOT=Path(__file__).resolve().parents[1]
def prompt(fact2):
    text='Section 416 requires cheating through personation.'
    return Prompt('Does section 416 apply to event E?',(Fact('F1','A deceives B into paying money.'),Fact('F2',fact2)),'IN',LegalScope('IPC','fixture-v1','stipulated_statute'),(Passage('P1','IPC','416','fixture-v1','fixture:P1',hashlib.sha256(text.encode()).hexdigest(),text),))
def base(negative=True):
    p=prompt("A uses only A's true identity and makes no false identity representation about anyone." if negative else 'The account gives no information about any identity representation.')
    s=lambda x: Span(x,0,len(p.sources()[x]))
    ns=tuple(Node(i,PadarthaCategory(cat),Sorta(sort),(s(src),)) for i,cat,sort,src in [('a','DRAVYA','party','F1'),('b','DRAVYA','party','F1'),('e','KRIYA','act','F1'),('s416','SAMANYA','provision','P1'),('personation','GUNA','element','P1')])
    cs=(Claim('c1',ClaimOp.absent,('personation','e'),(s('F2'),)),Claim('c2',ClaimOp.absent,('s416','e'),(), 'R416_NEG',('c1',))) if negative else ()
    tr=Trace(Pratijna('e','applies_s416',not negative),Hetu(('personation',)),Udaharana('R416_NEG' if negative else 'R416_POS',('P1',),None),Upanaya({'x':'e'},('personation',) if negative else ()),Nigamana('proved' if negative else 'not_proved',('c2',) if negative else ()))
    target=Target(Graph(ns,(Binding('e',KarakaRole.KARTR,'a'),Binding('e',KarakaRole.KARMAN,'b')),cs),(Element('personation','e','negated' if negative else 'unknown',(s('F2'),)),),tr,Hetvabhasa('legal-rules-v1',Verdict.VALID if negative else None,None,None),(CitedSection('P1','IPC','416'),),not negative,None if negative else 'missing_fact','Section 416 does not apply on these facts: personation is explicitly absent.' if negative else 'I cannot determine whether section 416 applies: the account does not establish or negate personation.')
    r=Record('nyaya-law-v1','example_a' if negative else 'example_b','law_apply' if negative else 'law_abstain',p,target,Audit('fixture_contract','fixture-only','fixture-only','synthetic',{}))
    return replace(r,audit=replace(r.audit,checker_report=validate_record(r)))
def write(name,r,**extra):
    payload={'record':to_dict(r), 'wire':serialize(r),**extra}
    (ROOT/'golden'/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
def main():
    a,b=base(),base(False)
    write('01_example_a.json',a)
    # Eight positive cases exercise each Lean constructor; a ninth case tests multiple events.
    for i,op in enumerate(ClaimOp,2):
        sentences={
            'provides': 'The supplied provision says: Section 416 requires cheating through personation.',
            'cites': 'The stipulated authority bears the citation 470 F.2d 798.',
            'establishes': 'The stipulated authority establishes the holding that personation is necessary.',
            'applies': 'For this authored application, cheating and personation are established for event E; section 416 applies.',
            'satisfies': 'During event E, A falsely represented being B; personation is established.',
            'binds': 'Under the stipulated court hierarchy, court1 binds court2.',
            'entitles': 'Under the stipulated remedy rule, party A is entitled to the specified remedy.',
            'absent': "A uses only A's true identity and makes no false identity representation about anyone.",
        }
        p=replace(a.prompt,question=f'What {op} claim does the stipulated source license?',facts=(Fact('F1',sentences[op]),Fact('F2',sentences[op])))
        sp=Span('F1',0,len(p.facts[0].text))
        extra=(Node('authority',PadarthaCategory.DRAVYA,Sorta.authority,(sp,)),Node('holding',PadarthaCategory.SAMANYA,Sorta.holding,(sp,)),Node('court1',PadarthaCategory.DRAVYA,Sorta.court,(sp,)),Node('court2',PadarthaCategory.DRAVYA,Sorta.court,(sp,)),Node('remedy',PadarthaCategory.SAMANYA,Sorta.remedy,(sp,)))
        args={'provides':('s416','provision_text'),'cites':('authority','case_cite'),'establishes':('authority','holding'),'applies':('s416','e'),'satisfies':('e','personation'),'binds':('court1','court2'),'entitles':('a','remedy'),'absent':('personation','e')}[op]
        claim=Claim('result',op,args,(sp,))
        base_nodes=tuple(replace(n,spans=(sp,)) if n.id in ('a','b','e') else n for n in a.target.graph.nodes)
        t=replace(a.target,graph=Graph(base_nodes+extra,a.target.graph.bindings,(claim,)),elements=(replace(a.target.elements[0],status='negated' if op=='absent' else ('established' if op in ('applies','satisfies') else 'unknown'),evidence=(sp,)),),trace=replace(a.target.trace,pratijna=Pratijna('e','applies_s416',op!='absent'),nigamana=Nigamana('proved',('result',))),answer=sentences[op],hetvabhasa=Hetvabhasa('legal-rules-v1',None,None,None))
        r=replace(a,id=f'operator_{op}',prompt=p,target=t)
        reg=Registry(texts={'provision_text':p.passages[0].text},citations={'case_cite':Cite(470,'F.2d',798)})
        payload={'record':to_dict(r),'wire':serialize(r,registry=reg),'registry':{'texts':reg.texts,'citations':{k:to_dict(v) for k,v in reg.citations.items()}}}
        (ROOT/'golden'/f'{i:02}_operator_{op}.json').write_text(json.dumps(payload,indent=2)+'\n')
    g=a.target.graph
    multi=replace(a,id='joint_agents',target=replace(a.target,graph=replace(g,nodes=g.nodes+(Node('e2',PadarthaCategory.KRIYA,Sorta.act,g.nodes[2].spans),),bindings=g.bindings+(Binding('e',KarakaRole.KARTR,'b'),Binding('e2',KarakaRole.SAMPRADANA,'a')))))
    write('10_joint_agents.json',multi)
    write('11_example_b.json',b)
    for i,reason in enumerate(('missing_source','conflicting_authority','unsupported_rule','missing_fact','missing_source'),12):
        refusal_text={
            'missing_source': 'No provision text is supplied, so applicability cannot be assessed.',
            'conflicting_authority': 'The supplied authorities disagree on the applicable rule, and their priority is unresolved.',
            'unsupported_rule': 'The requested extension of the rule has no supported inference procedure.',
            'missing_fact': 'The account omits whether any false identity was represented.',
        }[reason]
        p=replace(b.prompt,facts=(b.prompt.facts[0],Fact('F2',refusal_text)))
        r=replace(b,id=f'abstain_{i}',prompt=p,target=replace(b.target,elements=(replace(b.target.elements[0],evidence=(Span('F2',0,len(refusal_text)),)),),abstain_reason=reason,answer=refusal_text))
        if reason=='missing_source':
            t=r.target
            ns=tuple(replace(n,spans=()) if n.id in ('s416','personation') else n for n in t.graph.nodes)
            r=replace(r,prompt=replace(r.prompt,passages=()),target=replace(t,graph=replace(t.graph,nodes=ns),cited_sections=(),trace=replace(t.trace,udaharana=replace(t.trace.udaharana,passage_ids=()))))
        write(f'{i:02}_abstain_{reason}.json',r)
    wire=serialize(a)
    bad=[('duplicate_node',wire.replace('ROLE e KARTR a','NODE a DRAVYA party @F1\nROLE e KARTR a'),'duplicate node'),('bad_span',wire.replace('@F2:0:83','@F2:0:999999') if '@F2:0:83' in wire else wire.replace('@F1:0:30','@F1:0:999999'),'invalid span'),('unknown_operator',wire.replace('CLAIM c1 absent','CLAIM c1 invent'),'ClaimOp'),('dangling_binding',wire.replace('ROLE e KARTR a','ROLE e KARTR ghost'),'unknown node')]
    # Replace span by pattern rather than depending on sentence length.
    import re
    bad[1]=('bad_span',re.sub(r'@F1:0:\d+','@F1:0:999999',wire),'invalid span')
    for i,(name,text,error) in enumerate(bad,17):
        (ROOT/'golden'/f'{i:02}_reject_{name}.json').write_text(json.dumps({'prompt':to_dict(a.prompt),'wire':text,'reject':error},indent=2)+'\n')
if __name__=='__main__': main()
