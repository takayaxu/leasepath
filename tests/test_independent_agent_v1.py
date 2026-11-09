import itertools
import json
from pathlib import Path
import sys
import pytest
from leasepath.independent_agent_tasks import fixtures,evaluate
from leasepath.independent_agent_reference import solve
from leasepath.independent_agent_protocol import check_final,certify_loop

TASKS=fixtures()

@pytest.mark.parametrize('task',TASKS,ids=lambda t:t['kind'])
def test_independent_reference_is_feasible_and_optimal(task):
    r=solve(task)
    assert check_final(task,r,{'candidate':r['candidate']})==(True,None)
    assert not check_final(task,r,{'candidate':None})[0]
    assert not check_final(task,r,{'candidate':r['candidate'],'oracle':1})[0]

@pytest.mark.parametrize('kind,bad',[
 ('job_shop',[0,0,0,0]),('propositional_sat',[0]*5),
 ('package_resolution',[1]*4),('max_flow',[3,2,0,0,0]),('exact_cover',[0]),
 ('error_correction',[0]*3),('bin_packing',[0]*5),('automata_reachability','aaa'),
 ('access_control',[1]*5),('reaction_balance',[1]*3),('voting_rule',['A','B','C']),('edit_distance','MMM')])
def test_actual_wrong_answers_rejected(kind,bad):
    t=next(t for t in TASKS if t['kind']==kind)
    assert not check_final(t,solve(t),{'candidate':bad})[0]

@pytest.mark.parametrize('kind,suboptimal',[
 ('job_shop',[1,3,1,3]),('max_flow',[0]*5),('bin_packing',[0,1,2,3,4]),
 ('automata_reachability','ababa'),('reaction_balance',[8,6,4]),('edit_distance','SSSD')])
def test_feasible_suboptimal_answers_rejected(kind,suboptimal):
    t=next(t for t in TASKS if t['kind']==kind)
    assert evaluate(t,suboptimal)['valid']
    assert not check_final(t,solve(t),{'candidate':suboptimal})[0]


def test_reference_is_separate_from_tool_evaluator():
    import leasepath.independent_agent_reference as reference
    src=Path(reference.__file__).read_text()
    assert 'independent_agent_tasks' not in src and 'evaluate(' not in src


def test_loop_requires_consumed_feedback_changed_candidate_and_improvement():
    def call(i,candidate,valid,score):
        return dict(call_id=str(i),name='evaluate_candidate',status='ok',arguments={'candidate':candidate},started_ns=10*i,ended_ns=10*i+1,result={'valid':valid,'objective_minimize':score,'violations':[]})
    a=call(1,[0],False,5);b=call(2,[1],True,3)
    def turn(call):return {'input_ns':call['ended_ns']+1,'prompt':json.dumps(call['result'],separators=(',',':'))}
    assert certify_loop([turn(a),turn(b)],[a,b],successful=True)['behavioral_loop_supported']
    assert not certify_loop([turn(a)],[a],successful=True)['behavioral_loop_supported']
    assert not certify_loop([],[a,b],successful=True)['behavioral_loop_supported']
    assert not certify_loop([turn(a),turn(b)],[a,b],successful=False)['behavioral_loop_supported']
    b['arguments']=a['arguments']
    assert not certify_loop([turn(a),turn(b)],[a,b],successful=True)['behavioral_loop_supported']


def test_all_old_parents_and_source_groups_excluded():
    root=Path(__file__).resolve().parents[1]
    old=json.loads((root/'results/cache_policy/kv_distribution_next_stage_2026-11-09/seen-parent-exclusion.json').read_text())['parents']
    assert len(old)==50
    assert not {t['id'] for t in TASKS}&{t['parent_id'] for t in old}
    assert not {t['source_family'] for t in TASKS}&{t['source_group'] for t in old}
    assert not {t['source_family'] for t in TASKS if t['split']=='development'}&{t['source_family'] for t in TASKS if t['split']=='holdout'}
