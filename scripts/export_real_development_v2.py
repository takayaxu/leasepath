import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from leasepath.real_projection_v2 import export_v2

parser=argparse.ArgumentParser(description='Separate v2 lifecycle projection with all-request coverage')
parser.add_argument('--run',type=Path,required=True)
parser.add_argument('--clients',type=Path,nargs='+',required=True)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--legacy',action='store_true')
args=parser.parse_args()
r=export_v2(args.run,args.clients,args.output,legacy=args.legacy)
print(json.dumps({k:r[k] for k in ['version','backend_requests','coverage_status','outcomes','unresolved_relations','all_lookup_events_retained']}))
