"""Bounded identical inference workload for separate probe-on/off services.

This measures wall time including HTTP and logging, not device kernel time.
No issue tasks, simulated lookup, or training labels are generated here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=4)
    args = parser.parse_args()
    manifest = json.loads((args.run / 'service-manifest.json').read_text())
    base = f"http://127.0.0.1:{manifest['port']}"
    output = args.run / 'overhead-workload.json'
    if output.exists():
        raise ValueError('preserve previous workload attempts')
    rows = []

    def post(path, payload):
        req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
        start = time.monotonic_ns()
        with urllib.request.urlopen(req, timeout=300) as response:
            body = response.read()
        return json.loads(body) if body else {}, (time.monotonic_ns() - start) / 1e9

    def request(size, phase, repeat):
        tokens = [1700 + i % 100 for i in range(size)]
        payload = {'model': 'leasepath-qwen25-coder32b', 'prompt': tokens,
                   'max_tokens': 32, 'ignore_eos': True, 'temperature': 0, 'seed': 23,
                   'request_id': uuid.uuid4().hex}
        result, elapsed = post('/v1/completions', payload)
        rows.append({'size': size, 'phase': phase, 'repeat': repeat, 'elapsed_s': elapsed,
                     'request_id': payload['request_id'], 'usage': result['usage'],
                     'input_sha256': hashlib.sha256(json.dumps(tokens).encode()).hexdigest(),
                     'output_sha256': hashlib.sha256(result['choices'][0]['text'].encode()).hexdigest()})
        print(json.dumps(rows[-1]), flush=True)

    request(512, 'warmup_excluded', -1)
    for repeat in range(args.repeats):
        for size in [512, 2048, 4096]:
            post('/reset_prefix_cache', {})
            request(size, 'cold', repeat)
            request(size, 'warm', repeat)
    summary = {'probe_mode': manifest['probe_mode'], 'devices': manifest['devices'],
               'scope': 'synthetic_real_inference_HTTP_wall_time_including_logging', 'records': rows,
               'total_raw_log_bytes': sum(p.stat().st_size for p in (args.run / 'raw').glob('*.jsonl'))}
    output.write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
