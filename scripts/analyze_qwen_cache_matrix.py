"""Static scientific plots of all retained stage-3b measurements; missing cells stay missing."""
from pathlib import Path
import json,statistics
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
root=Path(__file__).resolve().parents[1]
out=root/'results/cache_policy/qwen_stage3b_2026-11-08'
s=json.loads((out/'summary.json').read_text());tasks=json.loads((out/'tasks.json').read_text());tools=json.loads((out/'tools.json').read_text())
policies=['retired_first_lru','conditional','gbdt'];caps=[24,32,64];ns=[3,6,10]
cells={(x['capacity'],x['concurrency'],x['policy']):x for x in s['cells']}
fig,axes=plt.subplots(2,2,figsize=(11,8),layout='constrained')
for col,p in enumerate(policies[1:]):
 savings=np.array([[(1-cells[c,n,p]['jct_s']['mean']/cells[c,n,policies[0]]['jct_s']['mean'])*100 if cells[c,n,p]['complete_paired_comparison'] else np.nan for n in ns] for c in caps])
 hits=np.array([[cells[c,n,p]['mean_cached_tokens']-cells[c,n,policies[0]]['mean_cached_tokens'] if cells[c,n,p]['complete_paired_comparison'] else np.nan for n in ns] for c in caps])
 for row,data in enumerate([savings,hits]):
  ax=axes[row,col]; lim=max(1.,np.nanmax(np.abs(data)));im=ax.imshow(data,cmap='RdYlGn',vmin=-lim,vmax=lim)
  ax.set_xticks(range(3),ns);ax.set_yticks(range(3),caps);ax.set_xlabel('Concurrent sessions');ax.set_ylabel('Capacity (128-token blocks)')
  ax.set_title(p+(' JCT reduction vs LRU (%)' if row==0 else ' extra hit tokens vs LRU'))
  for i in range(3):
   for j in range(3):ax.text(j,i,('not run' if caps[i]!=24 else 'incomplete') if np.isnan(data[i,j]) else f'{data[i,j]:+.2f}' if row==0 else f'{data[i,j]:+.0f}',ha='center',va='center',fontsize=11)
  fig.colorbar(im,ax=ax,shrink=.8)
fig.suptitle('Stopped real KV matrix: 24 blocks x 3/6 sessions complete, 3 cyclic repeats\nOther planned cells incomplete/unrun; known development parents, descriptive only',fontsize=11)
fig.savefig(out/'matrix.png',dpi=180);fig.savefig(out/'matrix.svg');plt.close(fig)
ridge=sorted([t for t in tools if t['task']=='ridge_validation' and t['name']=='compute' and t['status']=='ok'],key=lambda t:t['started_ns'])
parts=['input_hash','child_launch_exit','npz_materialize','sklearn_imports','numerical_compute','post_input_hash','other']
rows=[]
for t in ridge:
 phases=t['worker_timing']['phases'];row={p:phases.get(p,{}).get('wall_ns',0)/1e9 for p in ['npz_materialize','sklearn_imports','numerical_compute']}
 row.update(input_hash=t['input_check_ns']/1e9,post_input_hash=t['post_input_check_ns']/1e9,child_launch_exit=t['child_launch_import_exit_ns']/1e9)
 total=(t['ended_ns']-t['started_ns'])/1e9;row['other']=max(0,total-sum(row.values()));rows.append(row)
fig,ax=plt.subplots(figsize=(15,5),layout='constrained');bottom=np.zeros(len(rows))
for p in parts:
 vals=np.array([r[p] for r in rows]);ax.bar(range(len(rows)),vals,bottom=bottom,label=p,width=.85);bottom+=vals
ax.set_xlabel('Ridge compute call in chronological order (all 19 retained batches)');ax.set_ylabel('Actual tool wall time (seconds)');ax.legend(ncol=4,fontsize=8);ax.set_title('Ridge tool variance decomposition; no outlier discarded')
fig.savefig(out/'ridge-tool-phases.png',dpi=180);fig.savefig(out/'ridge-tool-phases.svg');plt.close(fig)
ops={}
for op in sorted({t['arguments'].get('operation') for t in tools if t['name']=='compute' and t['status']=='ok'}):
 ts=[t for t in tools if t['name']=='compute' and t['status']=='ok' and t['arguments']['operation']==op]
 def desc(v):return dict(n=len(v),mean=statistics.mean(v),p50=statistics.median(v),p95=float(np.quantile(v,.95)),min=min(v),max=max(v))
 ops[op]={'tool_wall_s':desc([(t['ended_ns']-t['started_ns'])/1e9 for t in ts]),'worker_wall_s':desc([t['worker_execute_ns']/1e9 for t in ts]),'process_cpu_s':desc([t['worker_timing']['overall']['cpu_ns']/1e9 for t in ts]),'main_thread_runqueue_wait_s':desc([t['worker_timing']['overall']['main_thread_runqueue_wait_ns']/1e9 for t in ts]),'worker_storage_read_bytes':desc([t['worker_timing']['overall']['read_bytes'] for t in ts]),'phases':{}}
 for phase in sorted({p for t in ts for p in t['worker_timing']['phases']}):
  ps=[t['worker_timing']['phases'][phase] for t in ts if phase in t['worker_timing']['phases']]
  ops[op]['phases'][phase]={k:desc([p[k]/(1e9 if k.endswith('_ns') else 1) for p in ps]) for k in ['wall_ns','cpu_ns','main_thread_runqueue_wait_ns','read_bytes','major_faults']}
(out/'tool-summary.json').write_text(json.dumps(ops,indent=2)+'\n')
lines=['| 块数 | 并发 | LRU JCT/s | 经验 JCT/s (配对差) | GBDT JCT/s (配对差) | 额外 hit tokens 经验/GBDT | 同状态淘汰集合改变 经验/GBDT |','|---:|---:|---:|---:|---:|---:|---:|']
for c in caps:
 for n in ns:
  a,b,d=[cells[c,n,p] for p in policies]
  if not a['complete_paired_comparison']:
   lines.append(f"| {c} | {n} | {'未完整通过' if c==24 else '未运行'} | — | — | — | — |")
   continue
  lines.append(f"| {c} | {n} | {a['jct_s']['mean']:.3f} | {b['jct_s']['mean']:.3f} ({b['paired_jct_delta_s']['mean']:+.3f}) | {d['jct_s']['mean']:.3f} ({d['paired_jct_delta_s']['mean']:+.3f}) | {b['mean_cached_tokens']-a['mean_cached_tokens']:+.1f}/{d['mean_cached_tokens']-a['mean_cached_tokens']:+.1f} | {b['same_state_victim_set_changes']}/{d['same_state_victim_set_changes']} |")
(out/'matrix-table.md').write_text('\n'.join(lines)+'\n')
slow=sorted([t for t in tools if t['name']=='compute' and t['status']=='ok'],key=lambda t:t['ended_ns']-t['started_ns'],reverse=True)[:10]
(out/'slowest-tools.json').write_text(json.dumps(slow,indent=2)+'\n')
print('\n'.join(lines));print(json.dumps(ops['ridge_cv'],indent=2))
