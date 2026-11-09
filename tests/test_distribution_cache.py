from copy import deepcopy
import math
import random

import pytest

from leasepath.distribution_cache import DistributionCache, MarginalCostTable, distribution_scores
from leasepath.predictive_cache import CapacityUnavailable, PredictiveCache
from test_predictive_cache import features


def distribution(masses=(.2, .6, 0.), dead=(0., 0., 0.)):
    fu = fd = 0.
    rows = []
    for edge, mass, d in zip((3., 10., 30.), masses, dead):
        survival = 1-fu-fd
        fu += mass; fd += d
        rows.append(dict(right_s=edge, supported=True, global_risk_workflows=10,
            hU=mass/survival, hD=d/survival, h0=1-(mass+d)/survival,
            F_U=fu, F_D=fd, survival=1-fu-fd))
    return rows


class Predictor:
    def __init__(self, by_tool=None):
        self.by_tool = by_tool or {}
        self.inputs = []

    def predict(self, f):
        self.inputs.append(deepcopy(f))
        return deepcopy(self.by_tool.get(f['tool_name'], distribution()))


def add(cache, owner, keys, tool='compute', now=0):
    cache.start(owner, keys, now)
    f = features() | {'prefix_tokens': len(keys)*128, 'tool_name': tool}
    cache.release(owner, f, now)


def costs(curves):
    doc = {'version': 1, 'backend_identity': 'fixture', 'block_tokens': 128,
        'validation': {'status': 'passed'}, 'curves': [
            {'observed_prefix_tokens': p, 'points': [{'hit_tokens': h, 'prefill_s': s} for h,s in curve.items()]}
            for p,curve in curves.items()]}
    return MarginalCostTable(doc, backend_identity='fixture')


def test_full_distribution_changes_tied_point_and_preserves_competing_risk_mass():
    early, late = distribution(), distribution((.2, 0., .6))
    a, b = distribution_scores(early), distribution_scores(late)
    assert a['F_U_3s'] == b['F_U_3s']
    assert a['W'] > b['W']
    assert a['W'] == pytest.approx(.2*math.exp(-.3)+.6*math.exp(-1))
    d = distribution_scores(distribution((.2, .3, 0.), (0., .2, .1)))
    assert d['W'] == pytest.approx(.2*math.exp(-.3)+.3*math.exp(-1))
    assert d['F_U_H'] == .5 and d['survival_H'] == pytest.approx(.2)
    predictor = Predictor({'compute': early, 'inspect_input': late})
    c = DistributionCache(2, predictor=predictor, audit_shadows=True)
    add(c,'a',['a']); add(c,'b',['b'],'inspect_input')
    result = c.start('c',['c'],2)
    assert result['evicted'][0]['key'] == 'b'
    assert result['same_state_diagnostic']['F_U_3s_victims'] == ['a']
    assert len(predictor.inputs) == 2  # actual and both shadows share one snapshot
    assert all(f['elapsed_idle_s'] == 2 and f['tool_elapsed_s'] == 2 for f in predictor.inputs)


def test_shared_max_product_and_leaf_marginal_recomputed():
    # A: W high, cost 1. B: W low, cost 10. Shared value is max(1*Wa,10*Wb).
    p = Predictor({'inspect_input': distribution((.05, 0., 0.))})
    table = costs({128:{0:1.,128:0.},256:{0:12.,128:10.,256:0.}})
    c = DistributionCache(3,predictor=p,cost_table=table)
    add(c,'a',['shared']); add(c,'b',['shared','tail'],'inspect_input')
    add(c,'d',['d'])
    result = c.start('new',['new','n2','n3'],1)
    sweeps = result['distribution_audit']['sweeps']
    shared = next(x for s in sweeps for x in s['candidates'] if x['key']=='shared')
    assert {x['owner']:x['marginal_cost'] for x in shared['owners']} == {'a':1.,'b':2.}
    assert shared['value'] == max(x['weighted_cost'] for x in shared['owners'])/33554432
    assert shared['value'] < max(x['marginal_cost'] for x in shared['owners'])*max(x['weight'] for x in shared['owners'])/33554432
    assert len(p.inputs) == 3
    assert set(c.blocks)=={'new','n2','n3'}


def test_uniform_cost_fallback_and_monotone_table_validation():
    c = DistributionCache(2,predictor=Predictor(),cost_table=costs({128:{0:2.,128:0.}}))
    add(c,'a',['a']); add(c,'b',['b'])
    c.observe('b',features() | {'prefix_tokens':256},0)
    result = c.start('c',['c'],1)
    s=result['distribution_audit']['sweeps'][0]
    assert s['cost_fallback'] and s['cost_units']=='equal_cost_unit'
    assert [o['marginal_cost'] for row in s['candidates'] for o in row['owners']] == [1.,1.]
    with pytest.raises(ValueError,match='monotone'):
        costs({128:{0:0.,128:1.}})
    with pytest.raises(ValueError,match='backend'):
        MarginalCostTable(c.cost_table.document,backend_identity='other')


