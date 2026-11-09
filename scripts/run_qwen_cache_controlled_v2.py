"""Targeted real KV policy replication; hard tool/noise gates precede live JCT."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.cache_matrix import AuditedEngine
from leasepath.cache_policy_models import TransferredPredictor
from leasepath.controlled_resources import host_sample
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import read_json,sha256
from leasepath.qwen_data_ledger import GenerationBudget
from leasepath.reuse_predictor import EmpiricalRiskPredictor
from probe_qwen_controlled_v2_tools import LockedInputs,LockedRuntime,tasks_and_refs,run_group,qualify
from probe_qwen_cache_matrix import run_gate,write
from qwen_cache_controlled_v2_batch import run_batch


class ScopedBudget(GenerationBudget):
    scope={}
    def reserve(self,lane,**metadata):
        return super().reserve(lane,**self.scope,**metadata)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--plan',type=Path,default=ROOT/'configs/prediction/qwen_cache_controlled_v2_2026-11-09.json')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--preparation',type=Path,required=True)
    p.add_argument('--tool-preflight',type=Path,required=True)
    args=p.parse_args()
    if not args.output.resolve().is_relative_to(Path('/dev/shm')):
        raise ValueError('controlled outputs must use private tmpfs')
    plan=read_json(args.plan)
    preflight=read_json(args.tool_preflight/'summary.json')
    if preflight['status']!='passed': raise ValueError('frozen CPU tool gate did not pass; no live JCT run allowed')
    prior_plan=read_json(args.tool_preflight/'frozen-plan.json')
    if {k:v for k,v in prior_plan.items() if k!='source_sha256'}!=plan:
        raise ValueError('preflight used different frozen plan')
    for name,digest in prior_plan['source_sha256'].items():
        if sha256(name)!=digest: raise ValueError('tool preflight source changed')
    copy=read_json(args.preparation/'model-copy.json')
    identity=read_json(args.preparation/'native-identity.json')
    if copy['sha256']!=plan['model_sha256'] or Path(copy['dst']).stat().st_size!=copy['bytes']:
        raise ValueError('model copy unverified')
    args.output.mkdir(parents=True,exist_ok=False)
    write(args.output/'frozen-plan.json',plan)
    write(args.output/'model-copy.json',copy)
    library=ROOT/'.artifacts/qwen-cache-stage2/native/backend.so'
    if sha256(library)!=identity['adapter_sha256']: raise ValueError('adapter differs')
    runtime=read_json(args.preparation/'runtime-copy.json')
    locked_runtime=LockedRuntime(runtime)
    tasks,refs=tasks_and_refs(plan)
    inputs=LockedInputs(tasks,args.output/'inputs')
    write(args.output/'input-copies.json',inputs.records)
    sources=[args.plan,library,Path(__file__).resolve(),ROOT/'scripts/qwen_cache_controlled_v2_batch.py',
             ROOT/'scripts/probe_qwen_controlled_v2_tools.py',ROOT/'scripts/probe_qwen_cache_matrix.py']
    sources+=list((ROOT/'src/leasepath').glob('*.py'))
    prediction=ROOT/plan['prediction_result']
    sources+=[prediction/'conditional-model.json',prediction/'gbdt-model.json']
    hashes={str(path):sha256(path) for path in sources}
    for path in sources:
        if path.is_relative_to(ROOT):
            dest=args.output/'frozen-source'/path.relative_to(ROOT)
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,dest)
    def check_sources():
        for name,digest in hashes.items():
            if sha256(name)!=digest: raise ValueError('protected source changed: '+name)
    def check_runtime():
        for name,digest in identity['linked_runtime_libraries'].items():
            if sha256(name)!=digest: raise ValueError('linked runtime changed')
    check_runtime()
    from transformers import AutoTokenizer
    tokenizer_dir=Path('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf')
    tokenizer=AutoTokenizer.from_pretrained(str(tokenizer_dir),local_files_only=True)
    compatibility=hashlib.sha256(json.dumps({'model':copy['sha256'],'library':sha256(library),
        'tokenizer':sha256(tokenizer_dir/'tokenizer.json'),'context':16384,'geometry':[24,128],
        'kv':'float16','experiment':plan['stage']},sort_keys=True).encode()).hexdigest()
    training=read_json(prediction/'conditional-model.json')['compatibilities'][0]
    raw={'conditional':EmpiricalRiskPredictor.load(prediction/'conditional-model.json'),
         'gbdt':GBDTRiskPredictor.load(prediction/'gbdt-model.json')}
    models={name:TransferredPredictor(model,runtime_compatibility=compatibility,training_compatibility=training)
            for name,model in raw.items()}
    budget=ScopedBudget(args.output/'ledger.jsonl',limits=plan['budget_limits'])
    manifest={'status':'started','source_sha256':hashes,'runtime_compatibility':compatibility,
              'runtime_libraries_sha256':identity['linked_runtime_libraries'],
              'started_wall_ns':time.time_ns(),'scope':plan['scope'],'tool_preflight':str(args.tool_preflight),
              'statistical_transfer':'frozen F_U(3s) guard mapped only after native gate; no retraining or horizon tuning',
              'host_before_load':host_sample()}
    engine=None
    batches=[]
    sentinels=[]
    try:
        os.sched_setaffinity(0,plan['model_cpus'])
        start=time.monotonic()
        engine=AuditedEngine(library,copy['dst'],tokenizer,capacity=24,context=16384,block_size=128,
            headroom=2048,compatibility=compatibility,lock_path=args.preparation/'engine.lock')
        manifest['model_load_s']=time.monotonic()-start
        manifest['device_preflight']=engine.resource_preflight
        gate=run_gate(engine,tokenizer,budget,args.output/'native-gate')
        for repeat in range(3):
            sentinels.append(run_group(tasks,refs,plan,args.output/f'after-load-{repeat}'))
        loaded_gate=qualify(sentinels,plan)
        write(args.output/'after-load-gate.json',loaded_gate)
        if loaded_gate['status']!='passed': raise RuntimeError('post-load tool noise gate failed')
        for repeat,order in enumerate(plan['orders']):
            cycle_sentinels=[run_group(tasks,refs,plan,args.output/f'cycle-{repeat}-before')]
            for name in order:
                check_sources()
                budget.scope={'capacity':24,'concurrency':3,'repeat':repeat,'policy':name}
                engine.clear()
                engine.policy='predicted_reuse' if name in models else name
                engine.predictor=models.get(name)
                engine.clear()
                before=host_sample()
                result=run_batch(engine,tokenizer,budget,tasks,refs,
                    args.output/f'concurrency-3-repeat-{repeat}-{name}',name,check_sources,plan['tool_controls'],plan['tool_threads'])
                after=host_sample()
                result={'capacity':24,'concurrency':3,'repeat':repeat,**result}
                write(args.output/f'host-repeat-{repeat}-{name}.json',{'before':before,'after':after})
                batches.append(result)
                write(args.output/'progress.json',{'results':batches,'completed_batches':len(batches),'budget':budget.summary()})
                if result['status']!='passed': raise RuntimeError('strict quality failure; stopped without rerun')
            cycle_sentinels.append(run_group(tasks,refs,plan,args.output/f'cycle-{repeat}-after'))
            sentinels.extend(cycle_sentinels)
            cycle_gate=qualify(cycle_sentinels,plan)
            write(args.output/f'cycle-{repeat}-sentinel-gate.json',cycle_gate)
            if cycle_gate['status']!='passed': raise RuntimeError('cycle sentinel noise gate failed')
        check_sources()
        check_runtime()
        summary={'status':'passed','batches':batches,'native_gate':gate,'budget':budget.summary(),
                 'sentinel_qualification':qualify(sentinels,plan),
                 'no_acceleration_claim_until_independent_resource_and_protocol_audit':True}
        write(args.output/'summary.json',summary)
        manifest['status']='passed'
    except BaseException as exc:
        manifest.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        write(args.output/'failure.json',manifest)
        raise
    finally:
        if engine:engine.close()
        inputs.close();locked_runtime.close()
        manifest.update(finished_wall_ns=time.time_ns(),budget_at_end=budget.summary(),host_after=host_sample())
        write(args.output/'run-manifest.json',manifest)
        ledger=args.output/'ledger.jsonl'
        write(ledger.with_suffix('.sealed.json'),{'sealed':True,'sha256':sha256(ledger) if ledger.exists() else None,
                                               'budget':budget.summary(),'reason':manifest['status']})


if __name__=='__main__':main()
