"""One registered development E2E arm; real continuing Agent and physical KV.

Uses the audited v2 controller. Learned predictors are separate model versions
under the exact accepted equal-cost score; the historical baseline is intact.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from leasepath.continuous_predictor_v1 import ContinuousGBDT,ContinuousEmpirical
from leasepath.continuous_online_v1 import UpdatingPredictor
from leasepath.distribution_cache import DistributionCache
from leasepath.preexec_pressure_v2 import PressureEngine,check_topology
from leasepath.cache_policy_models import TransferredPredictor
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline,file_digest,FIXED
from leasepath.controlled_resources import host_sample
from run_continuous_pilot_v7 import run_batch,write,identity
from run_qwen_cache_controlled import ScopedBudget
from probe_qwen_cache_matrix import run_gate


class LearnedFactory:
    def __init__(self,frozen,predictor):self.frozen=frozen;self.live=predictor
    def verify(self):self.frozen.verify()
    def predictor(self,runtime):
        assert runtime==self.live.runtime
        return self.live
    def cache(self,capacity,*,runtime_compatibility,arm='gbdt_distribution'):
        policy={'retired_first_lru':'retired_first_lru','gbdt_point':'predicted_reuse','gbdt_distribution':'distribution_value'}[arm]
        # Live timing has no shadow predictors: LRU must not incur prediction
        # work. Same-state diagnostics are produced by read-only replay later.
        return DistributionCache(capacity,block_size=FIXED['block_tokens'],block_bytes=FIXED['block_bytes'],policy=policy,
            predictor=self.predictor(runtime_compatibility),horizon_s=FIXED['horizon_s'],tau_s=FIXED['tau_s'],
            min_workflows=FIXED['min_global_workflows'],cost_table=None,audit_shadows=False)


class OnlineEngine(PressureEngine):
    observing=False
    def generate(self,*args,**kwargs):
        live=self.baseline.live
        if self.observing:live.boundary()
        version=live.deployed
        result=super().generate(*args,**kwargs)
        result.update(predictor_version=version,predictor_kind=live.kind)
        if self.observing:
            # Agent request_id is attached by PressureObservedQwen only after
            # generate returns. Native lookup time is already authoritative.
            tracker_id='native-'+str(result['lookup_ns'])
            result['tracker_request_id']=tracker_id
            live.request_completed({**result,'request_id':tracker_id})
        return result
    def retire(self,owner):
        if self.observing:self.baseline.live.retired(owner)
        return super().retire(owner)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);args=parser.parse_args()
    p=json.loads(args.plan.read_text());ev=Path(p['evidence']);work=Path(p['work'])
    if work.exists() or (ev/'state.json').exists():raise ValueError('arm attempt exists; preserve outcomes')
    work.mkdir(parents=True);write(work/'frozen-plan.json',p)
    state=dict(status='preparing',pid=os.getpid(),arm=p['arm'],holdout_model_tasks=0,completed_rounds=0,successful_rounds=0)
    def save(records=None):
        if records is not None:state.update(completed_rounds=len(records),successful_rounds=sum(r['success'] for r in records))
        state['updated_wall_ns']=time.time_ns();write(ev/'state.json',state)
    def stop(sig,frame):raise InterruptedError('bounded E2E arm interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGALRM):signal.signal(sig,stop)
    signal.alarm(p['overall_wall_budget_s']);engine=None;live=None;save()
    try:
        if sys.prefix!=p['runtime_prefix']:raise ValueError('wrong runtime')
        for name,digest in p['source_sha256'].items():
            if file_digest(name)!=digest:raise ValueError('registered source changed: '+name)
        if identity(p['model']['path'])!=p['model']['identity']:raise ValueError('model inode changed')
        receipt=json.loads(Path(p['model_receipt']).read_text())
        if file_digest(p['model_receipt'])!=p['model_receipt_sha256'] or receipt['sha256']!=p['model']['sha256']:raise ValueError('model receipt changed')
        native=json.loads(Path(p['native_identity']).read_text())
        if file_digest(p['library'])!=native['adapter_sha256']:raise ValueError('native adapter changed')
        for name,digest in native['linked_runtime_libraries'].items():
            if file_digest(name)!=digest:raise ValueError('native dependency changed')
        preparation=json.loads(Path(p['runtime_manifest']).read_text())
        for row in preparation['files']:
            if file_digest(Path(preparation['dst'])/row['relative'])!=row['sha256']:raise ValueError('runtime changed')
        state['topology']=check_topology(p);write(work/'host-before.json',host_sample())
        if host_sample()['meminfo_kib']['MemAvailable']<p['minimum_available_gib']*1024**2:raise ValueError('memory admission failed')
        os.sched_setaffinity(0,p['model_cpus'])
        from transformers import AutoTokenizer
        tokenizer=AutoTokenizer.from_pretrained(p['tokenizer'],local_files_only=True)
        compatibility=hashlib.sha256(json.dumps(dict(model=p['model']['sha256'],native=native['adapter_sha256'],capacity=p['capacity_blocks'],context=p['context_tokens'],stage='continuous-e2e-v3'),sort_keys=True).encode()).hexdigest()
        kind=p['predictor_kind'];model_path=Path(p['initial_model'])
        if file_digest(model_path)!=p['initial_model_sha256']:raise ValueError('initial predictor changed')
        model=(ContinuousGBDT if kind=='gbdt' else ContinuousEmpirical).load(model_path)
        seed=json.loads(Path(p['seed_observations']).read_text())
        if file_digest(p['seed_observations'])!=p['seed_observations_sha256']:raise ValueError('seed observations changed')
        live=UpdatingPredictor(model,runtime_domain=compatibility,training_domain=p['training_domain'],kind=kind,
            output=work/'training',seed_observations=seed,dynamic=p['dynamic'],update_episodes=100,minimum_projects=5,interval_s=600)
        frozen=FrozenEqualCostBaseline(ROOT,ROOT/p['baseline_manifest']);factory=LearnedFactory(frozen,live)
        state.update(status='loading_model',runtime_compatibility=compatibility);save();start=time.monotonic()
        engine=OnlineEngine(p['library'],p['model']['path'],tokenizer,capacity=p['capacity_blocks'],context=p['context_tokens'],headroom=2048,baseline=factory,compatibility=compatibility,lock_path=Path(p['engine_lock']))
        state.update(model_load_s=time.monotonic()-start,device_preflight=engine.resource_preflight,status='native_gate');save()
        probe=ScopedBudget(work/'ledgers/probe.jsonl',limits={'probe':13,'pilot':1,'total':13})
        gate=run_gate(engine,tokenizer,probe,work/'native-gate')
        write(probe.path.with_suffix('.sealed.json'),dict(sealed=True,sha256=file_digest(probe.path),summary=probe.summary()))
        engine.select_arm(p['policy_arm']);engine.observing=True
        state.update(status='running_e2e_arm',native_gate=gate['status']);save()
        summary=run_batch(engine,tokenizer,p,work,save)
        # The reused controller's generic policy label describes its collection
        # ancestry. Actual online decisions are checked separately below.
        actual_policy={'retired_first_lru':'retired_first_lru','gbdt_point':'predicted_reuse','gbdt_distribution':'distribution_value'}[p['policy_arm']]
        assert all(r['policy_decision']['distribution_audit']['policy']==actual_policy for r in engine.history)
        summary.update(policy=actual_policy,arm=p['arm'],dynamic=p['dynamic'],predictor_kind=kind,
                       deployed_updates=live.deployed,scope='single development E2E arm; paired summary and independent audit required')
        write(work/'summary.json',summary)
        write(work/'online-observations.json',dict(rows=live.tracker.as_of(time.monotonic()),excluded=live.tracker.excluded))
        for name,digest in p['source_sha256'].items():
            if file_digest(name)!=digest:raise ValueError('source changed during arm')
        state.update(status='completed_e2e_arm',summary_status=summary['status']);save()
    except BaseException as exc:
        state.update(status='stopped',error=type(exc).__name__+': '+str(exc));save();raise
    finally:
        signal.alarm(0)
        if live is not None:live.close()
        if engine is not None:engine.close()
        state['finished_wall_ns']=time.time_ns();save();write(work/'run-manifest.json',state);write(work/'host-after.json',host_sample())


if __name__=='__main__':main()
