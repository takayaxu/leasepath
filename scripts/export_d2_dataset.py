"""Validate and export one D2 pilot or controlled backend check."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_dataset import export


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--raw', type=Path, default=ROOT / '.artifacts/d2/raw.jsonl')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--results', type=Path, required=True, help='Pilot results list, with workflow_id')
    p.add_argument('--manifest', type=Path, default=ROOT / '.artifacts/d2/run-manifest.json')
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text())
    workflows = [r['workflow_id'] for r in json.loads(args.results.read_text())]
    result = export(args.raw, args.output, manifest['compatibility_id'], workflow_ids=workflows)
    print(json.dumps({k: v for k, v in result.items() if k not in {'output_sha256', 'lookup_duration_ns'}}))


if __name__ == '__main__':
    main()
