"""Export the new pilot only after complete backend and owner evidence checks."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from leasepath.real_dataset import export

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run', type=Path, required=True)
parser.add_argument('--pilot', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--controlled', action='store_true', help='Export explicit controlled fork owners separately')
args = parser.parse_args()
summary = export(args.run, args.pilot, args.output, controlled=args.controlled)
print(json.dumps({k: summary[k] for k in ['owners', 'requests', 'episodes', 'outcomes']}))
