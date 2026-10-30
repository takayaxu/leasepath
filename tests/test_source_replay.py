from copy import deepcopy
import json
from pathlib import Path

import pytest

from leasepath.bfcl_runtime import Scenario
from leasepath.kv_layout import Layout
from leasepath.source_replay import keys_for,run

ROOT=Path(__file__).resolve().parents[1]


def case():
    return {'id':'variable','block_size':128,'arrivals':{'a':0,'b':.01},'requests':[
        {'id':'a0','workflow':'a','context':'a','tokens':list(range(301)),'dependencies':[]},
        {'id':'b0','workflow':'b','context':'b','tokens':list(range(1000,1400)),'dependencies':[]},
        {'id':'a1','workflow':'a','context':'a','tokens':list(range(340)),'dependencies':[['a0',.1]],'retire_context':True}]}


def cost(request,cached,resident):return 100+.2*(len(request['tokens'])-cached)


def test_unaligned_prompts_and_one_token_logits_recompute():
    assert len(keys_for(list(range(256)),128))==1
    assert len(keys_for(list(range(257)),128))==2
    assert keys_for(list(range(20)),128)==()
    result=run(case(),8,3,cost=cost)
    assert result['requests'][-1]['cached_tokens']==256
    assert result['requests'][-1]['prompt_tokens']==340


@pytest.mark.parametrize('plain,fixed',[('lru','fixed_ttl'),('retired_first_lru','fixed_ttl_retired_first')])
def test_variable_zero_ttl_equivalence_and_script_permissions(plain,fixed):
    a=run(case(),8,3,plain,cost=cost);b=run(case(),8,3,fixed,0,cost=cost)
    assert a['requests']==b['requests'] and a['workflow_jct_s']==b['workflow_jct_s']
    with pytest.raises(ValueError):run(case(),8,3,plain,script={'a0':1},cost=cost)
    policy='offline_script_retired_first' if 'retired_first' in fixed else 'offline_script'
    scripted=run(case(),8,3,policy,script={'a0':.5,'b0':.5,'a1':.5},cost=cost)
    fixed_result=run(case(),8,3,fixed,.5,cost=cost)
    assert scripted['requests']==fixed_result['requests']


def test_protection_budget_and_blocking_recomputed():
    baseline=run(case(),5,2,cost=cost)
    pinned=run(case(),5,2,'fixed_ttl',.5,cost=cost)
    assert pinned['protection_wait_s']>0
    assert pinned['mean_jct_s']>baseline['mean_jct_s']
    assert pinned['protected_byte_seconds']>0
    with pytest.raises(ValueError):run(case(),3,0,cost=cost)


def test_source_join_waits_for_both_children():
    raw=case();raw['requests'].append({'id':'join','workflow':'a','context':'coordinator','tokens':list(range(500,750)),
                                     'dependencies':[['a0',.3],['a1',.2]]})
    result=run(raw,8,2,cost=cost);rows={r['request_id']:r for r in result['requests']}
    assert rows['join']['ready_s']==max(rows['a0']['finish_s']+.3,rows['a1']['finish_s']+.2)


def test_layout_preserves_union_and_reuses_freed_cells():
    layout=Layout(1024,128)
    first=layout.step(('a','b'),0,301)
    assert first['peak_resident']==332 and first['idle_resident']==256
    assert first['idle_span']==256
    layout.drop(['b']);second=layout.step(('a','c'),1,340)
    assert second['missed_tokens']==212
    assert second['peak_resident']==371 and second['idle_resident']==256
    assert not set(layout.owners['a'])&set(layout.owners['c'])
    layout.drop(['a','c']);assert not layout.used and layout.head==0


def test_local_reference_calls_never_evaluate_arbitrary_python():
    tasks={r['id']:r for r in map(json.loads,(ROOT/'data/sources/bfcl_v4/tasks.jsonl').read_text().splitlines())}
    scenario=Scenario(ROOT/'data/sources/bfcl_v4/runtime',tasks['multi_turn_base_15'])
    for expression in ["__import__('os').system('false')",'open("/tmp/not-allowed", "w")','mean([x for x in [1]])']:
        assert scenario.call(expression)['exception'] is not None
    assert json.loads(scenario.call('mean([1,2,3])')['output'])=={'result':2.0}


def test_public_calls_are_really_executed_and_returned_values_are_hashed():
    import hashlib
    tasks={r['id']:r for r in map(json.loads,(ROOT/'data/sources/bfcl_v4/tasks.jsonl').read_text().splitlines())}
    scenario=Scenario(ROOT/'data/sources/bfcl_v4/runtime',tasks['multi_turn_base_15'])
    value=scenario.call('mean([2,4,6])')
    assert value['execution_ns']>0 and value['exception'] is None
    assert hashlib.sha256(value['output'].encode()).hexdigest()==value['output_sha256']


@pytest.mark.parametrize('script',[{'missing':1},{'a0':-1},{'a0':float('inf')}])
def test_bad_future_script_rejected(script):
    with pytest.raises(ValueError):run(case(),8,3,'offline_script',cost=cost,script=script)


def test_clock_excludes_fixture_setup_and_keeps_request_intervals():
    from leasepath.measurement_clock import PreparedClock
    readings=iter([100.,105.,105.2,105.7,107.])
    clock=PreparedClock(lambda:next(readings))
    start=clock()
    assert clock()-start==0
    assert clock.excluded_s==5
    assert clock()-start==pytest.approx(.2)
    assert clock()-start==pytest.approx(.7)
    assert clock()-start==pytest.approx(2)
