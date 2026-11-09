"""Read-only score sensitivity on recorded states; never simulate JCT or actions.

No training, future labels or task identifiers enter either predictor. The
proposed score uses equal costs to isolate the change from one CDF point to a
weighted distribution. Recorded owner pairs need not be legal victim pairs.
"""
import argparse
from copy import deepcopy
import itertools
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import sha256
from leasepath.reuse_predictor import EmpiricalRiskPredictor


def sign(value):
    return 0 if abs(value) <= 1e-12 else (1 if value > 0 else -1)


def scores(distribution):
    previous = 0.
    weighted = 0.
    fixed = None
    for row in distribution:
        if row['right_s'] > 30:
            break
        if not row['supported'] or row['global_risk_workflows'] < 5:
            raise ValueError('proposal support requirement not met')
        delta = row['F_U'] - previous
        if delta < -1e-12:
            raise ValueError('nonmonotone cumulative incidence')
        weighted += math.exp(-row['right_s']/10.)*max(delta, 0.)
        previous = row['F_U']
        if row['right_s'] == 3:
            fixed = row['F_U']
    if fixed is None:
        raise ValueError('missing 3-second edge')
    return {'F_U_3s': fixed, 'discounted_F_U_H30_tau10': weighted}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, default=ROOT/'.artifacts/qwen-cache-resident-tree-natural-2026-11-09/run')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    directory = ROOT/'results/prediction/qwen50_holdout_2026-11-08'
    models = {'conditional': EmpiricalRiskPredictor.load(directory/'conditional-model.json'),
              'gbdt': GBDTRiskPredictor.load(directory/'gbdt-model.json')}
    cases = []
    original_checks = 0
    for path in sorted((args.run/'natural').glob('concurrency-*/*-quality.json')):
        quality = json.loads(path.read_text())
        for ordinal, request in enumerate(quality['model_requests']):
            saved = request['policy_decision']['same_state_diagnostic']
            if not saved['owner_F_U_3s']:
                continue
            record = {'source':str(path.relative_to(args.run)), 'request_ordinal':ordinal,
                      'recorded_policy':quality['policy'], 'models':{}}
            for name, model in models.items():
                support = getattr(model, 'support', model)
                values = {}
                for owner, feature in saved['observed_idle_features'].items():
                    current = deepcopy(feature)
                    current['compatibility_id'] = support.compatibilities[0]
                    result = scores(model.predict(current))
                    values[owner] = result
                    if name == quality['policy']:
                        expected = saved['owner_F_U_3s'][owner]
                        if expected is None or not math.isclose(result['F_U_3s'],expected,rel_tol=0,abs_tol=1e-10):
                            raise ValueError('recomputed frozen score differs')
                        original_checks += 1
                pairs = []
                for left, right in itertools.combinations(sorted(values),2):
                    before = sign(values[left]['F_U_3s']-values[right]['F_U_3s'])
                    after = sign(values[left]['discounted_F_U_H30_tau10']-values[right]['discounted_F_U_H30_tau10'])
                    pairs.append({'owners':[left,right],'old_sign':before,'new_sign':after,
                                  'strict_reversal':before*after == -1,'order_or_tie_changed':before != after})
                record['models'][name] = {'owner_scores':values,'pairs':pairs}
            cases.append(record)
    summary = {}
    for name in models:
        pairs = [p for case in cases for p in case['models'][name]['pairs']]
        summary[name] = {'states':len(cases),'owner_pairs':len(pairs),
                         'strict_reversals':sum(p['strict_reversal'] for p in pairs),
                         'order_or_tie_changes':sum(p['order_or_tie_changed'] for p in pairs)}
    support = models['conditional']
    result = {'status':'passed','analysis_only':True,'model_generations':0,'training_performed':False,
              'parameters':{'H_s':30,'tau_s':10,'weights':'exp(-right_edge/tau)','costs':'equal','min_global_workflows':5},
              'original_probability_checks':original_checks,'summary':summary,
              'support': [{'right_s':edge,'workflows':row['workflows']} for edge,row in zip(support.right_edges,support.global_counts)],
              'interpretation':'Same recorded states rescored by both frozen models. Owner pairs are not necessarily legal victim pairs. No new cache state trajectory, hit count, prefill saving or JCT is inferred.',
              'source_sha256':{str(Path(__file__).resolve()):sha256(__file__),
                               **{str(directory/(name+'-model.json')):sha256(directory/(name+'-model.json')) for name in models}},
              'cases':cases}
    args.output.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('cases','source_sha256')},indent=2))

if __name__ == '__main__':
    main()
