from pathlib import Path
import json,collections,statistics,numpy as np
root=Path(__file__).resolve().parents[1];run=root/'.artifacts/qwen-cache-stage3/run-b';out=root/'results/cache_policy/qwen_stage3b_2026-11-08';rows=[]
for c in [24,32,64]:
 directory=run/f'capacity-{c}'
 if not directory.exists():continue
 summary=json.loads((directory/'progress.json').read_text());summary['batches']=summary['results']
 for b in summary['batches']:
  d=run/f'capacity-{c}'/f"concurrency-{b['concurrency']}-repeat-{b['repeat']}-{b['policy']}"
  controller=[json.loads(l) for l in (d/'raw/controller.jsonl').read_text().splitlines()];backend=[json.loads(l)['data'] for l in (d/'raw/backend.jsonl').read_text().splitlines() if json.loads(l)['kind']=='engine_request'];prev={}
  ends=collections.defaultdict(list)
  for e in controller:
   if e['kind']=='tool_end':ends[e['data']['owner']].append(e['data']['actual_observed_monotonic_s'])
  for r in backend:
   owner=r['owner']
   if owner in prev:
    prior=prev[owner];release=prior['release_ns']/1e9;lookup=r['lookup_ns']/1e9
    end=[t for t in ends[owner] if release<=t<=lookup]
    rows.append(dict(capacity=c,concurrency=b['concurrency'],repeat=b['repeat'],policy=b['policy'],owner=owner,reuse_gap_s=lookup-release,tool_end_to_lookup_s=lookup-max(end) if end else None,actual_hit_tokens=r['hit']))
   prev[owner]=r
summary=[]
for c in [24,32,64]:
 for n in [3,6,10]:
  for p in ['retired_first_lru','conditional','gbdt']:
   rs=[r for r in rows if (r['capacity'],r['concurrency'],r['policy'])==(c,n,p)]
   if not rs:continue
   vals=[r['reuse_gap_s'] for r in rs];q=[r['tool_end_to_lookup_s'] for r in rs if r['tool_end_to_lookup_s'] is not None]
   summary.append(dict(capacity=c,concurrency=n,policy=p,reuses=len(rs),fraction_gap_within3s=sum(x<=3 for x in vals)/len(vals),reuse_gap_p50_s=statistics.median(vals),reuse_gap_p95_s=float(np.quantile(vals,.95)),post_tool_end_to_lookup_p50_s=statistics.median(q) if q else None))
(out/'reuse-gap-summary.json').write_text(json.dumps(summary,indent=2)+'\n');(out/'reuse-gaps.json').write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps(summary,indent=2))
