"""Independent greedy same-state mechanism audit: C, C*F_U(3), C*W.

No new workload trajectory or timing benefit; no synthetic predictor is used.
"""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.cache import block_keys
from leasepath.cache_policy_models import TransferredPredictor
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import read_json,read_jsonl,sha256
from leasepath.predictive_cache import PredictiveCache
from check_qwen_cache_matrix import check_actions


def greedy(replica,owner,keys,features,scores,curves,mode):
    blocks=deepcopy(replica.blocks);bindings=deepcopy(replica.bindings);bindings[owner]=tuple(keys)
    missing=len(keys)-replica.prefix_hits(keys);victims=[];fallback=False
    while len(blocks)+missing>24:
        parents={b.parent for b in blocks.values() if b.parent is not None}
        leaves={k for k,b in blocks.items() if k not in keys and not b.active and k not in parents}
        live={k:[] for k in leaves};lengths={}
        for o in features:
            length=0
            for k in bindings[o]:
                if k not in blocks:break
                length+=128
                if k in live:live[k].append(o)
            lengths[o]=length
        candidates={k for k in leaves if not live[k]} or leaves
        costs={}
        for o in {o for k in candidates for o in live[k]}:
            curve=curves.get(features[o]['prefix_tokens'],{});length=lengths[o]
            costs[o]=curve[length-128]-curve[length] if length-128 in curve and length in curve else None
        fallback=fallback or any(c is None for c in costs.values())
        def rank(k):
            value=max(((1. if fallback else costs[o])*scores[o][mode] for o in live[k]),default=0.)/33554432
            b=blocks[k];return (bool(live[k]),value,b.touched,-b.depth,k)
        victim=min(candidates,key=rank);victims.append(victim);del blocks[victim]
    return victims


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,default=ROOT/'.artifacts/qwen-cache-resident-tree-natural-2026-11-09/run')
    p.add_argument('--calibration',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    doc=read_json(args.calibration/'cost-table.json');assert doc['validation']['status']=='passed'
    curves={c['observed_prefix_tokens']:{p['hit_tokens']:p['prefill_s'] for p in c['points']} for c in doc['curves']}
    compat=read_json(args.run/'run-manifest.json')['runtime_compatibility']
    raw=GBDTRiskPredictor.load(ROOT/'results/prediction/qwen50_holdout_2026-11-08/gbdt-model.json')
    model=TransferredPredictor(raw,runtime_compatibility=compat,training_compatibility=raw.support.compatibilities[0])
    reference=read_json(ROOT/'results/cache_policy/kv_distribution_v1_2026-11-09/cost-action-audit.json')
    expected={(c['batch'],c['request_id']):c['victims']['calibrated'] for c in reference['cases']}
    cases=[]
    for batch in sorted((args.run/'natural').glob('concurrency-*')):
        replica=PredictiveCache(24,block_size=128,block_bytes=33554432)
        events=[e for e in read_jsonl(batch/'raw/controller.jsonl') if e['kind']=='context_retire']
        events += [e for e in read_jsonl(batch/'raw/backend.jsonl') if e['kind']=='engine_request']
        for event in sorted(events,key=lambda e:e['ts_ns']):
            row,now=event['data'],event['ts_ns']/1e9
            if event['kind']=='context_retire':replica.retire(row['owner'],now);continue
            features=row['policy_decision']['same_state_diagnostic']['observed_idle_features']
            if row['policy_decision']['evicted']:
                scores={}
                for o,f in features.items():
                    distribution=model.predict(f);previous=0.;weighted=0.;point=None
                    for r in distribution:
                        if r['right_s']>30:break
                        assert r['supported'] and r['global_risk_workflows']>=5
                        weighted+=(r['F_U']-previous)*math.exp(-r['right_s']/10.);previous=r['F_U']
                        if r['right_s']==3:point=r['F_U']
                    scores[o]={'cost_only':1.,'cost_point':point,'cost_weighted':weighted}
                keys=block_keys(row['tokens'][:((len(row['tokens'])-1)//128)*128],compat,128)
                victims={mode:greedy(replica,row['owner'],keys,features,scores,curves,mode) for mode in ('cost_only','cost_point','cost_weighted')}
                assert victims['cost_weighted']==expected[batch.name,row['request_id']]
                cases.append({'batch':batch.name,'request_id':row['request_id'],'victims':victims,
                    'weighted_vs_cost_only_changed':set(victims['cost_weighted'])!=set(victims['cost_only']),
                    'weighted_vs_cost_point_changed':set(victims['cost_weighted'])!=set(victims['cost_point'])})
            check_actions(replica,row,compat,128,now)
    result={'status':'passed','pressure_states':len(cases),'model_generations':0,
        'weighted_vs_cost_only_victim_set_changes':sum(c['weighted_vs_cost_only_changed'] for c in cases),
        'weighted_vs_cost_point_victim_set_changes':sum(c['weighted_vs_cost_point_changed'] for c in cases),
        'scope':'independent greedy reconstruction at old recorded states; no new cache trajectory/JCT inferred',
        'source_sha256':sha256(__file__),'cases':cases}
    args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k!='cases'},indent=2))


if __name__=='__main__':main()
