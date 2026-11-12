"""Target-data fits plus chronological development prediction validation.

The last 25% of time is scored using only matured observations available at
the split. Final initial models use the whole audited development collection.
Neither result is independent holdout evidence or an E2E policy benefit.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from leasepath.continuous_predictor_v1 import ContinuousGBDT,ContinuousEmpirical,training_data,FEATURE_NAMES
from leasepath.distribution_cache import distribution_scores
from leasepath.frozen_distribution_baseline import file_digest
from evaluate_independent_predictions_v1 import horizon_label

DOMAIN='continuous-development-v1'


def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--audit',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    audit=json.loads((args.audit/'summary.json').read_text());assert audit['status']=='passed'
    rows=json.loads((args.audit/'prediction-snapshots.json').read_text())['rows']
    if len({r['owner'] for r in rows})<5:raise ValueError('at least five observed development projects required')
    output=args.output;output.mkdir(parents=True,exist_ok=False)
    times=sorted(r['snapshot_s'] for r in rows);split=times[int(.75*(len(times)-1))]
    samples,targets=training_data(rows,domain=DOMAIN,cutoff_s=split)
    models={name:cls().fit(samples,targets) for name,cls in [('empirical',ContinuousEmpirical),('gbdt',ContinuousGBDT)]}
    validation=[r for r in rows if r['snapshot_s']>=split]
    metrics={};predictions=[]
    for name,model in models.items():
        pairs={h:[] for h in (1,3,10,30)};eligible=0
        for row in validation:
            distribution=model.predict({**row['features'],'compatibility_id':DOMAIN});eligible+=distribution_scores(distribution)['W'] is not None
            predictions.append(dict(sample_id=row['snapshot_id'],owner=row['owner'],model=name,snapshot_s=row['snapshot_s'],distribution=distribution,event=row['event'],remaining_s=row['remaining_s'],training_cutoff_s=split))
            for horizon in pairs:
                label=horizon_label(row['event'],row['remaining_s'],horizon)
                prediction=next((p for p in distribution if p['right_s']==horizon and p['supported']),None)
                if label is not None and prediction is not None:pairs[horizon].append((row['weight'],label,prediction['F_U']))
        metrics[name]={'snapshots':len(validation),'full_window_policy_eligible':eligible,'horizons':{}}
        for h,part in pairs.items():
            weight=sum(w for w,y,p in part)
            metrics[name]['horizons'][str(h)]=dict(snapshots=len(part),Brier_U=sum(w*(p-y)**2 for w,y,p in part)/weight if weight else None)
    cutoff=max(r['terminal_s'] for r in rows)+1e-6
    all_samples,all_targets=training_data(rows,domain=DOMAIN,cutoff_s=cutoff)
    fitted={};training_start=time.monotonic()
    for name,cls in [('empirical',ContinuousEmpirical),('gbdt',ContinuousGBDT)]:
        model=cls().fit(all_samples,all_targets);path=output/(name+'-initial.json');model.save(path);restored=cls.load(path)
        assert all(restored.predict(s['features'])==model.predict(s['features']) for s in all_samples[:30])
        fitted[name]=model
    write(output/'seed-observations.json',rows);write(output/'validation-predictions.json',predictions)
    summary=dict(status='trained_development_initial_models',training_domain=DOMAIN,projects=len({r['owner'] for r in rows}),snapshots=len(rows),
        episodes=len({r['episode_id'] for r in rows}),events=dict(Counter(r['event'] for r in rows)),training_cutoff_s=cutoff,
        chronological_split_s=split,pre_split_samples=len(samples),validation=metrics,training_wall_s=time.monotonic()-training_start,
        files={name+'-initial.json':file_digest(output/(name+'-initial.json')) for name in fitted},
        gbdt_feature_gain=dict(zip(FEATURE_NAMES,fitted['gbdt'].booster.feature_importance('gain').tolist())),
        gbdt_support=[dict(right_s=t,workflows=r['workflows']) for t,r in zip(fitted['gbdt'].support.right_edges,fitted['gbdt'].support.global_counts)],
        parameters=fitted['gbdt'].parameters,weighting='equal project, equal episodes, equal snapshots; mean snapshot weight normalized to one',
        holdout_model_tasks=0,holdout_training_rows=0,natural_JCT_acceptance=False,
        source_audit_sha256=file_digest(args.audit/'summary.json'),limitations=['six authored development projects','chronological validation within project histories, not unseen-project generalization','final full-development fits are initial models, not validation predictions'])
    write(output/'summary.json',summary);print(json.dumps(summary))


if __name__=='__main__':main()
