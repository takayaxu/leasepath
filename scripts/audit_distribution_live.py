"""Independent score and legal-action audit for the live four-policy experiment."""
from copy import deepcopy
import math
from leasepath.cache import block_keys
from leasepath.predictive_cache import PredictiveCache
from audit_kv_distribution_actions import independent_sweeps
from audit_kv_distribution_cost_actions import audit_cost_sweeps


def audit_live_state(replica,row,compatibility,model,cost_document,now):
    decision=row['policy_decision'];audit=decision['distribution_audit']
    observed=audit['observed_idle_features']
    assert set(observed)==set(replica.idle)-{row['owner']}
    for owner,features in observed.items():replica.idle[owner]=(deepcopy(features),now)
    for owner,score in audit['owner_scores'].items():
        predicted=model.predict(observed[owner])
        assert predicted==score['distribution']
        previous=0.;weighted=0.;point=None
        for interval in predicted:
            if interval['right_s']>30:break
            assert interval['supported'] and interval['global_risk_workflows']>=5
            weighted+=(interval['F_U']-previous)*math.exp(-interval['right_s']/10.)
            previous=interval['F_U']
            if interval['right_s']==3:point=interval['F_U']
        assert math.isclose(score['W'],weighted,abs_tol=1e-14) and score['F_U_3s']==point
    keys=block_keys(row['tokens'][:((len(row['tokens'])-1)//128)*128],compatibility,128)
    old=PredictiveCache(24,block_size=128,block_bytes=33554432,policy='predicted_reuse',predictor=model)
    old.blocks,old.bindings,old.idle=deepcopy((replica.blocks,replica.bindings,replica.idle));old.clock,old.now=replica.clock,replica.now
    point=old.start(row['owner'],keys,now)
    victims=[v['key'] for v in point['evicted']]
    assert victims==decision['same_state_diagnostic']['F_U_3s_victims']
    if audit['policy']=='predicted_reuse':assert victims==[r['key'] for r in decision['evicted']]
    elif audit['policy']=='distribution_value':
        if audit['cost_table_sha256'] is None:independent_sweeps(replica,decision,row['owner'],keys)
        else:audit_cost_sweeps(replica,decision,row['owner'],keys,cost_document)
    return {'cost_fallback_sweeps':sum(s['cost_fallback'] for s in audit['sweeps']),
        'distribution_fallback_sweeps':sum(s['distribution_fallback'] for s in audit['sweeps']),
        'point_victim_set_changed':decision['same_state_diagnostic']['point_victim_set_differs'],
        'controller_prepare_s':row['controller_prepare_ns']/1e9,
        'decision_with_shadows_s':decision['same_state_diagnostic']['admission_with_shadow_total_ns']/1e9,
        'prediction_calls':audit['prediction_calls']}
