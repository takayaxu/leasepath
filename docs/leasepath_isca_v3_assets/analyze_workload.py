"""Read-only descriptive analysis of audited development traces; no inference.

Run extraction once, then run this file from any directory with Python -B.
Tables use empirical counts, quantiles use linear interpolation, and charts
show empirical CDFs without treating correlated requests as independent tasks.
"""
from pathlib import Path
from collections import Counter,defaultdict
from itertools import combinations
import csv,json,math,hashlib
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[2]
ASSET=Path(__file__).resolve().parent
OUT=ROOT/'results/analysis/agent_workload_v1_2026-11-11'
RAW=ROOT/'results/agent/continuous_pilot_v7_2026-11-11/raw'

def read(p):return json.loads(p.read_text())
def dump(p,v):p.write_text(json.dumps(v,indent=2,allow_nan=False)+'\n')
def writecsv(name,rows):
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def stats(values):
    a=np.asarray(values,dtype=float)
    return dict(n=len(a),min=float(a.min()),p25=float(np.quantile(a,.25)),p50=float(np.median(a)),p75=float(np.quantile(a,.75)),p90=float(np.quantile(a,.9)),p95=float(np.quantile(a,.95)),max=float(a.max()),mean=float(a.mean()),sum=float(a.sum()))
def lcp(a,b):
    for i,(x,y) in enumerate(zip(a,b)):
        if x!=y:return i
    return min(len(a),len(b))
def cdf(ax,values,label,color=None,weights=None):
    a=np.asarray(values);order=np.argsort(a);a=a[order]
    w=np.ones(len(a)) if weights is None else np.asarray(weights)[order]
    y=np.cumsum(w)/sum(w)
    ax.step(a,y,where='post',label=label,color=color,lw=1.7)
    ax.set_ylim(0,1.02);ax.grid(alpha=.2);ax.spines[['top','right']].set_visible(False)
def save(fig,name):
    fig.savefig(ASSET/'figures'/f'{name}.pdf');fig.savefig(ASSET/'figures'/f'{name}.png',dpi=170);plt.close(fig)

controller=read(OUT/'collection-controller-compact.json');req=read(OUT/'collection-backend-compact.json')
summary=read(RAW/'summary.json');audit=read(RAW.parent/'audit/summary.json');snap=read(RAW.parent/'audit/prediction-snapshots.json')['rows']
rounds=[read(p) for p in sorted((RAW/'rounds').glob('*.json'))]
assert summary['status']==audit['status']=='passed'
assert len(req)==244 and len(rounds)==48 and all(r['success'] for r in rounds)
assert sum(len(r['tokens']) for r in req)==1227620
ready={};current={};last_cause={};dispatch={};trigger={};arrivals=defaultdict(list);think=[];waiting={};sources={}
for e in controller:
    d=e['data'];o=d.get('owner');kind=e['kind']
    if kind=='workflow_submit':sources[o]=d['source_group']
    elif kind=='user_wait':waiting[o]=e['ts_ns']/1e9
    elif kind=='user_arrival':
        at=d['actual_observed_monotonic_s'];current[o]=d['round'];last_cause[o]='user'
        arrivals[o].append(at)
        if o in waiting:think.append(at-waiting.pop(o))
    elif kind=='tool_end':last_cause[o]='tool'
    elif kind=='request_ready':ready[o]=d['actual_observed_monotonic_s']
    elif kind=='request_dispatch':dispatch[o]=e['ts_ns']/1e9
    elif kind=='request_input':
        trigger[d['request_id']]=dict(round=current[o],trigger=last_cause[o],ready_s=ready[o],controller_dispatch_s=dispatch[o])
