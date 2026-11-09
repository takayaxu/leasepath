"""Same recorded states: discounted distribution with vs without validated cost.

No alternative cache trajectory or downstream savings are inferred. The cost
window, grid, fit and acceptance criteria are not tuned using these actions.
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
from leasepath.distribution_cache import DistributionCache,MarginalCostTable
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import read_json,read_jsonl,sha256
from leasepath.predictive_cache import PredictiveCache
from check_qwen_cache_matrix import check_actions


def audit_cost_sweeps(before,decision,owner,keys,table):
    blocks=deepcopy(before.blocks);bindings=deepcopy(before.bindings);bindings[owner]=tuple(keys)
    idle=set(before.idle)-{owner};audit=decision['distribution_audit'];features=audit['observed_idle_features']
    curves={c['observed_prefix_tokens']:{p['hit_tokens']:p['prefill_s'] for p in c['points']} for c in table['curves']}
    for sweep in audit['sweeps']:
        parents={b.parent for b in blocks.values() if b.parent is not None}
        leaves={k for k,b in blocks.items() if not b.active and k not in keys and k not in parents}
        assert leaves==set(sweep['legal_leaves'])
        live={k:set() for k in leaves};lengths={}
        for o,path in bindings.items():
            if o not in idle:continue
            length=0
            for key in path:
                if key not in blocks:break
                length+=128
                if key in live:live[key].add(o)
            lengths[o]=length
        unbound={k for k in leaves if not live[k]};compared=unbound or leaves
        assert compared=={r['key'] for r in sweep['candidates']}
        ranks={}
        for candidate in sweep['candidates']:
            key=candidate['key'];assert live[key]=={r['owner'] for r in candidate['owners']}
            vals=[]
            for item in candidate['owners']:
                o=item['owner'];prefix=features[o]['prefix_tokens'];length=lengths[o]
                assert item['observed_prefix_tokens']==prefix and item['resident_prefix_tokens']==length
                curve=curves.get(prefix,{})
                expected=1. if sweep['cost_fallback'] else curve[length-128]-curve[length]
                assert math.isclose(item['marginal_cost'],expected,abs_tol=1e-12)
                previous=0.;weight=0.
                for r in audit['owner_scores'][o]['distribution']:
                    if r['right_s']>30:break
                    weight+=(r['F_U']-previous)*math.exp(-r['right_s']/10.)
                    previous=r['F_U']
                assert math.isclose(item['weight'],weight,abs_tol=1e-14)
                vals.append(expected*weight)
            value=max(vals,default=0.)/33554432
            assert math.isclose(candidate['value'],value,abs_tol=1e-20)
            block=blocks[key];ranks[key]=(bool(live[key]),value,block.touched,-block.depth,key)
        assert min(ranks,key=ranks.__getitem__)==sweep['victim']
        del blocks[sweep['victim']]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,default=ROOT/'.artifacts/qwen-cache-resident-tree-natural-2026-11-09/run')
    parser.add_argument('--calibration',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();table=read_json(args.calibration/'cost-table.json');backend=read_json(args.calibration/'backend-identity.json')
    common={'model_generations':0,'cost_table_file_sha256':sha256(args.calibration/'cost-table.json'),
        'scope':'same_state_actions_only; no hit/prefill/JCT benefit inferred','calibration_validation':table['validation']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    if table['validation']['status']!='passed':
        result=common | {'status':'cost_policy_disabled','reason':'independent_calibration_validation_rejected'}
        args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2));return
    costs=MarginalCostTable(table,backend_identity=backend['sha256'])
    compatibility=read_json(args.run/'run-manifest.json')['runtime_compatibility']
    raw=GBDTRiskPredictor.load(ROOT/'results/prediction/qwen50_holdout_2026-11-08/gbdt-model.json')
    model=TransferredPredictor(raw,runtime_compatibility=compatibility,training_compatibility=raw.support.compatibilities[0])
    cases=[]
    for batch in sorted((args.run/'natural').glob('concurrency-*')):
        replica=PredictiveCache(24,block_size=128,block_bytes=33554432)
        events=[r for r in read_jsonl(batch/'raw/controller.jsonl') if r['kind']=='context_retire']
        events+=[r for r in read_jsonl(batch/'raw/backend.jsonl') if r['kind']=='engine_request']
        for event in sorted(events,key=lambda e:e['ts_ns']):
            row,now=event['data'],event['ts_ns']/1e9
            if event['kind']=='context_retire':replica.retire(row['owner'],now);continue
            features=row['policy_decision']['same_state_diagnostic']['observed_idle_features']
            assert set(features)==set(replica.idle)-{row['owner']}
            for o,f in features.items():replica.idle[o]=(deepcopy(f),now)
            keys=block_keys(row['tokens'][:((len(row['tokens'])-1)//128)*128],compatibility,128)
            if row['policy_decision']['evicted']:
                decisions={}
                for name,cost in [('equal',None),('calibrated',costs)]:
                    cache=DistributionCache(24,predictor=model,cost_table=cost,audit_shadows=True)
                    cache.blocks,cache.bindings,cache.idle=deepcopy((replica.blocks,replica.bindings,replica.idle))
                    cache.clock,cache.now=replica.clock,replica.now
                    decisions[name]=cache.start(row['owner'],keys,now)
                decision=decisions['calibrated'];audit_cost_sweeps(replica,decision,row['owner'],keys,table)
                victims={name:[r['key'] for r in d['evicted']] for name,d in decisions.items()}
                point=decision['same_state_diagnostic']['F_U_3s_victims']
                cases.append({'batch':batch.name,'request_id':row['request_id'],'victims':victims,
                    'cost_vs_equal_set_changed':set(victims['equal'])!=set(victims['calibrated']),
                    'cost_vs_point_set_changed':set(point)!=set(victims['calibrated']),
                    'cost_fallback_sweeps':sum(s['cost_fallback'] for s in decision['distribution_audit']['sweeps']),
                    'distribution_fallback_sweeps':sum(s['distribution_fallback'] for s in decision['distribution_audit']['sweeps']),
                    'audit':decision['distribution_audit']})
            check_actions(replica,row,compatibility,128,now)
    result=common | {'status':'passed','pressure_states':len(cases),
        'cost_vs_equal_victim_set_changes':sum(c['cost_vs_equal_set_changed'] for c in cases),
        'cost_vs_point_victim_set_changes':sum(c['cost_vs_point_set_changed'] for c in cases),
        'cost_fallback_sweeps':sum(c['cost_fallback_sweeps'] for c in cases),
        'distribution_fallback_sweeps':sum(c['distribution_fallback_sweeps'] for c in cases),
        'source_sha256':sha256(__file__),'cases':cases}
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='cases'},indent=2))


if __name__=='__main__':main()
