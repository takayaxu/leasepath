"""Same natural-only JCT protocol; reuse verified prepared tmpfs inputs before measurement."""
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
from probe_qwen_resident_tree_tools import tasks_and_refs,run_group
from leasepath.shared_experiment_residency import LockedFiles,LockedInputs,LockedRuntime
from run_qwen_cache_controlled import ScopedBudget
from probe_qwen_cache_matrix import run_gate,write
from qwen_cache_resident_tree_batch import run_batch
from leasepath.resident_runtime import ResidentFiles,ResidentRuntime,ModelResidentKeeper


def process_io():
    return {k:int(v) for k,v in (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}


def worker_violation(call):
    usage=call.get('child_lifetime')
    io=call.get('file_diagnostics',{}).get('io_since_exec',{})
    overall=call['worker_timing']['overall']
    return (not usage or usage['major_faults'] or usage['block_input_ops'] or io.get('read_bytes',-1) or overall['major_faults'] or overall['read_bytes'])


def gpu_sample():
    cards=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.used,clocks.sm,clocks.mem,temperature.gpu,power.draw',
                                   '--format=csv,noheader,nounits'],text=True)
    tenants=subprocess.check_output(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,used_memory',
                                     '--format=csv,noheader,nounits'],text=True)
    return {'cards':cards,'tenants':tenants}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--plan',type=Path,default=ROOT/'configs/prediction/qwen_resident_tree_natural_2026-11-09.json')
    p.add_argument('--preparation',type=Path,required=True)
    p.add_argument('--runtime-copy',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--input-preparation',type=Path,required=True)
    args=p.parse_args();plan=read_json(args.plan)
    if os.environ.get('LEASEPATH_TRACE_FILES'):raise ValueError('ptrace must be disabled during JCT')
    runtime_manifest=read_json(args.runtime_copy)
    if sys.prefix!=runtime_manifest['dst'] or Path(sys.executable).resolve()!=Path(runtime_manifest['interpreter']).resolve():raise ValueError('private interpreter/prefix required')
    system_path=args.preparation/'system-residency.json';system=read_json(system_path)
    for row in system['files']:
        if sha256(row['path'])!=row['sha256']:raise ValueError('system file differs: '+row['path'])
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
    sources=[args.plan,Path(__file__).resolve(),ROOT/'scripts/qwen_cache_resident_tree_batch.py',
             ROOT/'scripts/probe_qwen_resident_tree_tools.py',ROOT/'scripts/probe_qwen_controlled_tools.py',
             ROOT/'scripts/run_qwen_cache_controlled.py',ROOT/'scripts/probe_qwen_cache_matrix.py',
             ROOT/'scripts/check_qwen_resident_tree_natural.py',ROOT/'scripts/summarize_qwen_resident_natural.py',
             ROOT/'scripts/prepare_qwen_resident_runtime.py',ROOT/'scripts/diagnose_qwen_resident_files.py',system_path,library,
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
    for file in (ROOT/'src/leasepath').glob('*.py'):
        copied=Path(runtime_manifest['dst'])/'lib/python3.13/site-packages/leasepath'/file.name
        if sha256(file)!=sha256(copied):raise ValueError('runtime source differs: '+str(file))
    manifest={'status':'preparing','source_sha256':hashes,'runtime_libraries_sha256':identity['linked_runtime_libraries'],
        'model_copy':copy,'started_wall_ns':time.time_ns(),'prior_noise_gates_passed':False,
        'scope':plan['design'],'system_residency':system,'private_interpreter':sys.executable,'private_prefix':sys.prefix,'runtime_manifest_sha256':sha256(args.runtime_copy),'mlock_limit_bytes':resource.getrlimit(resource.RLIMIT_MEMLOCK)}
    engine=None;inputs=None;locked_runtime=None;locked_files=None;model_keeper=None;loaded_files=None;budgets={};phases=[]
    try:
        tasks,refs=tasks_and_refs(plan)
        input_provenance=[]
        for task in tasks:
            source=args.input_preparation/(task['id']+'.npz')
            if not source.resolve().is_relative_to(Path('/dev/shm')) or sha256(source)!=task['input_sha256']:raise ValueError('prepared input differs')
            input_provenance.append({'task':task['id'],'original':task['file'],'prepared':str(source),'sha256':task['input_sha256']})
            task['file']=str(source)
        write(args.output/'preloaded-input-provenance.json',input_provenance)
        locked_runtime=ResidentRuntime(runtime_manifest)
        inputs=LockedInputs(tasks,args.output/'inputs');write(args.output/'input-copies.json',inputs.records)
        model_keeper=ModelResidentKeeper(model)
        write(args.output/'model-keeper.json',model_keeper.initial)
        locked_files=ResidentFiles([*sources,*map(Path,identity['linked_runtime_libraries']),*[Path(r['path']) for r in system['files']]])
        write(args.output/'locked-files.json',locked_files.records)
        write(args.output/'residency-before-load.json',locked_files.verify())
        allowed_inodes={(Path(r['path']).stat().st_dev,Path(r['path']).stat().st_ino) for r in locked_files.records}
        allowed_inodes.update((Path(runtime_manifest['dst'],r['relative']).stat().st_dev,Path(runtime_manifest['dst'],r['relative']).stat().st_ino) for r in runtime_manifest['files'])
        def validate_maps(call):
            for line in call['file_diagnostics']['maps'].splitlines():
                parts=line.split(None,5)
                if len(parts)<6 or not parts[5].startswith('/'):continue
                path=Path(parts[5]);st=path.stat()
                if (st.st_dev,st.st_ino) not in allowed_inodes:raise RuntimeError('unlocked mapped dependency: '+str(path))
            for opened in call['file_diagnostics']['opened']:
                name=opened['path']
                if name.startswith(('/proc/','/sys/','/dev/')):continue
                path=Path(name)
                if path.is_file():
                    st=path.stat()
                    if (st.st_dev,st.st_ino) not in allowed_inodes:raise RuntimeError('unlocked opened dependency: '+str(path))
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
        # Freeze and pin all actual parent mappings (CUDA driver, predictor, tokenizer)
        # before the first native generation. No privileged operations are needed.
        mapped=set();special_maps=[]
        for line in Path('/proc/self/maps').read_text().splitlines():
            parts=line.split(None,5)
            if len(parts)<6 or not parts[5].startswith('/'):continue
            path=Path(parts[5]).resolve()
            if not path.is_file():
                special_maps.append(parts[5]);continue
            st=path.stat()
            if path==model.resolve() or (st.st_dev,st.st_ino) in allowed_inodes:continue
            mapped.add(path)
        manifest['parent_special_mappings']=sorted(set(special_maps))
        loaded_files=ResidentFiles(mapped)
        manifest['post_load_libraries_sha256']={str(path):sha256(path) for path in sorted(mapped)}
        write(args.output/'post-load-locked-files.json',loaded_files.records)
        write(args.output/'post-load-residency.json',loaded_files.verify())
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
                if worker_violation(call):
                    raise RuntimeError('sentinel numerical worker read disk or major faulted')
                validate_maps(call)
            return group
        sentinel(args.output/'after-load-tool-sentinel')
        for spec in plan['phases']:
            directory=args.output/spec['name'];budget=budgets[spec['name']];batches=[]
            for repeat,order in enumerate(spec['orders']):
                sentinel(directory/f'sentinel-{repeat}')
                for name in order:
                    residency={'model':model_keeper.verify(),'protected':locked_files.verify(),'runtime':locked_runtime.verify(),'post_load':loaded_files.verify()}
                    check_sources();budget.scope={'phase':spec['name'],'capacity':24,'concurrency':3,'repeat':repeat,'policy':name}
                    engine.clear();engine.policy='predicted_reuse' if name in models else name;engine.predictor=models.get(name);engine.clear()
                    before=host_sample();gpu_before=gpu_sample();validate_gpu(gpu_before);io_before=process_io()
                    result=run_batch(engine,tokenizer,budget,tasks,refs,directory/f'concurrency-3-repeat-{repeat}-{name}',
                        name,check_sources,plan['tool_controls'],1,
                        plan['response_windows'] if spec['name']=='fixed_window' else None)
                    io_after=process_io();gpu_after=gpu_sample();validate_gpu(gpu_after);after=host_sample()
                    result={'phase':spec['name'],'capacity':24,'concurrency':3,'repeat':repeat,**result}
                    write(directory/f'host-repeat-{repeat}-{name}.json',{'before':before,'after':after,
                        'gpu_before':gpu_before,'gpu_after':gpu_after,'residency':residency,'parent_io_delta':{k:io_after[k]-io_before[k] for k in io_before}})
                    batches.append(result);write(directory/'progress.json',{'results':batches,'completed_batches':len(batches),'budget':budget.summary()})
                    if result['status']!='passed':raise RuntimeError('strict quality/window failure; all evidence retained')
                    for path in (directory/f'concurrency-3-repeat-{repeat}-{name}').glob('*-quality.json'):
                        for call in read_json(path)['tools']:
                            if call['status']=='ok' and call['name']=='compute':
                                o=call['worker_timing']['overall']
                                if worker_violation(call):raise RuntimeError('numerical worker read disk or major faulted')
                                validate_maps(call)
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
        for guard in (loaded_files,locked_files,inputs,locked_runtime,model_keeper):
            if guard:guard.close()
        for name,budget in budgets.items():
            write(budget.path.with_suffix('.sealed.json'),{'sealed':True,'sha256':sha256(budget.path) if budget.path.exists() else None,
                  'budget':budget.summary(),'reason':manifest['status']})
        manifest.update(finished_wall_ns=time.time_ns(),budget_at_end={n:b.summary() for n,b in budgets.items()},host_after=host_sample())
        write(args.output/'run-manifest.json',manifest)


if __name__=='__main__':main()