by_owner=defaultdict(list);rows=[];intervals=[];counts=Counter()
for r in req:
    o=r['owner'];p=by_owner[o][-1] if by_owner[o] else None;meta=trigger[r['request_id']]
    n=len(r['tokens']);possible=0
    if p:
        common=lcp(p['tokens'],r['tokens']);possible=min(common//128*128,(len(p['tokens'])-1)//128*128)
        assert possible==(len(p['tokens'])-1)//128*128,'changed prefix in accepted collection'
        interval=dict(owner=o,origin_request_id=p['request_id'],next_request_id=r['request_id'],trigger=meta['trigger'],
            release_to_lookup_s=(r['lookup_ns']-p['release_ns'])/1e9,
            release_to_ready_s=meta['ready_s']-p['release_ns']/1e9,
            ready_to_lookup_s=r['lookup_ns']/1e9-meta['ready_s'],compatible_prior_tokens=possible)
        assert min(interval[k] for k in ['release_to_lookup_s','release_to_ready_s','ready_to_lookup_s'])>=-1e-7
        assert abs(interval['release_to_lookup_s']-interval['release_to_ready_s']-interval['ready_to_lookup_s'])<1e-6
        intervals.append(interval)
    row=dict(owner=o,request_id=r['request_id'],request_ordinal=len(by_owner[o])+1,round=meta['round']+1,trigger=meta['trigger'],
       input_tokens=n,output_tokens=len(r['output_tokens']),compatible_prior_tokens=possible,compatible_fraction=possible/n,
       ready_to_lookup_s=r['lookup_ns']/1e9-meta['ready_s'],ready_to_dispatch_s=meta['controller_dispatch_s']-meta['ready_s'],
       lookup_s=r['lookup_ns']/1e9,release_s=r['release_ns']/1e9,cached_tokens=r['hit'],prefill_tokens=r['prefill'])
    assert row['cached_tokens']+row['prefill_tokens']==n
    rows.append(row);by_owner[o].append(r);counts[meta['trigger']]+=1
assert dict(counts)=={'user':48,'tool':196}
project=[];tools=[]
for o,rr in by_owner.items():
    local=[r for r in rows if r['owner']==o];roundlocal=[r for r in rounds if r['owner']==o]
    calls=[json.loads(l) for l in (RAW/'tools'/o/'calls.jsonl').read_text().splitlines()]
    for c in calls:
        tools.append(dict(owner=o,call_id=c['call_id'],round=c['round']+1,tool=c['name'],
            duration_s=(c['ended_ns']-c['started_ns'])/1e9,
            error=c['status']!='ok' or c['result'].get('returncode',0)!=0))
    project.append(dict(owner=o,source_group=sources[o],user_turns=len(roundlocal),model_requests=len(rr),tools=len(calls),tool_types=len(set(c['name'] for c in calls)),
       input_tokens=sum(r['input_tokens'] for r in local),output_tokens=sum(r['output_tokens'] for r in local),first_input_tokens=local[0]['input_tokens'],last_input_tokens=local[-1]['input_tokens'],
       session_first_to_last_lookup_s=(rr[-1]['lookup_ns']-rr[0]['lookup_ns'])/1e9,
       first_user_to_last_delivery_s=max(r['terminal_s'] for r in roundlocal)-min(arrivals[o])))
assert len(tools)==196 and sum(t['error'] for t in tools)==4
writecsv('requests.csv',rows);writecsv('intervals.csv',intervals);writecsv('tools.csv',tools);writecsv('projects.csv',project)
writecsv('rounds.csv',[{k:r[k] for k in ['owner','round','generations','tool_calls','success','response_s']} for r in rounds])
metric={
 'scope':'six authored development projects, one accepted collection; not natural human sessions or independent holdout',
 'counts':dict(projects=6,user_turns=48,model_requests=244,tool_calls=196,tool_types=9,tool_errors=4,continuation_intervals=len(intervals),snapshots=len(snap),observed_episodes=234),
 'trigger_counts':dict(counts),'input_tokens':stats([r['input_tokens'] for r in rows]),'output_tokens':stats([r['output_tokens'] for r in rows]),
 'input_output_token_ratio':sum(r['input_tokens'] for r in rows)/sum(r['output_tokens'] for r in rows),
 'compatible_prior_tokens_total':sum(r['compatible_prior_tokens'] for r in rows),
 'compatible_prior_fraction_of_all_input':sum(r['compatible_prior_tokens'] for r in rows)/sum(r['input_tokens'] for r in rows),
 'compatible_fraction_per_request':stats([r['compatible_fraction'] for r in rows]),
 'requests_per_project':stats([r['model_requests'] for r in project]),'requests_per_user_turn':stats([r['generations'] for r in rounds]),
 'tools_per_user_turn':stats([r['tool_calls'] for r in rounds]),'session_first_to_last_lookup_s':stats([r['session_first_to_last_lookup_s'] for r in project]),
 'tool_duration_s':stats([r['duration_s'] for r in tools]),'actual_user_wait_s':stats(think),
 'ready_to_lookup_s':stats([r['ready_to_lookup_s'] for r in rows]),
 'ready_to_controller_dispatch_s':stats([r['ready_to_dispatch_s'] for r in rows]),
 'tool_result_notification_lag_s':stats([e['ts_ns']/1e9-e['data']['actual_observed_monotonic_s'] for e in controller if e['kind']=='tool_end']),
 'release_to_lookup_s':stats([r['release_to_lookup_s'] for r in intervals]),
 'snapshot_remaining_s':stats([r['remaining_s'] for r in snap]),
 'snapshot_phase_counts':dict(Counter(r['features']['phase'] for r in snap)),
 'events':dict(Counter(r['event'] for r in snap)),
 'snapshot_fraction_demand_within_30s':sum(r['remaining_s']<=30 for r in snap)/len(snap),
 'episode_weighted_snapshot_fraction_demand_within_30s':sum(r['weight']*(r['remaining_s']<=30) for r in snap)/sum(r['weight'] for r in snap),
 'interval_fraction_demand_within_30s':sum(r['release_to_lookup_s']<=30 for r in intervals)/len(intervals),
 'projects':project,
 'definitions':{'input_tokens':'native full input length, including reusable prefix; not executed prefill after caching',
    'compatible_prior_tokens':'128-token-aligned prefix of previous same-owner INPUT verified in next input, independent of actual cache hits; excludes cached generated suffix and cross-owner sharing',
    'ready_to_lookup_s':'controller-recorded causal readiness to actual native lookup; includes queue, client and admission preparation',
    'tool_duration_s':'independently recorded tool call end minus start; not notification-processing time',
    'snapshot_weight':'one unit total per observed episode; correlated snapshots, not independent observations'}}

# Same-state diagnostic uses logged scores from a model deployed before its arm.
static=read(OUT/'static-backend-compact.json');staticby=defaultdict(list)
for r in static:staticby[r['owner']].append(r)
for v in staticby.values():v.sort(key=lambda r:r['lookup_ns'])
def next_demand(owner,at,features):
    previous=[r for r in staticby[owner] if r['release_ns']/1e9<=at]
    future=[r for r in staticby[owner] if r['lookup_ns']/1e9>=at]
    if not previous or not future:return None
    count=features[owner]['prefix_tokens']
    assert previous[-1]['tokens'][:count]==future[0]['tokens'][:count]
    return future[0]['lookup_ns']/1e9-at
states=[];paircounts=Counter();case=None
for r in static:
    a=r['audit'];s=r['first_sweep']
    if not s or s['distribution_fallback'] or s['cost_fallback']:continue
    candidates=s['candidates'];scores=a['owner_scores'];at=a['t0_s']
    if len(candidates)<2:continue
    scored=[];valid=True
    for c in candidates:
        names=[o['owner'] for o in c['owners']]
        if any(o not in scores or scores[o]['F_U_3s'] is None or scores[o]['W'] is None for o in names):valid=False;break
        w=max((scores[o]['W'] for o in names),default=0.)/c['block_bytes']
        assert math.isclose(w,c['value'],rel_tol=1e-10,abs_tol=1e-20)
        p=max((scores[o]['F_U_3s'] for o in names),default=0.)/c['block_bytes']
        rank=list(c['rank']);rank[1]=p
        future=[next_demand(o,at,a['observed_idle_features']) for o in names]
        future=[v for v in future if v is not None]
        scored.append(dict(key=c['key'],owners=names,w=w,point=p,wrank=c['rank'],prank=rank,demand_s=min(future) if future else None))
    if not valid:continue
    for x,y in combinations(scored,2):
        # Comparing physical block values scales all scores; use raw probability
        # differences for a fixed tolerance independent of byte accounting.
        dp=(x['point']-y['point'])*33554432;dw=(x['w']-y['w'])*33554432
        if abs(dp)<=1e-10 and abs(dw)>1e-10:paircounts['window_tie_distribution_distinguishes']+=1
        elif abs(dp)>1e-10 and abs(dw)>1e-10:
            paircounts['both_strict']+=1
            if dp*dw<0:paircounts['strict_reversals']+=1
    wd=min(scored,key=lambda c:c['wrank']);pt=min(scored,key=lambda c:c['prank'])
    assert wd['key']==s['victim']
    different=wd['key']!=pt['key']
    states.append(dict(request_id=r['request_id'],candidate_count=len(scored),different_victim=different,
        distribution_victim=wd['key'],window_victim=pt['key'],distribution_owners=';'.join(wd['owners']),window_owners=';'.join(pt['owners']),
        distribution_victim_next_demand_s=wd['demand_s'],window_victim_next_demand_s=pt['demand_s']))
    if different and case is None and wd['owners'] and pt['owners'] and wd['demand_s'] is not None and pt['demand_s'] is not None:
        case=dict(request_id=r['request_id'],at_s=at,distribution_victim=wd,window_victim=pt,
           owner_scores={o:scores[o] for o in set(wd['owners']+pt['owners'])})
metric['same_state_diagnostic']=dict(scope='first eviction sweep per static-GBDT admission, same logged predictions/legal leaves/tiebreaks; one action only, not alternate closed-loop replay',
    eligible_states=len(states),different_first_victims=sum(r['different_victim'] for r in states),pair_counts=dict(paircounts),
    differing_states_distribution_victim_demanded_earlier=sum(r['different_victim'] and r['distribution_victim_next_demand_s'] is not None and r['window_victim_next_demand_s'] is not None and r['distribution_victim_next_demand_s']<r['window_victim_next_demand_s'] for r in states),
    differing_states_distribution_victim_demanded_later=sum(r['different_victim'] and r['distribution_victim_next_demand_s'] is not None and r['window_victim_next_demand_s'] is not None and r['distribution_victim_next_demand_s']>r['window_victim_next_demand_s'] for r in states),
    first_case=case,exclusion='no eviction, fewer than two legal leaves, fallback or missing common score support')
assert states and case
writecsv('first_sweep_comparisons.csv',states)
dump(OUT/'summary.json',metric)

plt.rcParams.update({'font.size':9,'pdf.fonttype':42,'ps.fonttype':42})
colors=['#406c9b','#d3893d','#4c9b8c','#9d6aa9','#c55d65','#7d8941']
fig,axs=plt.subplots(1,3,figsize=(10.5,2.75),layout='constrained')
cdf(axs[0],[r['input_tokens'] for r in rows],'Full input');cdf(axs[0],[r['output_tokens'] for r in rows],'Generated output')
axs[0].set_xscale('log');axs[0].set_xlabel('Tokens per model request');axs[0].set_ylabel('Request CDF');axs[0].legend(fontsize=8)
for color,(o,rr) in zip(colors,by_owner.items()):
    vals=[r for r in rows if r['owner']==o];axs[1].plot([r['request_ordinal'] for r in vals],[r['input_tokens'] for r in vals],label=o.removeprefix('continuous_'),color=color)
axs[1].set_xlabel('Model request within project');axs[1].set_ylabel('Full input tokens');axs[1].legend(fontsize=7,ncol=2)
cdf(axs[2],[r['compatible_fraction']*100 for r in rows],'All 244 requests')
axs[2].set_xlabel('Compatible prior-input prefix (%)');axs[2].set_ylabel('Request CDF');axs[2].set_xlim(0,100)
save(fig,'workload_context')
fig,axs=plt.subplots(1,3,figsize=(10.5,2.7),layout='constrained')
names=[p['owner'].removeprefix('continuous_') for p in project]
x=np.arange(len(project));axs[0].bar(x-.17,[p['model_requests'] for p in project],.34,label='Model requests');axs[0].bar(x+.17,[p['tools'] for p in project],.34,label='Tool calls')
axs[0].set_xticks(x,names,rotation=35,ha='right',fontsize=7);axs[0].set_ylabel('Count per project');axs[0].legend(fontsize=8);axs[0].set_ylim(0,max(p['model_requests'] for p in project)*1.2)
cdf(axs[1],[r['generations'] for r in rounds],'Model requests');cdf(axs[1],[r['tool_calls'] for r in rounds],'Tool calls')
axs[1].set_xlabel('Count per user turn');axs[1].set_ylabel('User-turn CDF');axs[1].legend(fontsize=8)
cdf(axs[2],[p['session_first_to_last_lookup_s']/60 for p in project],'Six projects')
axs[2].set_xlabel('First-to-last lookup (minutes)');axs[2].set_ylabel('Project CDF');axs[2].legend(fontsize=8)
save(fig,'workload_loops')
fig,axs=plt.subplots(1,3,figsize=(10.5,2.8),layout='constrained')
cdf(axs[0],[r['duration_s'] for r in tools],'Tool call (n=196)')
cdf(axs[0],think,'Actual user wait (n=42)')
cdf(axs[0],[r['ready_to_lookup_s'] for r in rows],'Ready to lookup (n=244)')
axs[0].set_xscale('log');axs[0].set_xlabel('Seconds');axs[0].set_ylabel('CDF (different units)');axs[0].legend(fontsize=7)
for phase,label in [('waiting_tool','Waiting tool'),('queued','Queued'),('waiting_user','Waiting user')]:
    selected=[r for r in snap if r['features']['phase']==phase]
    cdf(axs[1],[r['remaining_s'] for r in selected],label+f' (n={len(selected)})')
axs[1].axvline(30,color='#777',ls=':',lw=1);axs[1].set_xscale('log');axs[1].set_xlabel('Snapshot to next lookup (s)');axs[1].set_ylabel('Snapshot CDF');axs[1].legend(fontsize=7)
for name in sorted(set(t['tool'] for t in tools)):
    vals=[t['duration_s'] for t in tools if t['tool']==name]
    cdf(axs[2],vals,f'{name} ({len(vals)})')
axs[2].set_xscale('log');axs[2].set_xlabel('Measured tool-call duration (s)');axs[2].set_ylabel('Call CDF');axs[2].legend(fontsize=6,loc='lower right')
save(fig,'workload_timing')
fig,axs=plt.subplots(1,2,figsize=(7.3,2.6),layout='constrained')
for o,sc in sorted(case['owner_scores'].items()):
    dist=sc['distribution'];xx=[0]+[d['right_s'] for d in dist if d['right_s']<=30];yy=[0]+[d['F_U'] for d in dist if d['right_s']<=30]
    axs[0].step(xx,yy,where='post',label=o.removeprefix('continuous_'))
axs[0].axvline(3,color='#777',ls=':',lw=1);axs[0].set_xlabel('Prediction horizon (s)');axs[0].set_ylabel('Logged demand probability');axs[0].set_ylim(0,1);axs[0].legend(fontsize=8)
wd=case['distribution_victim'];pt=case['window_victim']
vals=[pt['demand_s'],wd['demand_s']]
bar=axs[1].bar(['Window would evict','Distribution evicted'],vals,color=['#d3893d','#406c9b'])
axs[1].bar_label(bar,labels=[f'{v:.1f} s' for v in vals],padding=3);axs[1].set_ylim(0,max(vals)*1.2);axs[1].set_ylabel('Observed next demand after decision (s)')
save(fig,'workload_score_case')
print(json.dumps({k:v for k,v in metric.items() if k not in ['projects','definitions','same_state_diagnostic']},indent=2))
print('SAME STATE',len(states),sum(r['different_victim'] for r in states),dict(paircounts))
print('CASE',case['request_id'],wd['owners'],wd['demand_s'],pt['owners'],pt['demand_s'])
