"""Bounded local pipeline, preserves every attempt and stops at quality gates.

Wait for v3, validate the registered v5 root-cause repair, audit/fit, then one
development cycle of five real E2E arms. This is not the 60-project expansion
or the final five-repeat matrix. No automatic retries or holdout executions.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import file_digest


def read(path):return json.loads(Path(path).read_text())
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n');temp.replace(path)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);args=parser.parse_args()
    config=read(args.plan);root=args.plan.parent
    if (root/'supervisor-state.json').exists():raise ValueError('supervisor attempt exists')
    started=time.monotonic();deadline=started+config['overall_wall_budget_s']
    state=dict(status='waiting_for_v3_collection',pid=os.getpid(),holdout_model_tasks=0,stages=[])
    def save():
        state.update(updated_wall_ns=time.time_ns(),elapsed_s=time.monotonic()-started);write(root/'supervisor-state.json',state)
    def sources():
        for name,digest in config['source_sha256'].items():
            if file_digest(name)!=digest:raise ValueError('pipeline source changed: '+name)
    runtime=config['runtime_prefix'];env=os.environ.copy();env.update(PYTHONHOME=runtime,
        PYTHONPATH=f'{runtime}/lib/python3.13/site-packages:{ROOT}/.artifacts/prediction-holdout-deps:{ROOT}/src',PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='5,6,7',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',TOKENIZERS_PARALLELISM='false',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1')
    python=runtime+'/bin/python3.13'
    def command(arguments,log,timeout):
        sources()
        with Path(log).open('x') as stream:
            child=subprocess.Popen([python,'-u',*map(str,arguments)],cwd=ROOT,env=env,stdout=stream,stderr=stream,start_new_session=True)
        state['active_child_pid']=child.pid;state['active_command']=list(map(str,arguments));save()
        try:returncode=child.wait(timeout=min(timeout,max(1,deadline-time.monotonic())))
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(child.pid,signal.SIGTERM)
            try:child.wait(timeout=30)
            except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
            raise TimeoutError('registered pipeline child exceeded budget')
        state.pop('active_child_pid',None);save()
        if returncode:raise RuntimeError('pipeline command failed; inspect '+str(log))
    def archive(plan):
        work=Path(plan['work']);evidence=Path(plan['evidence']);destination=evidence/'raw'
        if destination.exists():raise ValueError('raw archive already exists')
        shutil.copytree(work,destination)
        files={str(p.relative_to(work)):file_digest(p) for p in work.rglob('*') if p.is_file()}
        assert all(file_digest(destination/name)==digest for name,digest in files.items())
        write(evidence/'raw-sha256.json',dict(source=str(work),files=files,copy_verified=True))
    save()
    try:
        sources();prior=read(config['prior_plan']);prior_manifest=Path(prior['work'])/'run-manifest.json'
        while not prior_manifest.exists():
            if time.monotonic()>deadline:raise TimeoutError('waiting for prior collection exceeded pipeline budget')
            save();time.sleep(15)
        if config.get('prior_supervisor_state'):
            while read(config['prior_supervisor_state'])['status'] not in ('stopped_requires_review','completed_development_cycle'):
                if time.monotonic()>deadline:raise TimeoutError('prior postprocessing exceeded budget')
                save();time.sleep(15)
        state['stages'].append(dict(name='v3_collection',state=read(prior_manifest)['status'],original_failures_preserved=True))
        v3=read(config['repair_plan']);state['status']='running_v3_repair_collection';save()
        command([ROOT/'scripts/run_continuous_pilot_v6.py','--plan',config['repair_plan']],Path(v3['evidence'])/'run.log',v3['overall_wall_budget_s']+60)
        state['status']='auditing_v3';save()
        command([ROOT/'scripts/audit_continuous_pilot_v6.py','--plan',config['repair_plan'],'--output',Path(v3['evidence'])/'audit'],Path(v3['evidence'])/'audit.log',1800)
        archive(v3)
        result=read(Path(v3['work'])/'summary.json')
        state['stages'].append(dict(name='v6_repair_collection',summary={k:result[k] for k in ('status','successful_rounds','observed_rounds','completed_projects','model_generations','tool_calls','max_prompt_tokens')}))
        rounds=[read(p) for p in (Path(v3['work'])/'rounds').glob('*.json')]
        per_project={owner:sum(r['success'] for r in rounds if r['owner']==owner)/8 for owner in {r['owner'] for r in rounds}}
        if result['successful_rounds']/48<.9 or len(per_project)!=6 or min(per_project.values())<.8:
            raise RuntimeError('capability gate not met; preserve failures and diagnose before training/E2E')
        audit=read(Path(v3['evidence'])/'audit/summary.json')
        successful_keys={(r['owner'],r['round']) for r in rounds if r['success']}
        unresolved=[error for error in audit['tool_errors'] if (error['owner'],error['round']) not in successful_keys]
        write(Path(v3['evidence'])/'tool-error-resolution.json',dict(all_errors=audit['tool_errors'],unresolved=unresolved,rule='Error feedback consumed and same user turn independently accepted; preserve all actual tool errors, not a blanket zero-error requirement'))
        if unresolved:raise RuntimeError('unresolved tool errors; root-cause repair required before E2E')
        state['status']='training_target_predictors';save()
        models=root/'prediction'
        command([ROOT/'scripts/train_continuous_predictors_v1.py','--audit',Path(v3['evidence'])/'audit','--output',models],root/'training.log',1200)
        learned=read(models/'summary.json');state['stages'].append(dict(name='initial_training',snapshots=learned['snapshots'],episodes=learned['episodes'],projects=learned['projects']))
        capacity=max(config['minimum_e2e_capacity'],((result['max_prompt_tokens']-1)//128)+8)
        if capacity>224:raise RuntimeError('single request exceeds registered E2E capacity ceiling; new geometry required')
        for arm in config['e2e_arms']:
            sources();evidence=root/'e2e'/arm['name'];evidence.mkdir(parents=True,exist_ok=False)
            p=copy.deepcopy(v3);p.update(stage='continuous_e2e_development_v4',evidence=str(evidence),work=str(Path('/dev/shm')/('leasepath-continuous-e2e-v4-'+arm['name']+'-20261111')),
                capacity_blocks=capacity,arm=arm['name'],predictor_kind=arm['predictor'],dynamic=arm['dynamic'],policy_arm=arm['policy'],
                initial_model=str(models/(arm['predictor']+'-initial.json')),training_domain=learned['training_domain'],
                seed_observations=str(models/'seed-observations.json'),seed_observations_sha256=file_digest(models/'seed-observations.json'),
                scope='one registered development cycle, not final balanced repeats',natural_JCT_acceptance=False)
            p['initial_model_sha256']=file_digest(p['initial_model']);p['source_sha256']=config['source_sha256']
            write(evidence/'plan.json',p)
            state.update(status='running_e2e',current_arm=arm['name']);save()
            command([ROOT/'scripts/run_continuous_e2e_v4.py','--plan',evidence/'plan.json'],evidence/'run.log',p['overall_wall_budget_s']+60)
            command([ROOT/'scripts/audit_continuous_e2e_v4.py','--plan',evidence/'plan.json','--output',evidence/'audit'],evidence/'audit.log',1800)
            archive(p);summary=read(Path(p['work'])/'summary.json')
            state['stages'].append(dict(name='e2e_'+arm['name'],status=summary['status'],successful_rounds=summary['successful_rounds'],observed_rounds=summary['observed_rounds'],updates=summary['deployed_updates']))
            command([ROOT/'scripts/summarize_continuous_e2e_v1.py','--pipeline',root],evidence/'summary.log',60)
            if summary['status']!='passed':raise RuntimeError('E2E quality failure; preserve whole arm, diagnose before further timing arms')
        state.update(status='completed_development_cycle',final_scope='six authored projects and one E2E cycle; 60-project expansion and balanced repeats remain')
    except BaseException as exc:
        state.update(status='stopped_requires_review',error=type(exc).__name__+': '+str(exc));save();raise
    finally:save()


if __name__=='__main__':main()
