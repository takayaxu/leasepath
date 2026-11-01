"""Controlled real TP inference checks, separate from repository task incidence."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
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
    parser.add_argument('--concurrency', type=int, nargs='+', default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.run / 'service-manifest.json').read_text())
    base = f"http://127.0.0.1:{manifest['port']}"
    log = Observer(args.output / 'controller.jsonl', run_id=args.run.name, source='controlled_runner')
    owner = 'controlled-' + uuid.uuid4().hex
    log.emit('owner_open', owner=owner, context_version='v1', scope='controlled_real_inference')

    def post(path, payload):
        req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=300) as response:
            data = response.read()
            return json.loads(data) if data else {}

    results = []
    def completion(messages, label, max_tokens=32):
        rid = 'check_' + uuid.uuid4().hex
        payload = {'model': 'leasepath-qwen25-coder32b', 'messages': messages,
                   'request_id': rid, 'max_tokens': max_tokens, 'temperature': 0, 'seed': 23}
        log.emit('request_ready', owner=owner, context_version='v1', request_id=rid, case=label)
        start = time.monotonic_ns()
        data = post('/v1/chat/completions', payload)
        elapsed = time.monotonic_ns() - start
        log.emit('client_response', request_id=rid, response_id=data['id'], usage=data.get('usage'), elapsed_ns=elapsed)
        record = {'case': label, 'request_id': rid, 'response_id': data['id'],
                  'usage': data.get('usage'), 'elapsed_s': elapsed / 1e9,
                  'finish_reason': data['choices'][0]['finish_reason']}
        results.append(record)
        (args.output / (rid + '.request.json')).write_text(json.dumps(payload) + '\n')
        (args.output / (rid + '.response.json')).write_text(json.dumps(data) + '\n')
        return record

    try:
        if args.concurrency:
            for count in args.concurrency:
                messages = [{'role': 'user', 'content': 'List ten Python testing practices concisely.'}]
                with ThreadPoolExecutor(max_workers=count) as pool:
                    list(pool.map(lambda n: completion(messages, f'concurrent_{count}_{n}', 64), range(count)))
        else:
            post('/reset_prefix_cache', {})
            messages = [{'role': 'system', 'content': 'You are a concise coding assistant.'},
                        {'role': 'user', 'content': '\n'.join(f'def function_{n}(x): return x + {n}' for n in range(100)) + '\nSummarize this code in one sentence.'}]
            completion(messages, 'cold')
            completion(messages, 'warm')
            post('/reset_prefix_cache', {})
            log.emit('explicit_cache_reset', reason='controlled_reset_miss_not_capacity_eviction')
            completion(messages, 'reset_miss')
            # Close the observation with the owner relationship genuinely open;
            # it is retired only after that boundary by the explicit controller.
            log.emit('observation_end', owner=owner, relation_still_open=True,
                     backend_integrity_requires_independent_stream_validation=True)
        log.emit('context_retire', owner=owner, reason='controller_explicit_close_after_observation')
    finally:
        log.finish()
    (args.output / 'summary.json').write_text(json.dumps({'scope': 'controlled_real_inference_not_natural_task_occurrence',
        'owner': owner, 'results': results, 'backend_checks_status': 'requires_independent_event_audit'}, indent=2) + '\n')
    print(json.dumps({'requests': len(results), 'output': str(args.output)}))


if __name__ == '__main__':
    main()
