import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.trace import read_events, validate_events, derive_idle_labels

parser = argparse.ArgumentParser()
parser.add_argument('trace', type=Path)
parser.add_argument('--labels', type=Path)
args = parser.parse_args()
events = read_events(args.trace)
print(json.dumps(validate_events(events)))
if args.labels:
    args.labels.parent.mkdir(parents=True, exist_ok=True)
    args.labels.write_text(''.join(json.dumps(row)+'\n' for row in derive_idle_labels(events)))
