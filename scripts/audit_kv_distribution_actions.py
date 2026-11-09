"""Compare legal victim actions on independently reconstructed recorded states.

This is not a new cache trajectory: after each comparison the recorded actual
victims are applied. No future outcome, new hit benefit, or JCT is inferred.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.cache import block_keys
from leasepath.cache_policy_models import TransferredPredictor
from leasepath.distribution_cache import DistributionCache
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.predictive_cache import PredictiveCache
from leasepath.prediction_training import read_json, read_jsonl, sha256
from leasepath.reuse_predictor import EmpiricalRiskPredictor
from check_qwen_cache_matrix import check_actions


def independent_sweeps(before, decision, owner, keys):
    """Audit the physical leaf set, owner max, score arithmetic, and victim tie."""
    blocks=deepcopy(before.blocks)
    bindings=deepcopy(before.bindings);bindings[owner]=tuple(keys)
    idle=set(before.idle)-{owner}
    audit=decision['distribution_audit']
    for sweep in audit['sweeps']:
        parents={b.parent for b in blocks.values() if b.parent is not None}
        leaves={k for k,b in blocks.items() if k not in keys and not b.active and k not in parents}
        assert leaves==set(sweep['legal_leaves'])
        live={k:set() for k in leaves}
        for o,path in bindings.items():
            if o not in idle:continue
            for key in path:
                if key not in blocks:break
                if key in live:live[key].add(o)
        unbound={k for k in leaves if not live[k]}
        compared=unbound or leaves
        assert compared=={r['key'] for r in sweep['candidates']}
        ranks={}
        for row in sweep['candidates']:
            key=row['key'];assert live[key]=={r['owner'] for r in row['owners']}
            values=[]
            for value in row['owners']:
                score=audit['owner_scores'][value['owner']]
                mass_before=0.;w=0.
                for interval in score['distribution']:
                    if interval['right_s']>30:break
                    assert interval['supported'] and interval['global_risk_workflows']>=5
                    w+=(interval['F_U']-mass_before)*math.exp(-interval['right_s']/10.)
                    mass_before=interval['F_U']
                assert math.isclose(w,score['W'],abs_tol=1e-14)
                assert value['marginal_cost']==1. and math.isclose(value['weighted_cost'],w,abs_tol=1e-14)
                values.append(w)
            expected=max(values,default=0.)/33554432
            assert math.isclose(row['value'],expected,abs_tol=1e-20)
            b=blocks[key];ranks[key]=(bool(live[key]),expected,b.touched,-b.depth,key)
            assert list(ranks[key])==row['rank']
        assert min(ranks,key=ranks.__getitem__)==sweep['victim']
        del blocks[sweep['victim']]


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,default=ROOT/'.artifacts/qwen-cache-resident-tree-natural-2026-11-09/run')
    p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    manifest=read_json(args.run/'run-manifest.json');compatibility=manifest['runtime_compatibility']
    directory=ROOT/'results/prediction/qwen50_holdout_2026-11-08'
    raw={'conditional':EmpiricalRiskPredictor.load(directory/'conditional-model.json'),
         'gbdt':GBDTRiskPredictor.load(directory/'gbdt-model.json')}
    models={name:TransferredPredictor(m,runtime_compatibility=compatibility,
        training_compatibility=getattr(m,'support',m).compatibilities[0]) for name,m in raw.items()}
    cases=[];counts=Counter();source_hashes={};original_checks=0
    for batch in sorted((args.run/'natural').glob('concurrency-*')):
        policy=batch.name.split('-')[-1]
        replica=PredictiveCache(24,block_size=128,block_bytes=33554432)
        controller_path=batch/'raw/controller.jsonl';backend_path=batch/'raw/backend.jsonl'
        for path in (controller_path,backend_path):source_hashes[str(path.relative_to(args.run))]=sha256(path)
        events=[e for e in read_jsonl(controller_path) if e['kind']=='context_retire']
        events += [e for e in read_jsonl(backend_path) if e['kind']=='engine_request']
        for event in sorted(events,key=lambda e:e['ts_ns']):
            row,now=event['data'],event['ts_ns']/1e9
            if event['kind']=='context_retire':replica.retire(row['owner'],now);continue
            counts['requests']+=1
            observed=row['policy_decision']['same_state_diagnostic']['observed_idle_features']
            assert set(observed)==set(replica.idle)-{row['owner']}
            # Use exact features captured before the recorded generation; event
            # timestamp is only the common clock origin, not a new elapsed estimate.
            for o,features in observed.items():replica.idle[o]=(deepcopy(features),now)
            keys=block_keys(row['tokens'][:((len(row['tokens'])-1)//128)*128],compatibility,128)
            if row['policy_decision']['evicted']:
                counts['pressure_requests']+=1
                case={'batch':batch.name,'request_id':row['request_id'],'owner':row['owner'],
                      'recorded_policy':policy,'models':{}}
                for name,model in models.items():
                    old=PredictiveCache(24,block_size=128,block_bytes=33554432,policy='predicted_reuse',predictor=model)
                    new=DistributionCache(24,predictor=model,audit_shadows=True)
                    for target in (old,new):
                        target.blocks,target.bindings,target.idle=deepcopy((replica.blocks,replica.bindings,replica.idle))
                        target.clock,target.now=replica.clock,replica.now
                    before=old.start(row['owner'],keys,now);after=new.start(row['owner'],keys,now)
                    point=[r['key'] for r in before['evicted']];weighted=[r['key'] for r in after['evicted']]
                    assert point==after['same_state_diagnostic']['F_U_3s_victims']
                    if policy==name:
                        assert point==[r['key'] for r in row['policy_decision']['evicted']]
                        original_checks+=1
                    independent_sweeps(replica,after,row['owner'],keys)
                    case['models'][name]={'point_victims':point,'weighted_victims':weighted,
                        'set_changed':set(point)!=set(weighted),'sequence_changed':point!=weighted,
                        'fallback_sweeps':new.fallback_sweeps,'audit':after['distribution_audit']}
                cases.append(case)
            check_actions(replica,row,compatibility,128,now)
    summary={}
    for name in models:
        values=[case['models'][name] for case in cases]
        summary[name]={'pressure_states':len(values),'victim_set_changes':sum(v['set_changed'] for v in values),
            'victim_sequence_changes':sum(v['sequence_changed'] for v in values),
            'fallback_sweeps':sum(v['fallback_sweeps'] for v in values),
            'by_recorded_policy':{policy:{'states':sum(c['recorded_policy']==policy for c in cases),
                'set_changes':sum(c['models'][name]['set_changed'] for c in cases if c['recorded_policy']==policy)}
                for policy in ('retired_first_lru','conditional','gbdt')}}
    result={'status':'passed','scope':'same_state_legal_victims_only','model_generations':0,
        'parameters':{'H_s':30,'tau_s':10,'cost':'equal','min_global_workflows':5},
        'counts':dict(counts),'recorded_predictive_actions_verified':original_checks,'summary':summary,
        'interpretation':'Recorded states, independently reconstructed. No new hit/prefill/JCT benefit inferred.',
        'evidence_sha256':source_hashes,'source_sha256':{str(p.relative_to(ROOT)):sha256(p) for p in
            [Path(__file__).resolve(),ROOT/'src/leasepath/distribution_cache.py',*(directory/(n+'-model.json') for n in models)]},'cases':cases}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in {'cases','evidence_sha256','source_sha256'}},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
