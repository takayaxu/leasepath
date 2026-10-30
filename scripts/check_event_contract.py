"""Check synthetic event semantics and independent handwritten label expectations."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.contract_fixtures import expand_cases, check_case, write_jsonl
from leasepath.trace import read_events

FIXTURES = ROOT / 'data/fixtures/event_contract.json'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'results/prediction/event_contract')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    config = json.loads(FIXTURES.read_text())
    results, positive_labels = [], {}
    for case, events in expand_cases(config):
        path = args.output / (case['id'] + '.events.jsonl')
        write_jsonl(path, events)
        # Read the written artifact back: acceptance covers the file/export path too.
        row, labels = check_case(case, read_events(path))
        results.append(row)
        if labels is not None:
            label_path = args.output / (case['id'] + '.labels.jsonl')
            write_jsonl(label_path, labels)
            if read_events(label_path) != labels:
                raise AssertionError('label JSONL round trip differs')
            positive_labels[case['id']] = labels
    if positive_labels['legal_demand_physical_miss'] != positive_labels['legal_demand_physical_hit']:
        raise AssertionError('changing only physical hit/miss changed demand labels')
    inputs = [FIXTURES, Path(__file__), ROOT / 'src/leasepath/trace.py',
              ROOT / 'schemas/trace.schema.json', ROOT / 'docs/prediction_contract.md',
              ROOT / 'tests/test_event_contract.py', ROOT / 'src/leasepath/contract_fixtures.py']
    summary = {
        'status': 'passed', 'scope': 'Synthetic event/label contract',
        'evidence_kind': 'synthetic', 'performance_claim_allowed': False,
        'accepted_cases': sum(row['observed'] == 'accepted' for row in results),
        'rejected_cases': sum(row['observed'] == 'rejected' for row in results),
        'physical_hit_miss_labels_identical': True,
        'cases': results,
        'sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
    }
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({key: value for key, value in summary.items() if key not in {'cases', 'sha256'}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
