"""Real fixed-response mechanism test plus natural paired JCT on the shared host."""
import argparse
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.cache_matrix import AuditedEngine
from leasepath.cache_policy_models import TransferredPredictor
from leasepath.controlled_resources import host_sample
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import read_json,sha256
from leasepath.reuse_predictor import EmpiricalRiskPredictor
from probe_qwen_controlled_v2_tools import tasks_and_refs,run_group
from leasepath.shared_experiment_residency import LockedFiles,LockedInputs,LockedRuntime
from run_qwen_cache_controlled import ScopedBudget
from probe_qwen_cache_matrix import run_gate,write
from qwen_cache_shared_paired_batch import run_batch


def process_io():
    return {k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}


def gpu_sample():
    cards=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.used,clocks.sm,clocks.mem,temperature.gpu,power.draw',
                                   '--format=csv,noheader,nounits'],text=True)
    tenants=subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,used_memory',
                                     '--format=csv,noheader,nounits'],text=True)
    return {'cards':cards,'tenants':tenants}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--plan',type=Path,default=ROOT/'configs/prediction/qwen_shared_server_paired_2026-11-09.json')
    p.add_argument('--preparation',type=Path,required=True)
    p.add_argument('--runtime-copy',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();plan=read_json(args.plan)
    if not args.output.resolve().is_relative_to(Path('/dev/shm')):raise ValueError('private tmpfs output required')
    args.output.mkdir(parents=True,exist_ok=False);write(args.output/'frozen-plan.json',plan)
    copy=read_json(args.preparation/'model-copy.json');identity=read_json(args.preparation/'native-identity.json')
    model=Path(copy['dst'])
    if copy['sha256']!=plan['model_sha256'] or model.stat().st_size!=copy['bytes']:raise ValueError('model unverified')
    model_stat=tuple(getattr(model.stat(),k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))
    tokenizer_dir=Path('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf')
    library=ROOT/'.artifacts/qwen-cache-stage2/native/backend.so'
    if sha256(library)!=identity['adapter_sha256']:raise ValueError('adapter differs')
    prediction=ROOT/plan['prediction_result']
    sources=[args.plan,Path(__file__).resolve(),ROOT/'scripts/qwen_cache_shared_paired_batch.py',
             ROOT/'scripts/probe_qwen_controlled_v2_tools.py',ROOT/'scripts/probe_qwen_controlled_tools.py',
             ROOT/'scripts/run_qwen_cache_controlled.py',ROOT/'scripts/probe_qwen_cache_matrix.py',
             ROOT/'scripts/check_qwen_shared_server_paired.py',library,
             tokenizer_dir/'tokenizer.json',tokenizer_dir/'tokenizer_config.json',
             prediction/'conditional-model.json',prediction/'gbdt-model.json',args.runtime_copy]
    sources+=list((ROOT/'src/leasepath').glob('*.py'))
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        sources.extend(ROOT/'.artifacts'/name/file for file in ('tasks.private.json','references.private.json'))
    hashes={str(path.resolve()):sha256(path) for path in sources}
    for path in sources:
        if path.is_relative_to(ROOT):
            dest=args.output/'frozen-source'/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    def check_sources():
        for path,digest in hashes.items():
            if sha256(path)!=digest:raise ValueError('source changed: '+path)
        if tuple(getattr(model.stat(),k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))!=model_stat:
            raise ValueError('verified model stat changed')
    def check_runtime():
        for path,digest in identity['linked_runtime_libraries'].items():
            if sha256(path)!=digest:raise ValueError('linked runtime changed')
    check_runtime()
    manifest={'status':'preparing','source_sha256':hashes,'runtime_libraries_sha256':identity['linked_runtime_libraries'],
        'model_copy':copy,'started_wall_ns':time.time_ns(),'prior_noise_gates_passed':False,
        'scope':plan['design'],'mlock_limit_bytes':resource.getrlimit(resource.RLIMIT_MEMLOCK)}
    engine=None;inputs=None;locked_runtime=None;locked_files=None;budgets={};phases=[]
    try:
        tasks,refs=tasks_and_refs(plan)
        locked_runtime=LockedRuntime(read_json(args.runtime_copy))
        inputs=LockedInputs(tasks,args.output/'inputs');write(args.output/'input-copies.json',inputs.records)
        locked_files=LockedFiles([model,*sources,*map(Path,identity['linked_runtime_libraries'])])
        write(args.output/'locked-files.json',locked_files.records)
        manifest['locked_status_before_load']=Path('/proc/self/status').read_text()
        os.sched_setaffinity(0,plan['model_cpus'])
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(str(tokenizer_dir),local_files_only=True)
        compatibility=hashlib.sha256(json.dumps({'model':copy['sha256'],'adapter':sha256(library),
            'tokenizer':sha256(tokenizer_dir/'tokenizer.json'),'context':16384,'kv':'float16',
            'geometry':[24,128],'stage':plan['stage']},sort_keys=True).encode()).hexdigest()
        manifest['runtime_compatibility']=compatibility
        training=read_json(prediction/'conditional-model.json')['compatibilities'][0]
        raw={'conditional':EmpiricalRiskPredictor.load(prediction/'conditional-model.json'),
             'gbdt':GBDTRiskPredictor.load(prediction/'gbdt-model.json')}
        models={n:TransferredPredictor(m,runtime_compatibility=compatibility,training_compatibility=training) for n,m in raw.items()}
        manifest['host_before_load']=host_sample();start=time.monotonic()
        engine=AuditedEngine(library,str(model),tokenizer,capacity=24,context=16384,block_size=128,headroom=2048,
                            compatibility=compatibility,lock_path=args.preparation/'engine.lock')
        manifest['model_load_s']=time.monotonic()-start;manifest['device_preflight']=engine.resource_preflight
        selected_uuids={row['uuid'] for row in engine.resource_preflight}
        def validate_gpu(sample):
            for line in sample['tenants'].splitlines():
                parts=[x.strip() for x in line.split(',')]
                if len(parts)>=2 and parts[0] in selected_uuids and parts[1]!=str(os.getpid()):
                    raise RuntimeError('another GPU tenant appeared')
        manifest['status']='running';write(args.output/'run-manifest.json',manifest)
        for spec in plan['phases']:
            directory=args.output/spec['name'];directory.mkdir()
            budgets[spec['name']]=ScopedBudget(directory/'ledger.jsonl',limits=spec['budget_limits'])
            write(directory/'frozen-phase.json',spec)
        gate=run_gate(engine,tokenizer,budgets[plan['phases'][0]['name']],args.output/'native-gate')
        def sentinel(output):
            group=run_group(tasks,refs,plan,output)
            for row in group['rows']:
                call=row['call']
                if not row['correct'] or call['status']!='ok':
                    raise RuntimeError('real sentinel reference or tool failure')
                overall=call['worker_timing']['overall']
                if overall['read_bytes'] or overall['major_faults']:
                    raise RuntimeError('sentinel numerical worker read disk or major faulted')
            return group
        sentinel(args.output/'after-load-tool-sentinel')
        for spec in plan['phases']:
            directory=args.output/spec['name'];budget=budgets[spec['name']];batches=[]
            for repeat,order in enumerate(spec['orders']):
                sentinel(directory/f'sentinel-{repeat}')
                for name in order:
                    check_sources();budget.scope={'phase':spec['name'],'capacity':24,'concurrency':3,'repeat':repeat,'policy':name}
                    engine.clear();engine.policy='predicted_reuse' if name in models else name;engine.predictor=models.get(name);engine.clear()
                    before=host_sample();gpu_before=gpu_sample();validate_gpu(gpu_before);io_before=process_io()
                    result=run_batch(engine,tokenizer,budget,tasks,refs,directory/f'concurrency-3-repeat-{repeat}-{name}',
                        name,check_sources,plan['tool_controls'],1,
                        plan['response_windows'] if spec['name']=='fixed_window' else None)
                    io_after=process_io();gpu_after=gpu_sample();validate_gpu(gpu_after);after=host_sample()
                    result={'phase':spec['name'],'capacity':24,'concurrency':3,'repeat':repeat,**result}
                    write(directory/f'host-repeat-{repeat}-{name}.json',{'before':before,'after':after,
                        'gpu_before':gpu_before,'gpu_after':gpu_after,'parent_io_delta':{k:io_after[k]-io_before[k] for k in io_before}})
                    batches.append(result);write(directory/'progress.json',{'results':batches,'completed_batches':len(batches),'budget':budget.summary()})
                    if result['status']!='passed':raise RuntimeError('strict quality/window failure; all evidence retained')
                    for path in (directory/f'concurrency-3-repeat-{repeat}-{name}').glob('*-quality.json'):
                        for call in read_json(path)['tools']:
                            if call['status']=='ok' and call['name']=='compute':
                                o=call['worker_timing']['overall']
                                if o['read_bytes'] or o['major_faults']:raise RuntimeError('numerical worker read disk or major faulted')
                                if any(p['num_threads']!=1 for p in call['thread_diagnostics']['pools_after']):raise RuntimeError('numerical pool not single-threaded')
            phase={'name':spec['name'],'status':'completed','interpretation':spec['interpretation'],'batches':batches,'budget':budget.summary()}
            phases.append(phase);write(directory/'summary.json',phase)
        check_sources();check_runtime()
        write(args.output/'summary.json',{'status':'completed','native_gate':gate,'phases':phases,
            'new_model_generations':sum(b.summary()['generations'] for b in budgets.values()),
            'no_natural_acceleration_claim_without_independent_paired_audit':True})
        manifest['status']='completed'
    except BaseException as exc:
        manifest.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        write(args.output/'failure.json',manifest);raise
    finally:
        if engine:engine.close()
        for guard in (locked_files,inputs,locked_runtime):
            if guard:guard.close()
        for name,budget in budgets.items():
            write(budget.path.with_suffix('.sealed.json'),{'sealed':True,'sha256':sha256(budget.path) if budget.path.exists() else None,
                  'budget':budget.summary(),'reason':manifest['status']})
        manifest.update(finished_wall_ns=time.time_ns(),budget_at_end={n:b.summary() for n,b in budgets.items()},host_after=host_sample())
        write(args.output/'run-manifest.json',manifest)


if __name__=='__main__':main()
