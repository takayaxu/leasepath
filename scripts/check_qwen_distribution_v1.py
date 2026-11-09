"""Independent raw-cell/token/ledger audit and a separate isotonic-fit oracle."""
import argparse
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.cache import block_keys
from leasepath.prediction_training import read_json,read_jsonl,sha256
from leasepath.predictive_cache import PredictiveCache
from check_qwen_cache_stage2 import check_native
from check_qwen_cache_matrix import check_actions
from audit_kv_distribution_actions import independent_sweeps


def require(condition,message):
    if not condition:raise ValueError(message)


def isotonic_oracle(values):
    # Independent min-max characterization; does not call production PAV.
    return [min(max(statistics.mean(values[a:b+1]) for b in range(i,len(values))) for a in range(i+1))
            for i in range(len(values))]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run=args.run;manifest=read_json(run/'run-manifest.json');plan=read_json(run/'frozen-plan.json')
    require(manifest['status']=='completed','run failed or incomplete; do not claim success')
    for source,digest in manifest['source_sha256'].items():
        require(sha256(source)==digest,'frozen source changed: '+source)
        source=Path(source)
        if source.is_relative_to(ROOT):require(sha256(run/'frozen-source'/source.relative_to(ROOT))==digest,'source copy differs')
    require(any(source.endswith('.json') and '/configs/prediction/' in source and read_json(source)==plan for source in manifest['source_sha256']),'plan differs')
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf',local_files_only=True)
    reservations={};phase_counts={}
    for phase,limits in plan['phases'].items():
        path=run/phase/'ledger.jsonl';ledger=read_jsonl(path);sealed=read_json(path.with_suffix('.sealed.json'))
        require(sealed['sealed'] and sealed['sha256']==sha256(path),'ledger not sealed')
        require(len(ledger)==limits['total'],'phase not fully executed')
        require(all(r['budget_limits']==limits for r in ledger),'ledger cap changed')
        for lane in ('probe','pilot'):require(sum(r['lane']==lane for r in ledger)<=limits[lane],'lane cap exceeded')
        for row in ledger:
            require(row['id'] not in reservations,'duplicate reservation');reservations[row['id']]=row | {'phase':phase}
        phase_counts[phase]=len(ledger)
    require(len(reservations)==plan['total_generation_hard_cap']==273,'total differs')
    seen=set();samples=[];primes=[];evidence={}
    for directory in ('native-gate','weighted-gate','calibration_fit','calibration_validation'):
        for path in sorted((run/directory).glob('*.json')):
            row=read_json(path)
            if 'reservation_id' not in row:continue
            reservation=row['reservation_id'];require(reservation in reservations and reservation not in seen,'unmapped or duplicate request')
            seen.add(reservation);evidence[str(path.relative_to(run))]=sha256(path)
            require(tokenizer.encode(row['prompt'],add_special_tokens=False)==row['tokens'],'client/native token mismatch')
            check_native(row,24,128,2048)
            if 'calibration' in row:
                sample=row['calibration'];entry=reservations[reservation]
                require(entry['scope']=='calibration_measurement' and entry['lane']=='pilot','sample lane/scope differs')
                require(all(entry[k]==sample[k] for k in ('split','repeat','prefix_tokens','hit_tokens')),'sample reservation differs')
                require(len(row['tokens'])==sample['prefix_tokens']+1,'observed prefix proxy differs')
                require(row['hit']==sample['hit_tokens'] and row['prefill']==len(row['tokens'])-row['hit'],'calibration token counts differ')
                require(row['reason']=='length' and len(row['output_tokens'])==1,'calibration decode differs')
                require(sample['prefill_s']==row['prefill_ns']/1e9,'native time differs')
                require(row['physical_before']==row['trim']['physical_after'],'trim physical snapshot differs')
                require(row['trim']['physical_after']['used_cells']==sample['hit_tokens'],'trim did not release exact cells')
                require(row['policy_decision']['evicted']==[],'unexpected competing calibration owner')
                require(sample['evidence']==str(path.relative_to(run)),'sample path differs')
                samples.append(sample)
            elif directory.startswith('calibration_'):
                primes.append(row)
                require(reservations[reservation]['scope']=='calibration_prime' and row['hit']==0,'prime reservation/cache differs')
    require(seen==set(reservations),'reservation without raw native evidence')
    require(len(samples)==237 and len(primes)==18,'calibration sample count differs')
    grid=Counter((r['prefix_tokens'],r['hit_tokens'],r['repeat']) for r in samples)
    expected={(p*128,h*128,repeat) for p in plan['prefix_blocks'] for h in range(p+1) for repeat in range(3)}
    require(set(grid)==expected and set(grid.values())=={1},'calibration grid incomplete')
    require(all((r['split']=='fit')==(r['repeat']<2) for r in samples),'fit/validation split differs')
    saved=read_json(run/'calibration-measurements.json')
    require(sorted(saved,key=lambda r:r['reservation_id'])==sorted(samples,key=lambda r:r['reservation_id']),'measurement summary differs')
    table=read_json(run/'cost-table.json');backend=read_json(run/'backend-identity.json')
    import hashlib
    require(hashlib.sha256(json.dumps(backend['configuration'],sort_keys=True).encode()).hexdigest()==backend['sha256']==table['backend_identity'],'backend identity differs')
    errors=[];validation=[];marginals=[]
    for curve in table['curves']:
        prefix=curve['observed_prefix_tokens'];raw=[]
        for hit in range(0,prefix+1,128):
            raw.append(statistics.mean(r['prefill_s'] for r in samples if r['prefix_tokens']==prefix and r['hit_tokens']==hit and r['split']=='fit'))
        fitted=isotonic_oracle(raw)
        for point,value,raw_value in zip(curve['points'],fitted,raw):
            require(math.isclose(point['prefill_s'],value,rel_tol=0,abs_tol=1e-12),'independent monotone fit differs')
            require(point['fit_raw_mean_s']==raw_value,'raw fit mean differs')
            val=next(r['prefill_s'] for r in samples if r['prefix_tokens']==prefix and r['hit_tokens']==point['hit_tokens'] and r['split']=='validation')
            require(point['validation_prefill_s']==val,'validation evidence differs')
            errors.append(abs(value-val));validation.append(val)
        marginals.extend(a-b for a,b in zip(fitted,fitted[1:]) if a-b>1e-12)
    mae=statistics.mean(errors);median=statistics.median(marginals) if marginals else 0.
    verdict=mae/statistics.mean(validation)<=.1 and median>0 and mae<=.5*median
    require((table['validation']['status']=='passed')==verdict,'calibration acceptance differs')
    require(math.isclose(mae,table['validation']['MAE_s'],abs_tol=1e-12),'validation MAE differs')
    weighted=[read_json(run/'weighted-gate'/(label+'.json')) for label in
        ('A_early_cold','B_late_cold','C_pressure','A_preserved','B_recomputed')]
    replica=PredictiveCache(24,block_size=128,block_bytes=33554432)
    for row in weighted:
        now=row['lookup_ns']/1e9
        for owner,features in row['policy_decision']['distribution_audit']['observed_idle_features'].items():
            require(owner in replica.idle,'unobserved owner');replica.idle[owner]=(deepcopy(features),now)
        keys=block_keys(row['tokens'][:((len(row['tokens'])-1)//128)*128],manifest['compatibility'],128)
        independent_sweeps(replica,row['policy_decision'],row['owner'],keys)
        check_actions(replica,row,manifest['compatibility'],128,now)
    decision=weighted[2]['policy_decision']
    require(decision['same_state_diagnostic']['point_victim_set_differs'],'synthetic weighted gate did not alter action')
    require(all(v['live_owners']==['B'] for v in decision['evicted']),'wrong weighted victims')
    require(weighted[3]['hit']==1280 and weighted[4]['hit']<1280,'native preservation/recompute differs')
    host=list((run/'calibration_fit').glob('host-*.json'))+list((run/'calibration_validation').glob('host-*.json'))
    require(len(host)==18,'missing calibration host/residency evidence')
    for path in host:
        row=read_json(path)
        require(row['residency']['model']['nonresident_pages']==row['residency']['parent']['nonresident_pages']==0,'unresident calibration files')
    result={'status':'passed','experiment_status':manifest['status'],'model_generations':len(seen),'phase_generations':phase_counts,
        'native_physical_token_requests_checked':len(seen),'calibration_samples_checked':len(samples),
        'independent_fit_validation':table['validation'],'synthetic_weighted_native_gate':'passed',
        'sample_parent_disk_read_bytes':sum(r['parent_read_bytes'] for r in samples),
        'sample_parent_major_faults':sum(r['parent_major_faults'] for r in samples),
        'natural_JCT_executed':False,'cumulative_known_generations':plan['prior_known_generations']+len(seen),
        'raw_sha256':evidence,'audit_source_sha256':sha256(__file__)}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='raw_sha256'},indent=2))


if __name__=='__main__':main()
