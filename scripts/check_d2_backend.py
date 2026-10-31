"""Controlled real inference: cold miss, warm hit, reset/miss U, and C boundary."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_raw import writer
from leasepath.d2_dataset import export


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, default=ROOT / '.artifacts/d2/backend-check')
    args = p.parse_args()
    if args.output.exists():
        raise ValueError('choose a fresh check output directory')
    config = json.loads((ROOT / 'configs/prediction/d2_first_round.json').read_text())
    manifest = json.loads((ROOT / '.artifacts/d2/run-manifest.json').read_text())
    base = f"http://{config['host']}:{config['port']}"
    log = writer(str(ROOT / '.artifacts/d2/raw.jsonl'))

    def post(path, data=None):
        request = urllib.request.Request(base + path, data=json.dumps(data or {}).encode(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read()
            return json.loads(body) if body else None

    post('/reset_prefix_cache')
    workflow = 'controlled-' + uuid.uuid4().hex
    log.emit('workflow_submit', workflow_id=workflow, task_id='backend-contract-check',
             source_group='controlled_backend_check', evidence_scope='controlled_real_inference',
             compatibility_id=manifest['compatibility_id'])
    # The repeated input is solely for the backend check, never a natural task.
    messages = [{'role': 'system', 'content': 'You are a concise coding assistant.'},
                {'role': 'user', 'content': ('Review this Python code without executing it.\n'
                    + '\n'.join(f'def function_{n}(x): return x + {n}' for n in range(80))
                    + '\nReply with one short sentence.')}]
    request_ids = []
    for turn in range(3):
        if turn == 2:
            post('/reset_prefix_cache')
            log.emit('cache_reset', workflow_id=workflow, reason='controlled_miss_check')
        rid = uuid.uuid4().hex
        request_ids.append(rid)
        log.emit('request_ready', request_id=rid, workflow_id=workflow,
                 owner=workflow, context_version=f'check-{turn}')
        post('/v1/chat/completions', {'model': config['served_model_name'], 'messages': messages,
                                     'request_id': rid, 'temperature': 0, 'max_tokens': 32, 'seed': 23})
    # End a complete observation while the third relationship is still open.
    # Close it only after the boundary, rather than manufacturing a D before it.
    log.emit('workflow_observation_end', workflow_id=workflow)
    log.emit('context_close', workflow_id=workflow, reason='controller_closed_after_observation')
    log.flush()
    # Server writes are asynchronous; bounded polling waits for its last receipt.
    deadline = time.monotonic() + 10
    raw_path = ROOT / '.artifacts/d2/raw.jsonl'
    while time.monotonic() < deadline:
        records = [json.loads(line) for line in raw_path.read_text().splitlines()]
        if any(r['kind'] == 'request_release' and request_ids[-1] in r['request_id'] for r in records):
            break
        time.sleep(0.1)
    result = export(raw_path, args.output, manifest['compatibility_id'], workflow_ids=[workflow])
    lookup = [r for r in records if r['kind'] == 'cache_lookup' and any(k in r['request_id'] for k in request_ids)]
    by_id = {next(k for k in request_ids if k in r['request_id']): r for r in lookup}
    assert by_id[request_ids[0]]['cached_tokens'] == 0
    assert by_id[request_ids[1]]['cached_tokens'] > 0
    assert by_id[request_ids[2]]['cached_tokens'] == 0
    labels = [json.loads(line) for line in (args.output / 'episode_labels.jsonl').read_text().splitlines()]
    assert any(l['demand_request_id'] == request_ids[2] and l['outcome'] == 'legal_demand' for l in labels)
    assert result['outcomes'] == {'legal_demand': 2, 'right_censored': 1}
    (args.output / 'checks.json').write_text(json.dumps({
        'status': 'passed', 'workflow_id': workflow,
        'cold_cached_tokens': 0, 'warm_cached_tokens': by_id[request_ids[1]]['cached_tokens'],
        'reset_cached_tokens': 0, 'miss_is_legal_demand': True,
        'open_relation_is_censored_at_complete_boundary': True,
        'scope': 'controlled real inference; excluded from autonomous pilot',
    }, indent=2) + '\n')
    print(json.dumps({'status': 'passed', 'outcomes': result['outcomes']}))


if __name__ == '__main__':
    main()
