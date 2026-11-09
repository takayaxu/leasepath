"""Bounded native policy gate and independent prefill-cost calibration, no JCT."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import random
import resource
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.cache_policy_models import TransferredPredictor
from leasepath.controlled_resources import host_sample
from leasepath.distribution_cache_backend import DistributionEngine
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import read_json,sha256
from leasepath.prefill_cost_calibration import fit_table
from leasepath.qwen_data_ledger import GenerationBudget
from leasepath.resident_runtime import ResidentFiles
from leasepath.distribution_model_residency_v2 import DistributionModelKeeper,reuse_verified_model
from probe_qwen_cache_matrix import fixture,run_gate,write


def io():
    return {k:int(v) for k,v in (r.split(':') for r in Path('/proc/self/io').read_text().splitlines())}


def gpu():
    return {'cards':subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.used,clocks.sm,temperature.gpu','--format=csv,noheader,nounits'],text=True),
        'tenants':subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,used_memory','--format=csv,noheader,nounits'],text=True)}


class SyntheticCrossingPredictor:
    """Arithmetic fixture only; never presented as learned prediction evidence."""
    def predict(self,features):
        masses=(.2,.6,0.) if features['tool_name']=='compute' else (.2,0.,.6)
        fu=0.;rows=[]
        for edge,mass in zip((3.,10.,30.),masses):
            s=1-fu;fu+=mass
            rows.append({'right_s':edge,'supported':True,'global_risk_workflows':10,
                'hU':mass/s,'hD':0.,'h0':1-mass/s,'F_U':fu,'F_D':0.,'survival':1-fu})
        return rows


def weighted_gate(engine,tokenizer,budget,output):
    output.mkdir()
    engine.policy='distribution_value';engine.predictor=SyntheticCrossingPredictor();engine.clear()
    def generate(label,owner,prompt):
        reservation=budget.reserve('probe',scope='synthetic_crossing_real_native',label=label)
        engine.owner=owner
        result=engine.generate(prompt,limit=64)
        write(output/(label+'.json'),result | {'label':label,'reservation_id':reservation['id'],'prompt':prompt})
        if result['reason']!='eog' or result['text'].strip()!='READY':raise ValueError('weighted gate protocol failure')
        return result
    a,b,c=(fixture(tokenizer,label,blocks) for label,blocks in [('a',10),('b',10),('c',5)])
    first=generate('A_early_cold','A',a)
    second=generate('B_late_cold','B',b)
    if second['policy_decision']['evicted']:raise ValueError('fixture does not fit two initial prefixes')
    for owner,tool in [('A','compute'),('B','inspect_input')]:
        features=deepcopy(engine.cache.idle[owner][0]);features.update(tool_name=tool,tool_elapsed_s=0.,phase='waiting_tool')
        engine.observe(owner,features)
    admission=generate('C_pressure','C',c)
    decision=admission['policy_decision'];audit=decision['same_state_diagnostic']
    if not decision['evicted'] or not audit['point_victim_set_differs'] or not all(r['live_owners']==['B'] for r in decision['evicted']):
        raise ValueError('new rank did not drive the intended native victim')
    hot=generate('A_preserved','A',a)
    recompute=generate('B_recomputed','B',b)
    if hot['hit']!=(len(first['tokens'])-1)//128*128 or recompute['hit']>=(len(second['tokens'])-1)//128*128:
        raise ValueError('physical preservation/recompute differs')
    if hot['output_tokens']!=first['output_tokens'] or recompute['output_tokens']!=second['output_tokens']:
        raise ValueError('weighted gate changed deterministic output')
    result={'status':'passed','generations':5,'synthetic_prediction_fixture':True,
        'real_native_different_victims':len(decision['evicted']),'preserved_A_hit_tokens':hot['hit'],
        'B_recomputed_tokens':recompute['prefill'],'natural_JCT_claim':False}
    write(output/'summary.json',result);engine.clear();return result


def trim(engine,keep):
    keys=engine.cache.bindings['calibration'];removed=[]
    for key in reversed(keys[keep:]):
        if engine.cache.blocks[key].active:raise ValueError('cannot trim active calibration KV')
        slot=engine.slots.pop(key);removed.append(slot)
        engine.expected.pop(slot);del engine.cache.blocks[key]
    if removed:engine._read(engine.lib.lp_qm_drop(engine.ptr,json.dumps(removed).encode()))
    engine.cache.check();snapshot=engine.inspect()
    if snapshot['used_cells']!=keep*128:raise ValueError('calibration trim does not match exact physical hit')
    return {'keep_blocks':keep,'removed_slots':removed,'physical_after':snapshot}


def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,default=ROOT/'configs/prediction/qwen_distribution_v1_resident_v2_2026-11-09.json')
    p.add_argument('--preparation',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();plan=read_json(args.plan)
    if os.environ.get('LEASEPATH_TRACE_FILES'):raise ValueError('no ptrace during calibration')
    args.output.mkdir(parents=True,exist_ok=False);write(args.output/'frozen-plan.json',plan)
    identity=read_json(args.preparation/'native-identity.json')
    library=ROOT/'.artifacts/qwen-cache-stage2/native/backend.so'
    model=Path('/home/liuxv/local-models/qwen25-coder-32b-leasepath/persistent-f16/Qwen2.5-Coder-32B-Instruct-F16.gguf')
    tokenizer_dir=Path('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf')
    predictor_path=ROOT/'results/prediction/qwen50_holdout_2026-11-08/gbdt-model.json'
    sources=[args.plan,Path(__file__),ROOT/'scripts/probe_qwen_cache_matrix.py',library,
        tokenizer_dir/'tokenizer.json',tokenizer_dir/'tokenizer_config.json',predictor_path,*sorted((ROOT/'src/leasepath').glob('*.py'))]
    hashes={str(path.resolve()):sha256(path) for path in sources}
    for path in sources:
        if path.is_relative_to(ROOT):
            dest=args.output/'frozen-source'/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    write(args.output/'native-identity.json',identity)
    budgets={name:GenerationBudget(args.output/name/'ledger.jsonl',limits=limits) for name,limits in plan['phases'].items()}
    manifest={'status':'preparing','source_sha256':hashes,'started_wall_ns':time.time_ns(),'host_before':host_sample(),
              'scope':'native_correctness_and_prefill_calibration_only','pid':os.getpid()}
    engine=keeper=guard=None;rows=[]
    try:
        for path,digest in identity['linked_runtime_libraries'].items():
            if sha256(path)!=digest:raise ValueError('runtime identity changed')
        if sha256(library)!=identity['adapter_sha256']:raise ValueError('native adapter differs')
        print('rechecking prior full-hash provenance and model file identity',flush=True)
        write(args.output/'model-verification-reuse.json',reuse_verified_model(plan,model))
        model_stat=model.stat();manifest['model_identity']={k:getattr(model_stat,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}
        print('pinning verified model pages; bounded startup',plan['model_residency_startup_timeout_s'],'s',flush=True)
        keeper=DistributionModelKeeper(model,startup_timeout_s=plan['model_residency_startup_timeout_s']);write(args.output/'model-residency.json',keeper.initial)
        os.sched_setaffinity(0,plan['model_cpus'])
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(str(tokenizer_dir),local_files_only=True)
        compatibility=hashlib.sha256(json.dumps({'stage':plan['stage'],'model':plan['model_sha256'],'adapter':sha256(library),
            'tokenizer':sha256(tokenizer_dir/'tokenizer.json'),'geometry':[24,128],'context':16384,'kv':'float16'},sort_keys=True).encode()).hexdigest()
        raw=GBDTRiskPredictor.load(predictor_path)
        predictor=TransferredPredictor(raw,runtime_compatibility=compatibility,training_compatibility=raw.support.compatibilities[0])
        manifest['compatibility']=compatibility;write(args.output/'run-manifest.json',manifest)
        print('loading native model on GPU 5/6/7',flush=True);began=time.monotonic()
        engine=DistributionEngine(library,model,tokenizer,capacity=24,context=16384,block_size=128,headroom=2048,
            policy='retired_first_lru',predictor=predictor,compatibility=compatibility,lock_path=args.preparation/'engine.lock')
        manifest['model_load_s']=time.monotonic()-began;manifest['gpu_preflight']=engine.resource_preflight
        uuids={r['uuid'] for r in engine.resource_preflight}
        def gpu_check():
            sample=gpu()
            for line in sample['tenants'].splitlines():
                parts=[x.strip() for x in line.split(',')]
                if len(parts)>1 and parts[0] in uuids and parts[1]!=str(os.getpid()):raise RuntimeError('new GPU tenant')
            return sample
        mapped=set(sources)
        for line in Path('/proc/self/maps').read_text().splitlines():
            parts=line.split(None,5)
            if len(parts)==6 and parts[5].startswith('/'):
                path=Path(parts[5]).resolve()
                if path.is_file() and path!=model.resolve():mapped.add(path)
        guard=ResidentFiles(mapped);write(args.output/'pinned-parent-files.json',guard.records)
        backend={'model_sha256':plan['model_sha256'],'adapter_sha256':sha256(library),
            'tokenizer_sha256':sha256(tokenizer_dir/'tokenizer.json'),'runtime_libraries':identity['linked_runtime_libraries'],
            'GPU_UUIDs':sorted(uuids),'cuda_visible_devices':os.environ['CUDA_VISIBLE_DEVICES'],
            'model_cpus':sorted(os.sched_getaffinity(0)),'native_threads':8,'batch_tokens':512,
            'capacity_blocks':24,'block_tokens':128,'context':16384,'headroom':2048,'kv':'float16'}
        backend_identity=hashlib.sha256(json.dumps(backend,sort_keys=True).encode()).hexdigest()
        write(args.output/'backend-identity.json',{'sha256':backend_identity,'configuration':backend})
        manifest['status']='running';write(args.output/'run-manifest.json',manifest)
        gate=run_gate(engine,tokenizer,budgets['native'],args.output/'native-gate')
        crossing=weighted_gate(engine,tokenizer,budgets['native'],args.output/'weighted-gate')
        engine.policy='retired_first_lru';engine.predictor=None;engine.audit_shadows=False;engine.clear()
        rng=random.Random(plan['calibration']['seed'])
        schedule=[]
        for repeat in range(3):
            prefixes=list(plan['prefix_blocks']);rng.shuffle(prefixes)
            for blocks in prefixes:
                hits=list(range(blocks+1));rng.shuffle(hits)
                schedule.append({'repeat':repeat,'prefix_blocks':blocks,'hit_blocks':hits,'split':'fit' if repeat<2 else 'validation'})
        write(args.output/'calibration-schedule.json',schedule)
        for group in schedule:
            phase='calibration_fit' if group['split']=='fit' else 'calibration_validation';budget=budgets[phase]
            blocks=group['prefix_blocks'];prefix=blocks*128
            # Content is deliberately generic and separate from task answers.
            prompt=' value'*(prefix+1)
            if len(tokenizer.encode(prompt,add_special_tokens=False))!=prefix+1:raise ValueError('calibration prompt exact length failed')
            engine.clear();engine.owner='calibration'
            reservation=budget.reserve('probe',scope='calibration_prime',repeat=group['repeat'],prefix_tokens=prefix)
            prime=engine.generate(prompt,limit=1)
            write(args.output/phase/f"prime-{group['repeat']}-{blocks}.json",prime | {'reservation_id':reservation['id'],'prompt':prompt})
            resident={'model':keeper.verify(),'parent':guard.verify()};gpu_before=gpu_check();host_before=host_sample()
            counters=io();usage=resource.getrusage(resource.RUSAGE_SELF)
            for hit_blocks in group['hit_blocks']:
                trimming=trim(engine,hit_blocks)
                reservation=budget.reserve('pilot',scope='calibration_measurement',split=group['split'],repeat=group['repeat'],prefix_tokens=prefix,hit_tokens=hit_blocks*128)
                start_io=io();start_usage=resource.getrusage(resource.RUSAGE_SELF)
                request=engine.generate(prompt,limit=1)
                end_usage=resource.getrusage(resource.RUSAGE_SELF);end_io=io()
                if request['hit']!=hit_blocks*128 or request['prefill']!=prefix+1-hit_blocks*128 or request['reason']!='length':
                    raise ValueError('calibration exact native token/work mismatch')
                path=args.output/phase/f"sample-{group['repeat']}-{blocks}-{hit_blocks}.json"
                measurement={'repeat':group['repeat'],'split':group['split'],'prefix_tokens':prefix,'hit_tokens':hit_blocks*128,
                    'prefill_s':request['prefill_ns']/1e9,'reservation_id':reservation['id'],'evidence':str(path.relative_to(args.output)),
                    'parent_read_bytes':end_io['read_bytes']-start_io['read_bytes'],
                    'parent_major_faults':end_usage.ru_majflt-start_usage.ru_majflt}
                write(path,request | {'calibration':measurement,'reservation_id':reservation['id'],'prompt':prompt,'trim':trimming})
                rows.append(measurement)
            counters_after=io();usage_after=resource.getrusage(resource.RUSAGE_SELF)
            write(args.output/phase/f"host-{group['repeat']}-{blocks}.json",{'before':host_before,'after':host_sample(),
                'gpu_before':gpu_before,'gpu_after':gpu_check(),'residency':resident,
                'parent_io_delta':{k:counters_after[k]-counters[k] for k in counters},
                'parent_major_faults':usage_after.ru_majflt-usage.ru_majflt})
            write(args.output/'calibration-progress.json',{'samples':len(rows),'rows':rows})
            print('calibration',group['split'],'repeat',group['repeat'],'prefix',prefix,'samples',len(rows),flush=True)
        table=fit_table(rows,backend_identity)
        table['provenance']={'plan_sha256':sha256(args.plan),'raw_measurements':len(rows),
            'fit_samples':sum(r['split']=='fit' for r in rows),'validation_samples':sum(r['split']=='validation' for r in rows)}
        write(args.output/'cost-table.json',table)
        write(args.output/'calibration-measurements.json',rows)
        for path,digest in hashes.items():
            if sha256(path)!=digest:raise ValueError('frozen source changed: '+path)
        if any(getattr(model.stat(),k)!=v for k,v in manifest['model_identity'].items()):raise ValueError('model identity changed')
        gpu_check();keeper.verify();guard.verify()
        summary={'status':'completed','native_gate':gate,'weighted_gate':crossing,'calibration':table['validation'],
            'generations':sum(b.summary()['generations'] for b in budgets.values()),'natural_JCT_executed':False,
            'next_gate':'parent I/O attribution and prospectively frozen natural-JCT gate before timing policies'}
        write(args.output/'summary.json',summary);manifest['status']='completed'
        print(json.dumps(summary['calibration']),flush=True)
    except BaseException as exc:
        manifest.update(status='failed',error=type(exc).__name__+': '+str(exc));write(args.output/'failure.json',manifest);raise
    finally:
        if engine:engine.close()
        if guard:guard.close()
        if keeper:keeper.close()
        for name,budget in budgets.items():
            write(budget.path.with_suffix('.sealed.json'),{'sealed':True,'sha256':sha256(budget.path) if budget.path.exists() else None,
                'budget':budget.summary(),'reason':manifest['status']})
        manifest.update(finished_wall_ns=time.time_ns(),budgets={k:b.summary() for k,b in budgets.items()},host_after=host_sample())
        write(args.output/'run-manifest.json',manifest)


if __name__=='__main__':main()
