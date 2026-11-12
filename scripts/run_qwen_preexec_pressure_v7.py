"""Retained source paths and parent I/O attribution; frozen batch/worker/policy semantics."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.preexec_pressure_v2 import PressureEngine,run_tool_group as run_group
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline
from pressure_disk_model_v1 import SharedDiskKeeper, verify_disk_model, admit_disk_model
from leasepath.controlled_resources import host_sample
from leasepath.prediction_training import read_json,sha256
from probe_qwen_resident_tree_tools import tasks_and_refs
from leasepath.shared_experiment_residency import LockedInputs
from run_qwen_cache_controlled import ScopedBudget
from probe_qwen_cache_matrix import run_gate,write
from qwen_cache_preexec_batch_v2 import run_batch
from leasepath.resident_runtime import ResidentFiles,ResidentRuntime
from source_io_guard_v1 import SourcePathHandles,SourceIOAudit


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
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--preparation',type=Path,required=True)
    p.add_argument('--runtime-copy',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--input-preparation',type=Path,required=True)
    p.add_argument('--tool-preflight',type=Path,required=True)
    p.add_argument('--diagnostic',action='store_true')
    p.add_argument('--trace-run',type=Path)
    args=p.parse_args();plan=read_json(args.plan)
    tool_gate=read_json(args.tool_preflight/'summary.json')
    if tool_gate['status']!='passed' or tool_gate.get('concurrencies')!=[3,6,10]:raise ValueError('fresh complete CPU gate required; failed gates cannot be bypassed')
    tool_plan=read_json(args.tool_preflight/'frozen-config.json')
    for key in ('tool_controls','model_cpus','matrix','baseline_manifest','model_sha256'):
        if tool_plan[key]!=plan[key]:raise ValueError('CPU gate configuration differs')
    from audit_pressure_preexec_v4 import audit as audit_tools
    if not audit_tools(args.tool_preflight)['natural_launch_allowed']:raise ValueError('independent CPU audit rejects launch')
    disk_model=verify_disk_model(plan)
    memory_admission_before=admit_disk_model(plan, disk_model)
    capacity=plan['capacity_blocks'];concurrency=plan['concurrency']
    baseline=FrozenEqualCostBaseline(ROOT,ROOT/plan['baseline_manifest'])
    if capacity not in (24,32,64) or concurrency not in (3,6,10):raise ValueError('unregistered geometry')
    if len(plan['live_tasks'])!=concurrency:raise ValueError('concurrency/task mismatch')
    tracer=int(next(line.split(':')[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('TracerPid:')))
    if not args.diagnostic and (tracer or os.environ.get('LEASEPATH_TRACE_FILES')):raise ValueError('ptrace forbidden during natural JCT')
    if args.diagnostic and (not tracer or concurrency!=10):raise ValueError('diagnostic requires full ptrace and ten tasks')
    traced=None
    if not args.diagnostic:
        if args.trace_run is None:raise ValueError('fresh complete-controller trace required')
        traced=read_json(args.trace_run/'acceptance.json')
        if traced['status']!='passed' or not traced.get('diagnostic') or not traced.get('traces_sha256') or traced['capacity']!=capacity or traced['concurrency']!=10:raise ValueError('trace not accepted for this geometry')
        for path,digest in {**traced['source_sha256'],**traced['traces_sha256']}.items():
            if sha256(path)!=digest:raise ValueError('traced source differs')
        if traced['config_controls']!= {k:plan[k] for k in ('model_cpus','tool_controls','baseline_manifest','model_sha256')}:raise ValueError('traced controls differ')

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
    if model.resolve()!=Path(disk_model['model_path']).resolve():raise ValueError('preparation points to different staged model')
    model_stat=tuple(getattr(model.stat(),k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))
    tokenizer_dir=Path('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf')
    library=ROOT/'.artifacts/qwen-cache-stage2/native/backend.so'
    if sha256(library)!=identity['adapter_sha256']:raise ValueError('adapter differs')
    prediction=ROOT/plan['prediction_result']
    sources=[args.plan,Path(__file__).resolve(),ROOT/'scripts/qwen_cache_preexec_batch_v2.py',
             ROOT/'scripts/probe_qwen_resident_tree_tools.py',ROOT/'scripts/probe_qwen_controlled_tools.py',
             ROOT/'scripts/run_qwen_cache_controlled.py',ROOT/'scripts/probe_qwen_cache_matrix.py',
             ROOT/'scripts/check_qwen_preexec_pressure_v7.py',ROOT/'scripts/check_qwen_preexec_pressure_v6.py',ROOT/'scripts/source_io_guard_v1.py',ROOT/'scripts/audit_pressure_protocol_v3.py',ROOT/'scripts/audit_pressure_rejection_v1.py',ROOT/'scripts/pressure_disk_model_v1.py',ROOT/'scripts/pressure_disk_keeper_v1.py',ROOT/'scripts/stage_pressure_model_v1.py',ROOT/'scripts/resident_windowed_v1.py',Path(plan['disk_model']['plan']),Path(plan['disk_model']['ready']),ROOT/'scripts/audit_pressure_live_state.py',ROOT/'scripts/audit_pressure_preexec_v4.py',
             ROOT/'scripts/audit_kv_distribution_actions.py',ROOT/'scripts/audit_kv_distribution_cost_actions.py',ROOT/'scripts/summarize_qwen_resident_natural.py',
             ROOT/'scripts/prepare_qwen_resident_runtime.py',ROOT/'scripts/diagnose_qwen_resident_files.py',system_path,library,
             tokenizer_dir/'tokenizer.json',tokenizer_dir/'tokenizer_config.json',
             prediction/'conditional-model.json',prediction/'gbdt-model.json',args.runtime_copy,ROOT/plan['baseline_manifest']]
    sources+=[Path(plan['launcher']),ROOT/'native/leasepath_preexec_v1.c',ROOT/'scripts/preflight_pressure_preexec_v2.py']
    sources+=list((ROOT/'src/leasepath').glob('*.py'))
    sources+=list((ROOT/'scripts/__pycache__').glob('*.pyc'))
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        sources.extend(ROOT/'.artifacts'/name/file for file in ('tasks.private.json','references.private.json'))
    if traced:
        for row in traced['file_closure']:
            if sha256(row['path'])!=row['sha256']:raise ValueError('native traced file differs')
            sources.append(Path(row['path']))
        sources.append(args.trace_run/'acceptance.json')
    hashes={str(path.resolve()):sha256(path) for path in sources}
    for path in sources:
        if path.is_relative_to(ROOT):
            dest=args.output/'frozen-source'/path.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,dest)
    source_io=SourceIOAudit()
    def check_sources():
        for path,digest in hashes.items():
            if source_io.digest(path)!=digest:raise ValueError('source changed: '+path)
        if tuple(getattr(model.stat(),k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns'))!=model_stat:
            raise ValueError('verified model stat changed')
    def check_runtime():
        for path,digest in identity['linked_runtime_libraries'].items():
            if sha256(path)!=digest:raise ValueError('linked runtime changed')
    check_runtime()
    for file in (ROOT/'src/leasepath').glob('*.py'):
        copied=Path(runtime_manifest['dst'])/'lib/python3.13/site-packages/leasepath'/file.name
        if sha256(file)!=sha256(copied):raise ValueError('runtime source differs: '+str(file))
    manifest={'status':'preparing','tracer_pid':tracer,'diagnostic_trace':args.diagnostic,'capacity':capacity,'concurrency':concurrency,'source_sha256':hashes,'runtime_libraries_sha256':identity['linked_runtime_libraries'],
        'model_copy':copy,'disk_model_verification':disk_model,'memory_admission_before':memory_admission_before,'started_wall_ns':time.time_ns(),'prior_noise_gates_passed':False,
        'scope':plan['cpu_recipe'],'system_residency':system,'private_interpreter':sys.executable,'private_prefix':sys.prefix,'runtime_manifest_sha256':sha256(args.runtime_copy),'mlock_limit_bytes':resource.getrlimit(resource.RLIMIT_MEMLOCK)}
    engine=None;inputs=None;locked_runtime=None;locked_files=None;model_keeper=None;loaded_files=None;source_handles=None;budgets={};phases=[]
    try:
        source_handles=SourcePathHandles([*hashes,model])
        write(args.output/'source-path-handles.json',source_handles.records)
        manifest['source_path_handles']=source_handles.verify()
        manifest['parent_io_recipe']='retained-source-paths-thread-attribution-v1'
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
        write(args.output/'model-verification-disk.json',disk_model)
        model_keeper=SharedDiskKeeper(plan)
        write(args.output/'model-keeper.json',model_keeper.initial)
        manifest['memory_admission_after_model_lock']=admit_disk_model(plan,disk_model)
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
            'geometry':[capacity,128],'stage':plan['stage']},sort_keys=True).encode()).hexdigest()
        manifest['runtime_compatibility']=compatibility
        manifest['baseline']=baseline.evidence()
        manifest['host_before_load']=host_sample();start=time.monotonic()
        engine=PressureEngine(library,str(model),tokenizer,capacity=capacity,context=16384,headroom=2048,
                            baseline=baseline,compatibility=compatibility,lock_path=args.preparation/'engine.lock')
        native_generate=engine.generate
        def measured_generate(*a,**kw):
            return source_io.measure('native_generate',native_generate,*a,**kw)
        engine.generate=measured_generate
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
        # Diagnostic gates have separate ledgers. Each natural policy batch has its own capped ledger.
        gate=None
        if args.diagnostic:
            probe=ScopedBudget(args.output/'probe-ledger.jsonl',limits={'probe':13,'pilot':1,'total':13})
            budgets['native-probe']=probe
            gate=run_gate(engine,tokenizer,probe,args.output/'native-gate')
        def sentinel(output):
            group=run_group(tasks,refs,plan,output)
            write(output/'summary.json',group)
            for row in group['rows']:
                call=row['call']
                if not row['correct'] or call['status']!='ok':
                    raise RuntimeError('real sentinel reference or tool failure')
                overall=call['worker_timing']['overall']
                if worker_violation(call):
                    raise RuntimeError('sentinel numerical worker read disk or major faulted')
                from preflight_pressure_preexec_v2 import verify_inheritance
                verify_inheritance(call,plan['tool_controls'][row['task']])
                validate_maps(call)
            return group
        sentinel(args.output/'after-load-tool-sentinel')
        batches=[]
        orders=[["gbdt_distribution"]] if args.diagnostic else plan['matrix']['orders']
        for repeat,order in enumerate(orders):
            sentinel(args.output/f'sentinel-{repeat}')
            for name in order:
                directory=args.output/f'cycle-{repeat}-{name}';directory.mkdir()
                budget=ScopedBudget(directory/'ledger.jsonl',limits={'probe':1,'pilot':concurrency*6,'total':concurrency*6})
                budgets[directory.name]=budget
                residency={'model':model_keeper.verify(),'protected':locked_files.verify(),'runtime':locked_runtime.verify(),'post_load':loaded_files.verify()}
                check_sources();baseline.verify();budget.scope={'capacity':capacity,'concurrency':concurrency,'repeat':repeat,'policy':name,'diagnostic':args.diagnostic}
                source_handles.verify()
                engine.select_arm(name)
                source_io.reset(directory.name)
                before=host_sample();gpu_before=gpu_sample();validate_gpu(gpu_before);io_before=process_io();usage_before=resource.getrusage(resource.RUSAGE_SELF)
                batch_dir=directory/'batch'
                result=run_batch(engine,tokenizer,budget,tasks,refs,batch_dir,name,check_sources,plan['tool_controls'],1)
                io_after=process_io();usage_after=resource.getrusage(resource.RUSAGE_SELF);gpu_after=gpu_sample();validate_gpu(gpu_after);after=host_sample()
                write(directory/'parent-source-io.json',source_io.snapshot())
                result={'capacity':capacity,'concurrency':concurrency,'repeat':repeat,**result}
                write(directory/'host.json',{'before':before,'after':after,'gpu_before':gpu_before,'gpu_after':gpu_after,'residency':residency,
                    'parent_io_delta':{k:io_after[k]-io_before[k] for k in io_before},'parent_major_faults':usage_after.ru_majflt-usage_before.ru_majflt,
                    'parent_block_input_ops':usage_after.ru_inblock-usage_before.ru_inblock})
                batches.append(result);write(args.output/'progress.json',{'results':batches,'completed_batches':len(batches)})
                if result['status']!='passed':raise RuntimeError('strict quality failure; no replacements')
                if io_after['read_bytes']!=io_before['read_bytes'] or usage_after.ru_majflt!=usage_before.ru_majflt or usage_after.ru_inblock!=usage_before.ru_inblock:
                    raise RuntimeError('parent disk read or major fault; prospective gate')
                for path in batch_dir.glob('*-quality.json'):
                    for call in read_json(path)['tools']:
                        if call['status']=='ok' and call['name']=='compute':
                            if worker_violation(call):raise RuntimeError('worker disk read or major fault')
                            from preflight_pressure_preexec_v2 import verify_inheritance
                            verify_inheritance(call,plan['tool_controls'][read_json(path)['task']])
                            validate_maps(call)
                            if any(p['num_threads']!=1 for p in call['thread_diagnostics']['pools_after']):raise RuntimeError('numerical pool not single-threaded')
        phases=[{'name':'diagnostic' if args.diagnostic else 'natural','batches':batches}]
        check_sources();check_runtime()
        write(args.output/'summary.json',{'status':'completed','native_gate':gate,'phases':phases,
            'new_model_generations':sum(b.summary()['generations'] for b in budgets.values()),
            'no_natural_acceleration_claim_without_independent_paired_audit':True})
        manifest['status']='completed';manifest['diagnostic_trace']=args.diagnostic;manifest['capacity']=capacity;manifest['concurrency']=concurrency
    except BaseException as exc:
        manifest.update(status='failed',error=f'{type(exc).__name__}: {exc}')
        write(args.output/'failure.json',manifest);raise
    finally:
        write(args.output/'parent-source-io-final.json',source_io.snapshot())
        if engine:engine.close()
        for guard in (loaded_files,locked_files,inputs,locked_runtime,model_keeper,source_handles):
            if guard:guard.close()
        for name,budget in budgets.items():
            write(budget.path.with_suffix('.sealed.json'),{'sealed':True,'sha256':sha256(budget.path) if budget.path.exists() else None,
                  'budget':budget.summary(),'reason':manifest['status']})
        manifest.update(finished_wall_ns=time.time_ns(),budget_at_end={n:b.summary() for n,b in budgets.items()},host_after=host_sample())
        write(args.output/'run-manifest.json',manifest)


if __name__=='__main__':main()
