"""Export independently audited natural JCT, tool and host observations."""
import argparse
import csv
import json
from pathlib import Path
import statistics


def save_csv(path,rows):
    if not rows:return
    with path.open('w',newline='') as stream:
        keys=list(dict.fromkeys(k for row in rows for k in row))
        writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in row.items()})


def main():
    p=argparse.ArgumentParser();p.add_argument('--audit',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=json.loads(a.audit.read_text());a.output.mkdir(parents=True,exist_ok=False)
    save_csv(a.output/'batches.csv',r['batch_metrics']);save_csv(a.output/'tasks.csv',r['task_metrics'])
    tools=[]
    for batch in r['batch_metrics']:
        for tool in batch['tools']:tools.append({**{k:batch[k] for k in ('phase','repeat','policy')},**tool})
    save_csv(a.output/'tools.csv',tools)
    common=[]
    repeats=sorted(set(x['repeat'] for x in r['batch_metrics']))
    complete=[repeat for repeat in repeats if {x['policy'] for x in r['batch_metrics'] if x['repeat']==repeat}=={'retired_first_lru','conditional','gbdt'}]
    for policy in ('retired_first_lru','conditional','gbdt'):
        selected=[b for b in r['batch_metrics'] if b['repeat'] in complete and b['policy']==policy]
        if selected:common.append({'policy':policy,'complete_cycles':len(selected),**{key:statistics.mean(b[key] for b in selected) for key in ('mean_successful_jct_s','cached_tokens','prefill_s','decode_s')}})
    if common:
        baseline=common[0]['mean_successful_jct_s']
        for row in common:
            row['jct_delta_s']=row['mean_successful_jct_s']-baseline
            row['jct_delta_pct']=100*row['jct_delta_s']/baseline
    summary={k:v for k,v in r.items() if k not in ('source_sha256','batch_metrics','task_metrics')}
    summary['common_complete_cycles']=complete;summary['common_cycle_aggregates']=common
    (a.output/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    if any(x['complete_repeat_pairs'] for x in r['paired']):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(7,3.5))
        for index,row in enumerate(r['paired']):
            values=row['repeat_mean_jct_deltas_s'];ax.plot(range(1,len(values)+1),values,'o-',label=row['policy'])
        ax.axhline(0,color='gray',lw=1);ax.set(xlabel='Counterbalanced cycle (three known development tasks)',ylabel='Mean task JCT difference vs LRU (s)');ax.legend();fig.tight_layout()
        fig.savefig(a.output/'paired_jct.png',dpi=180);fig.savefig(a.output/'paired_jct.pdf');plt.close(fig)
    print(json.dumps({'experiment_status':r['experiment_status'],'batches':r['batches'],'correct':r['correct'],'common_cycles':common,'paired':r['paired']},indent=2))

if __name__=='__main__':main()
