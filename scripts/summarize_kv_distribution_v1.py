"""Export calibrated-cost diagnostics; never extrapolate end-to-end benefit."""
import argparse
import csv
import json
from pathlib import Path
import statistics


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    table=json.loads((args.run/'cost-table.json').read_text());measurements=json.loads((args.run/'calibration-measurements.json').read_text())
    rows=[];details=[]
    for curve in table['curves']:
        p=curve['observed_prefix_tokens'];points=curve['points'];values=[r['prefill_s'] for r in points]
        deltas=[a-b for a,b in zip(values,values[1:])]
        mae=statistics.mean(abs(x['prefill_s']-x['validation_prefill_s']) for x in points)
        rows.append({'observed_prefix_tokens':p,'actual_prompt_tokens':p+1,'fitted_cold_prefill_s':values[0],
            'fitted_full_prefix_hit_prefill_s':values[-1],'fitted_cold_to_hot_difference_s':values[0]-values[-1],
            'median_block_marginal_s':statistics.median(deltas),'min_block_marginal_s':min(deltas),
            'max_block_marginal_s':max(deltas),'validation_MAE_s':mae})
        for index,point in enumerate(points):
            details.append({'observed_prefix_tokens':p,**point,'marginal_cost_s':deltas[index-1] if index else None})
    for filename,records in [('cost-curve-summary.csv',rows),('cost-grid.csv',details)]:
        with (args.output/filename).open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(records[0]),lineterminator='\n');writer.writeheader();writer.writerows(records)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,3,figsize=(12,7),sharey=True,constrained_layout=True)
    for ax,curve in zip(axes.flat,table['curves']):
        prefix=curve['observed_prefix_tokens'];points=curve['points']
        raw=[r for r in measurements if r['prefix_tokens']==prefix and r['split']=='fit']
        ax.scatter([r['hit_tokens'] for r in raw],[r['prefill_s'] for r in raw],s=13,alpha=.5,label='Fit samples (2 repeats)')
        ax.plot([r['hit_tokens'] for r in points],[r['prefill_s'] for r in points],lw=1.5,label='Monotone fit')
        ax.scatter([r['hit_tokens'] for r in points],[r['validation_prefill_s'] for r in points],s=24,marker='x',label='Held-out repeat')
        ax.set_title(f'Observed prefix {prefix}; prompt {prefix+1}')
        ax.set_xlabel('Physically cached prefix tokens');ax.set_ylabel('Native prefill (seconds)');ax.grid(alpha=.2)
    axes.flat[0].legend(fontsize=7)
    fig.suptitle('Independent native prefill calibration — '+table['validation']['status'])
    fig.savefig(args.output/'prefill-cost-curves.png',dpi=160);fig.savefig(args.output/'prefill-cost-curves.pdf');plt.close(fig)
    result={'status':table['validation']['status'],'validation':table['validation'],'curves':rows,
        'sample_parent_disk_read_bytes':sum(r['parent_read_bytes'] for r in measurements),
        'sample_parent_major_faults':sum(r['parent_major_faults'] for r in measurements),
        'scope':'Calibration with generic fixed prompt content. No new policy hit, prefill or JCT savings claimed.'}
    (args.output/'calibration-summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))


if __name__=='__main__':main()
