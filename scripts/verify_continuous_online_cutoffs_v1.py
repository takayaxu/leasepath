from pathlib import Path
import json,hashlib,sys
r=Path('/home/liuxv/LeasePath-new-server-data');sys.path.insert(0,str(r/'src'))
from leasepath.continuous_online_v2 import EpisodeTracker
root=r/'results/agent/continuous_workflow_v6_2026-11-11';proof=[]
for arm in ['empirical_distribution','gbdt_dynamic_point','gbdt_dynamic_distribution']:
 p=json.loads((root/'e2e'/arm/'plan.json').read_text());w=Path(p['work']);seed=json.loads(Path(p['seed_observations']).read_text());events=[]
 for line in (w/'raw/backend.jsonl').read_text().splitlines():
  e=json.loads(line)
  if e['kind']=='engine_request':events.append(e)
 for line in (w/'raw/controller.jsonl').read_text().splitlines():
  e=json.loads(line)
  if e['kind']=='context_retire':events.append(e)
 events.sort(key=lambda e:e['ts_ns'])
 timeline=[json.loads(x) for x in (w/'training/training_timeline.jsonl').read_text().splitlines()]
 for training in [x for x in timeline if x['kind']=='training_started']:
  cutoff=training['cutoff_s'];tracker=EpisodeTracker()
  for e in events:
   if e['ts_ns']/1e9>cutoff:break
   d=e['data']
   if e['kind']=='engine_request':tracker.request({**d,'request_id':d['tracker_request_id']})
   else:tracker.retire(d['owner'],e['ts_ns']/1e9)
  source=seed+tracker.as_of(cutoff)
  digest=hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()
  assert digest==training['source_sha256'],(arm,cutoff,digest,training['source_sha256'])
  deployed=next(x for x in timeline if x['kind']=='deployed' and x['cutoff_s']==cutoff)
  model=w/'training'/('model-%03d.json'%deployed['model_version']);assert hashlib.sha256(model.read_bytes()).hexdigest()==deployed['model_sha256']
  for e in events:
   if e['kind']=='engine_request':
    d=e['data']
    if d['predictor_version']==deployed['model_version']:assert d['lookup_ns']>=deployed['observed_ns']
  proof.append(dict(arm=arm,version=deployed['model_version'],cutoff_s=cutoff,source_sha256=digest,source_reconstructed_from_observed_prefix=True,samples=training['samples'],matured_live_episodes=training['matured_live_episodes'],model_sha_verified=True,used_only_after_deployment=True,training_wall_s=deployed['training_wall_s'],training_cpu_s=deployed['training_thread_cpu_s']))
(root/'online-cutoff-replay.json').write_text(json.dumps(dict(status='passed',updates=proof),indent=2)+'\n');print(json.dumps(dict(status='passed',updates=len(proof))))
