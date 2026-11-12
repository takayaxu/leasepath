"""Observed-prefix episode tracking and bounded causal model updates.

No future outcome file is read. Model updates occur between requests without
clearing the cache. CPU work is bounded to one background training thread.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from .continuous_predictor_v1 import ContinuousGBDT,ContinuousEmpirical,training_data
from .frozen_distribution_baseline import file_digest
from .prediction_training import check_distribution


class EpisodeTracker:
    def __init__(self):
        self.latest={};self.pending=[];self.observations=[];self.excluded=[];self.episode_events={}

    def request(self,row):
        owner=row['owner'];at=row['lookup_ns']/1e9;keep=[]
        for snapshot in self.pending:
            if snapshot['owner']!=owner:keep.append(snapshot);continue
            count=snapshot['features']['prefix_tokens'];prefix=snapshot.pop('_prefix')
            if row['tokens'][:count]!=prefix:
                self.excluded.append({**snapshot,'reason':'next_lookup_changes_observed_prefix'});continue
            observed={**snapshot,'terminal_s':at,'remaining_s':at-snapshot['snapshot_s'],'event':'U'}
            if observed['remaining_s']<=0:raise ValueError('nonpositive observed demand residual')
            self.observations.append(observed);self.episode_events[snapshot['episode_id']]='U'
        self.pending=keep
        audit=row['policy_decision']['distribution_audit'];snapshot_at=audit['t0_s']
        for idle_owner,features in audit['observed_idle_features'].items():
            previous=self.latest.get(idle_owner)
            if previous is None:raise ValueError('snapshot without actual previous release')
            count=features['prefix_tokens']
            if count!=(len(previous['tokens'])-1)//128*128:raise ValueError('snapshot prefix geometry differs')
            self.pending.append(dict(sample_id=row['request_id']+':'+idle_owner,
                episode_id=idle_owner+':'+previous['request_id'],owner=idle_owner,
                snapshot_s=snapshot_at,features=deepcopy(features),_prefix=previous['tokens'][:count]))
        self.latest[owner]=row

    def retire(self,owner,at):
        keep=[]
        for snapshot in self.pending:
            if snapshot['owner']!=owner:keep.append(snapshot);continue
            row={k:v for k,v in snapshot.items() if k!='_prefix'}
            self.observations.append({**row,'terminal_s':at,'remaining_s':at-row['snapshot_s'],'event':'D'})
            self.episode_events[row['episode_id']]='D'
        self.pending=keep;self.latest.pop(owner,None)

    def as_of(self,cutoff_s):
        observed=[deepcopy(row) for row in self.observations if row['terminal_s']<=cutoff_s]
        for snapshot in self.pending:
            if snapshot['snapshot_s']>=cutoff_s:continue
            row={k:deepcopy(v) for k,v in snapshot.items() if k!='_prefix'}
            observed.append({**row,'terminal_s':cutoff_s,'remaining_s':cutoff_s-row['snapshot_s'],'event':'C'})
        return observed


class UpdatingPredictor:
    def __init__(self,model,*,runtime_domain,training_domain,kind,output,seed_observations,
                 update_episodes=100,minimum_projects=5,interval_s=600,dynamic=True):
        self.model=model;self.runtime=runtime_domain;self.domain=training_domain;self.kind=kind
        self.output=Path(output);self.output.mkdir(parents=True,exist_ok=False)
        self.seed=deepcopy(seed_observations);self.update_episodes=update_episodes
        self.minimum_projects=minimum_projects;self.interval=interval_s;self.dynamic=dynamic
        self.tracker=EpisodeTracker();self.pool=ThreadPoolExecutor(max_workers=1)
        self.future=None;self.last_started=time.monotonic();self.deployed=0;self.last_episodes=0
        self._event('initial_deployment',predictor_kind=kind,dynamic=dynamic)

    def _event(self,kind,**data):
        with (self.output/'training_timeline.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(kind=kind,observed_ns=time.monotonic_ns(),model_version=self.deployed,**data),allow_nan=False)+'\n')

    def predict(self,features):
        if features['compatibility_id']!=self.runtime:raise ValueError('runtime domain mismatch')
        return self.model.predict({**features,'compatibility_id':self.domain})

    def request_completed(self,row):
        self.tracker.request(row)

    def retired(self,owner):
        self.tracker.retire(owner,time.monotonic())

    def boundary(self):
        if self.future is not None and self.future.done():
            try:
                candidate,path,proof=self.future.result()
                self.model=candidate;self.deployed+=1
                self._event('deployed',model_sha256=file_digest(path),**proof)
            except Exception as exc:self._event('training_failed',error=type(exc).__name__+': '+str(exc))
            self.future=None
        if not self.dynamic or self.future is not None:return
        count=len(self.tracker.episode_events);now=time.monotonic()
        if count-self.last_episodes<self.update_episodes and now-self.last_started<self.interval:return
        observations=self.tracker.as_of(now)
        matured=[r for r in observations if r['event'] in ('U','D')]
        owners={r['owner'] for r in matured}
        if len(owners)<self.minimum_projects or count-self.last_episodes<self.update_episodes:
            self._event('update_skipped',matured_episodes=count,new_episodes=count-self.last_episodes,projects=len(owners))
            self.last_started=now;return
        training=self.seed+observations
        samples,targets=training_data(training,domain=self.domain,cutoff_s=now)
        proof=dict(cutoff_s=now,projects=len({s['run_id'] for s in samples}),samples=len(samples),
                   matured_live_episodes=count,source_sha256=hashlib.sha256(json.dumps(training,sort_keys=True).encode()).hexdigest())
        self._event('training_started',**proof)
        self.last_started=now;self.last_episodes=count
        number=self.deployed+1
        def train():
            started=time.monotonic();cpu=time.thread_time()
            model=(ContinuousGBDT() if self.kind=='gbdt' else ContinuousEmpirical()).fit(samples,targets)
            for sample in samples[:24]:check_distribution(model.predict(sample['features']))
            path=self.output/f'model-{number:03d}.json';model.save(path)
            return model,path,{**proof,'training_wall_s':time.monotonic()-started,'training_thread_cpu_s':time.thread_time()-cpu}
        self.future=self.pool.submit(train)

    def close(self):
        self.pool.shutdown(wait=True)
        # A candidate completed only after service has ended is not a deployed
        # update. Record that distinction rather than claiming online use.
        if self.future is not None:
            try:
                _,path,proof=self.future.result();self._event('completed_after_service_not_deployed',model_sha256=file_digest(path),**proof)
            except Exception as exc:self._event('training_failed',error=str(exc))
        self._event('service_end',deployed_updates=self.deployed)
