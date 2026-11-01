"""Controlled TP lifecycle stress. Synthetic prompts, real inference/KV actions.

Capacity pressure comes from real concurrent prefill/decode, without sleeps or
fake tool durations. None of these inputs counts as a natural task sample.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import socket
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.real_observer import Observer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.run / 'service-manifest.json').read_text())
    port = manifest['port']
    base = f'http://127.0.0.1:{port}'
    log = Observer(args.output / 'controller.jsonl', run_id=args.run.name, source='controlled_lifecycle')
    results = []

    def post(path, data):
        request = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}

    def complete(tokens, case, generated=16, owner=None):
        rid = uuid.uuid4().hex
        owner = owner or case
        log.emit('request_ready', owner=owner, context_version='initial', request_id=rid, case=case)
        payload = {'model': 'leasepath-qwen25-coder32b', 'prompt': tokens,
                   'request_id': rid, 'max_tokens': generated, 'temperature': 0,
                   'ignore_eos': True, 'seed': 23}
        start = time.monotonic()
        data = post('/v1/completions', payload)
        result = {'request_id': rid, 'case': case, 'owner': owner,
                  'usage': data.get('usage'), 'elapsed_s': time.monotonic() - start}
        log.emit('client_response', **result)
        results.append(result)
        (args.output / f'{rid}.response.json').write_text(json.dumps(data) + '\n')
        return result

    try:
        post('/reset_prefix_cache', {})
        # 16,368 prompt + 16 generated tokens = the actual 16K context budget.
        full_context = [1000 + n % 100 for n in range(16368)]
        complete(full_context, 'context_16K', owner='context-check')
        post('/reset_prefix_cache', {})
        common = [1100 + n % 100 for n in range(4096)]
        complete(common + [600], 'shared_warmup', 16, owner='warmup')
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda n: complete(common + [700 + n] * 128,
                                             f'shared_{n}', 128, owner=f'shared-owner-{n}'), range(2)))
        # Explicitly closing owner A cannot close owner B's shared prefix.
        log.emit('context_retire', owner='shared-owner-0', context_version='initial', reason='controlled_owner_A_close')
        complete(common + [701] * 128, 'shared_owner_B_continues', owner='shared-owner-1')
        post('/reset_prefix_cache', {})
        for count in [2, 4, 8]:
            with ThreadPoolExecutor(max_workers=count) as pool:
                list(pool.map(lambda n: complete([1200 + n] * 1024,
                                                 f'concurrency_{count}_{n}', 16), range(count)))
        post('/reset_prefix_cache', {})
        # Two 14K inputs exceed 24K capacity and may cause real preemption.
        long_inputs = [[1300 + n] + [1400 + i % 100 for i in range(14079)] for n in range(2)]
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda n: complete(long_inputs[n], f'pressure_{n}', 128,
                                             owner=f'pressure-owner-{n}'), range(2)))
        # Re-request prefix 0 after actual competing allocations. Whether it
        # missed is determined by engine evidence, never assumed here.
        complete(long_inputs[0], 'after_capacity_competition', owner='pressure-owner-0')
        log.emit('context_version_change', owner='pressure-owner-0', old='initial', new='compact',
                 reason='controlled_explicit_old_version_discard')
        log.emit('context_retire', owner='pressure-owner-0', context_version='initial', reason='controlled_explicit_old_version_discard')
        complete([1500] * 1024, 'compacted_input', owner='compact-owner')
        # Restore an open relation by sending precisely the same input; cache
        # miss/hit does not affect whether its real next lookup is demand.
        complete(long_inputs[1], 'restored_input', owner='pressure-owner-1')
        rid = uuid.uuid4().hex
        log.emit('request_ready', owner='cancel-owner', request_id=rid, case='client_cancel')
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=300)
        payload = {'model': 'leasepath-qwen25-coder32b', 'prompt': [1600] * 512,
                   'request_id': rid, 'max_tokens': 2048, 'ignore_eos': True,
                   'temperature': 0, 'stream': True}
        conn.request('POST', '/v1/completions', body=json.dumps(payload), headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        first = response.readline()
        log.emit('cancel_requested', request_id=rid, first_stream_line=first.decode(errors='replace'), status=response.status)
        if conn.sock:
            conn.sock.shutdown(socket.SHUT_RDWR)
        response.close()
        conn.close()
        # A following genuine request is a backend drain/health check, with no
        # artificial wait inserted into any task trajectory.
        complete([1700] * 512, 'after_cancel_health')
        log.emit('observation_end', owner='pressure-owner-1', relation_still_open=True,
                 backend_integrity_requires_independent_stream_validation=True)
        log.emit('context_retire', owner='pressure-owner-1', context_version='initial', reason='controller_close_after_observation')
    finally:
        log.finish()
        (args.output / 'summary.json').write_text(json.dumps({
            'scope': 'controlled_real_inference_synthetic_prompts_not_task_occurrence',
            'results': results, 'status': 'requires_independent_backend_audit'}, indent=2) + '\n')
    print(json.dumps({'completed_requests': len(results)}))


if __name__ == '__main__':
    main()
