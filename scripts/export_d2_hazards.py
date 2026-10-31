"""Export risk intervals and explicitly separate administrative censoring views."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_hazards import administrative_views, derive_hazard_rows, summarize_horizons


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dataset', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    samples = [json.loads(l) for l in (args.dataset / 'snapshots.jsonl').read_text().splitlines()]
    targets = [json.loads(l) for l in (args.dataset / 'targets.jsonl').read_text().splitlines()]
    hazards = derive_hazard_rows(samples, targets)
    views = administrative_views(targets)
    for name, records in [('risk_intervals', hazards), ('administrative_views', views)]:
        (args.output / (name + '.jsonl')).write_text(''.join(json.dumps(r) + '\n' for r in records))
    summary = {'snapshots': len(samples), 'risk_interval_rows': len(hazards),
               'trainable_labels': dict(Counter(r['label'] for r in hazards if r['loss_mask'])),
               'masked_partial_intervals': sum(not r['loss_mask'] for r in hazards),
               'cumulative_horizons': summarize_horizons(targets),
               'administrative_views': {'rows': len(views), 'outcomes': dict(Counter(r['outcome'] for r in views)),
                                         'scope': 'separate fixed-horizon derived labels, not natural censoring'},
               'source_sha256': {n: hashlib.sha256((args.dataset / n).read_bytes()).hexdigest()
                                 for n in ['snapshots.jsonl', 'targets.jsonl']},
               'output_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.glob('*.jsonl')}}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k not in {'cumulative_horizons', 'source_sha256', 'output_sha256'}}))


if __name__ == '__main__':
    main()
