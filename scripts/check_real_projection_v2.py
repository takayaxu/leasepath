"""Independent checks of v2 source references, first demand and coverage."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from leasepath.real_audit import load_records

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,required=True)
parser.add_argument('--dataset',type=Path,required=True)
parser.add_argument('--clients',type=Path,nargs='+',required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
read=lambda p:[json.loads(s) for s in p.read_text().splitlines()]
raw=load_records(args.run/'raw');clients=[r for p in args.clients for r in read(p)]
source={(r['stream_id'],r['seq']):r for r in raw+clients}
labels=read(args.dataset/'labels.v2.jsonl');coverage=read(args.dataset/'coverage.v2.jsonl')
events=read(args.dataset/'events.v2.jsonl');protection=read(args.dataset/'protection.v2.jsonl')
all_backend={r['data']['request_id'] for r in raw if r['kind']=='request_enqueue'}
covered=[rid for item in coverage for rid in item['backend_request_ids']]
assert len(covered)==len(set(covered)) and set(covered)==all_backend
mapping={rid:item for item in coverage for rid in item['backend_request_ids']}
def original(ref):
    row=source[(ref['stream_id'],ref['seq'])];assert row['ts_ns']==ref['ts_ns'];return row
seen=set()
for label in labels:
    assert label['episode_id'] not in seen;seen.add(label['episode_id'])
    start,end=original(label['start_source']),original(label['end_source'])
    assert start['kind']=='request_release' and start['data']['status'] in {'FINISHED_STOPPED','FINISHED_LENGTH_CAPPED'}
    assert start['data']['prefix_token_ids']==label['prefix_token_ids']
    assert label['start_ns']==start['ts_ns'] and label['end_ns']>=label['start_ns']
    matches=[]
    for event in events:
        if event['kind']!='cache_lookup' or event['owner']!=label['owner'] or event['context_version']!=label['context_version']:continue
        r=original(event['source']);at=r['data']['lookup_started_ns']
        enq=next(x for x in raw if x['kind']=='request_enqueue' and x['data']['request_id']==event['backend_request_id'])
        if (label['start_ns']<=at<=label['end_ns'] and enq['data'].get('cache_salt')==label['cache_salt'] and
            enq['data']['prompt_token_ids'][:len(label['prefix_token_ids'])]==label['prefix_token_ids'] and
            True):matches.append((at,r))
    if label['outcome']=='U':
        assert end['kind']=='cache_lookup' and matches and min(t for t,_ in matches)==label['end_ns']
        assert label['demand_backend_request_id']==end['data']['request_id']
    else:
        assert not matches
        assert end['kind']==('context_retire' if label['outcome']=='D' else 'observation_end')
        assert end['data']['owner']==label['owner']
        if label['outcome']=='D':assert end['data']['context_version']==label['context_version']
for interval in protection:
    start,end=original(interval['start_source']),original(interval['end_source'])
    assert start['kind']=='request_enqueue' and end['kind']=='request_release'
    assert interval['start_ns']==start['ts_ns'] and interval['end_ns']==end['ts_ns']
    for ref in interval['preemptions']:
        r=original(ref);assert r['kind']=='preemption_release' and start['ts_ns']<=r['ts_ns']<=end['ts_ns']
result={'status':'passed','backend_requests_accounted':len(all_backend),'labels':len(labels),
        'outcomes':dict(Counter(r['outcome'] for r in labels)),'protection_intervals':len(protection),
        'scope':'independent_source_reference_first_demand_scope_and_full_coverage_checks'}
args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
