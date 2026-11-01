"""Supplement controlled coverage with real decode preemption, retry and fork."""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from leasepath.real_observer import Observer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    port = json.loads((args.run / 'service-manifest.json').read_text())['port']
    compatibility = json.loads((args.run / 'service-manifest.json').read_text())['compatibility_id']
    base = f'http://127.0.0.1:{port}'
    log = Observer(args.output / 'controller.jsonl', run_id=args.run.name, source='controlled_recovery')
    results = []

    def post(path, data):
        req = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=300) as response:
            body = response.read()
            return json.loads(body) if body else {}

    def complete(tokens, case, owner, generated=16, retry_of=None):
        rid = uuid.uuid4().hex
        log.emit('request_ready', owner=owner, request_id=rid, case=case, retry_of=retry_of)
        payload = {'model': 'leasepath-qwen25-coder32b', 'prompt': tokens, 'request_id': rid,
                   'max_tokens': generated, 'ignore_eos': True, 'temperature': 0, 'seed': 23}
        start = time.monotonic()
        response = post('/v1/completions', payload)
        record = {'case': case, 'owner': owner, 'request_id': rid, 'usage': response['usage'],
                  'elapsed_s': time.monotonic() - start}
        log.emit('client_response', **record)
        (args.output / (rid + '.response.json')).write_text(json.dumps(response) + '\n')
        results.append(record)
        print(json.dumps(record), flush=True)
        return rid

    try:
        post('/reset_prefix_cache', {})
        # Two admitted 94-block prompts fit; their 256-token decode growth
        # needs 192 blocks plus the reserved null slot, exceeding 192 total.
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda n: complete([1800 + n] + [1900] * 12031,
                 f'decode_pressure_{n}', f'preempt-owner-{n}', 256), range(2)))
        rid = uuid.uuid4().hex
        log.emit('request_ready', owner='retry-owner', request_id=rid, case='cancel_attempt')
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=300)
        payload = {'model': 'leasepath-qwen25-coder32b', 'prompt': [2000] * 512, 'request_id': rid,
                   'max_tokens': 512, 'ignore_eos': True, 'temperature': 0, 'stream': True}
        conn.request('POST', '/v1/completions', body=json.dumps(payload), headers={'Content-Type': 'application/json'})
        response = conn.getresponse()
        first = response.readline()
        log.emit('cancel_requested', owner='retry-owner', request_id=rid, status=response.status,
                 first_stream_line=first.decode(errors='replace'))
        if conn.sock:
            conn.sock.shutdown(socket.SHUT_RDWR)
        response.close()
        conn.close()
        complete([2000] * 512, 'explicit_client_retry_after_cancel', 'retry-owner', retry_of=rid)
        parent = [2100] * 1024
        log.emit('workflow_submit', owner='fork-parent', task_id='controlled-fork-parent',
                 source_group='controlled-fork', agent='controlled_driver', compatibility_id=compatibility)
        complete(parent + [2101], 'fork_parent', 'fork-parent')
        log.emit('context_fork', parent_owner='fork-parent', child_owner='fork-child',
                 inherited_input_token_ids=parent + [2101], reason='controlled_explicit_branch')
        log.emit('workflow_submit', owner='fork-child', task_id='controlled-fork-child',
                 source_group='controlled-fork', agent='controlled_driver', compatibility_id=compatibility)
        complete(parent + [2101, 2102], 'fork_child', 'fork-child')
        log.emit('context_retire', owner='fork-parent', context_version='initial', reason='explicit_parent_close')
        log.emit('observation_end', owner='fork-parent', relation_still_open=False)
        complete(parent + [2101, 2102], 'fork_child_after_parent_close', 'fork-child')
        log.emit('observation_end', owner='fork-child', relation_still_open=True,
                 backend_integrity_requires_independent_stream_validation=True)
        log.emit('context_retire', owner='fork-child', context_version='initial', reason='close_after_observation')
    finally:
        log.finish()
        (args.output / 'summary.json').write_text(json.dumps({'scope': 'controlled_real_inference_not_task_occurrence',
             'results': results, 'status': 'requires_independent_backend_audit'}, indent=2) + '\n')


if __name__ == '__main__':
    main()
