"""Capture Codex's HTTP request against a rejecting loopback endpoint.

This is client protocol discovery only: no generated output, tools, inference,
cache lookup, or U/D/C data. Private request bodies stay in --output.
"""
import argparse
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    work = args.output / 'empty-workspace'
    work.mkdir()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if self.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            target = args.output / f'request-{len(requests)}.json'
            target.write_bytes(body)
            target.chmod(0o600)
            try:
                data = json.loads(body)
                detail = {'keys': sorted(data), 'stream': data.get('stream'),
                          'model': data.get('model'),
                          'tools': [{'type': t.get('type'), 'name': t.get('name')}
                                    for t in data.get('tools', [])]}
            except (ValueError, TypeError):
                detail = {'body_not_json': True}
            requests.append({'method': 'POST', 'path': self.path,
                             'body_sha256': hashlib.sha256(body).hexdigest(),
                             'body_bytes': len(body), **detail})
            error = b'{"error":{"message":"stage0 protocol capture only; inference unavailable","type":"stage0_unavailable"}}'
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(error)))
            self.end_headers()
            self.wfile.write(error)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    provider = 'model_providers.leasepath_stage0.'
    command = ['codex', 'exec', '--ignore-user-config', '--ephemeral', '--json',
               '--skip-git-repo-check', '-s', 'read-only', '-C', str(work.resolve()),
               '-m', 'Qwen2.5-Coder-32B-Instruct',
               '-c', 'model_provider="leasepath_stage0"',
               '-c', provider + 'name="LeasePath stage0 capture"',
               '-c', provider + f'base_url="http://127.0.0.1:{server.server_port}/v1"',
               '-c', provider + 'wire_api="responses"',
               '-c', provider + 'requires_openai_auth=false',
               '-c', provider + 'request_max_retries=0',
               '-c', provider + 'stream_max_retries=0',
               'Reply with OK. Do not run tools.']
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=40,
                                env={**os.environ, 'NO_PROXY': '127.0.0.1,localhost',
                                     'no_proxy': '127.0.0.1,localhost'})
        returncode = result.returncode
        (args.output / 'codex.stdout.jsonl').write_text(result.stdout)
        (args.output / 'codex.stderr.log').write_text(result.stderr)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    summary = {'scope': 'client_protocol_capture_only_not_agent_or_backend_acceptance',
               'command': command, 'codex_returncode': returncode,
               'intentional_server_status': 503, 'requests': requests,
               'real_inference_requests': 0, 'backend_lookup_events': 0,
               'tool_loop_verified': False}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))
    raise SystemExit(0 if requests else 1)


if __name__ == '__main__':
    main()