@pytest.mark.parametrize('kind',['missing','low_support','nan','unsupported'])
def test_uniform_distribution_fallback_does_not_treat_unknown_as_dead(kind):
    bad = distribution()
    if kind=='missing':bad.pop()
    if kind=='low_support':bad[-1]['global_risk_workflows']=4
    if kind=='nan':bad[0]['F_U']=float('nan')
    if kind=='unsupported':
        bad[-1].update(supported=False, **dict.fromkeys(['hU','hD','h0','F_U','F_D','survival']))
    c=DistributionCache(2,predictor=Predictor({'inspect_input':bad}))
    add(c,'a',['a']);add(c,'b',['b'],'inspect_input')
    result=c.start('c',['c'],1)
    assert result['evicted'][0]['key']=='a' # older known owner, not unsupported newer b
    assert result['evicted'][0]['reason']=='lru_fallback_unsupported'
    assert all(row['value']==0 for row in result['distribution_audit']['sweeps'][0]['candidates'])
    import json
    json.dumps(result,allow_nan=False)


def test_no_prediction_before_pressure_and_unbound_first_and_invalidation():
    p=Predictor();c=DistributionCache(3,predictor=p)
    add(c,'a',['a']);add(c,'b',['b']);c.retire('a',0)
    add(c,'d',['d']); assert p.inputs==[]
    add(c,'e',['e'],now=1); assert p.inputs==[]
    assert c.last_evictions[0]['key']=='a'
    add(c,'f',['f'],now=2); assert p.inputs
    old=len(p.inputs)
    c.observe('e',features() | {'tool_name':'inspect_input'},2)
    c.start('g',['g'],3)
    assert len(p.inputs)>old
    assert any(f['tool_name']=='inspect_input' and f['elapsed_idle_s']==1 for f in p.inputs[old:])


def test_active_protection_atomic_failures_and_shared_retirement():
    c=DistributionCache(2,predictor=Predictor())
    add(c,'a',['shared']);add(c,'b',['shared','tail']);c.retire('a',0)
    assert set(c.blocks)=={'shared','tail'}
    c.start('b',['shared','tail'],1)
    before=deepcopy((c.blocks,c.bindings,c.idle,c.clock,c.now))
    with pytest.raises(CapacityUnavailable):c.start('c',['c'],2)
    assert (c.blocks,c.bindings,c.idle,c.clock,c.now)==before
    c.release('b',features() | {'prefix_tokens':256},2)
    before=deepcopy((c.blocks,c.bindings,c.idle,c.clock,c.now))
    with pytest.raises(ValueError,match='ancestry'):c.start('c',['x','shared'],3)
    assert (c.blocks,c.bindings,c.idle,c.clock,c.now)==before
    with pytest.raises(ValueError,match='allowlist'):c.observe('b',features() | {'future_suffix': 128},3)


def test_tied_distributions_match_independent_lru_with_random_shared_prefixes():
    rng=random.Random(5519)
    c=DistributionCache(7,predictor=Predictor())
    reference=PredictiveCache(7,policy='retired_first_lru')
    paths=[['root'+str(i//3),'branch'+str(i),'leaf'+str(i)] for i in range(9)]
    for now in range(100):
        owner='owner'+str(rng.randrange(8)); keys=rng.choice(paths)[:rng.randrange(1,4)]
        a=c.start(owner,keys,now);b=reference.start(owner,keys,now)
        assert [r['key'] for r in a['evicted']]==[r['key'] for r in b['evicted']]
        assert a['cached_blocks']==b['cached_blocks'] and c.blocks==reference.blocks
        f=features() | {'prefix_tokens':len(keys)*128}
        c.release(owner,f,now);reference.release(owner,f,now)
        if rng.random()<.3:c.retire(owner,now);reference.retire(owner,now)


def test_unsupported_owner_exposed_later_sticks_to_lru_for_remaining_sweeps():
    invalid=distribution();invalid[-1]['global_risk_workflows']=1
    p=Predictor({'inspect_input':invalid})
    c=DistributionCache(3,predictor=p)
    add(c,'root_owner',['root'],'inspect_input')
    add(c,'leaf_owner',['root','tail'])
    add(c,'other',['other'])
    # First sweep only sees the supported leaf_owner and other. Deleting tail
    # reveals root_owner, which must trigger uniform fallback without rescoring.
    result=c.start('new',['n1','n2','n3'],1)
    sweeps=result['distribution_audit']['sweeps']
    assert sweeps[0]['victim']=='tail' and not sweeps[0]['distribution_fallback']
    assert sweeps[1]['distribution_fallback'] and sweeps[2]['distribution_fallback']
    assert len(p.inputs)==3


def test_supported_three_second_point_is_usable_when_long_window_is_unsupported():
    rows=distribution();rows[-1]['global_risk_workflows']=4
    c=DistributionCache(2,policy='predicted_reuse',predictor=Predictor({'inspect_input':rows}))
    add(c,'a',['a']);add(c,'b',['b'],'inspect_input')
    result=c.start('c',['c'],1)
    assert not result['distribution_audit']['sweeps'][0]['distribution_fallback']
    assert result['evicted'][0]['reason']=='predicted_reuse'


def test_zero_reuse_and_retired_shared_owner_remain_well_defined():
    p=Predictor({'compute':distribution((0.,0.,0.),(.2,.2,.2))})
    c=DistributionCache(2,predictor=p)
    add(c,'a',['shared']);add(c,'alias',['shared']);add(c,'b',['b'])
    c.retire('a',0)
    result=c.start('new',['new'],1)
    assert result['evicted'][0]['live_owners']==['alias']
    assert all(score['W']==0. for score in result['distribution_audit']['owner_scores'].values())
