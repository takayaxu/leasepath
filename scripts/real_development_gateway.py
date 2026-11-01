"""Loopback recording proxy with explicit request IDs; native Responses first.

Stores original/forwarded requests and raw SSE locally. It neither fabricates
model outputs nor executes tools. Optional function-only mode is an explicit
protocol adaptation whose discarded tool schemas are recorded.
"""
import argparse
import gzip
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.real_observer import Observer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--port', type=int, default=8203)
    parser.add_argument('--backend-port', type=int, default=8202)
    parser.add_argument('--function-only', action='store_true')
    parser.add_argument('--owner', default='codex-integration-smoke')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    log = Observer(args.output / 'gateway.jsonl', run_id=args.run_id, source='protocol')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != '/v1/responses':
                self.send_error(404)
                return
            started = time.monotonic_ns()
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if self.headers.get('Content-Encoding') == 'gzip':
                body = gzip.decompress(body)
            request = json.loads(body)
            rid = 'resp_lp_' + uuid.uuid4().hex
            target = args.output / rid
            target.mkdir(mode=0o700)
            (target / 'original.json').write_bytes(body)
            request['model'] = 'leasepath-qwen25-coder32b'
            request['request_id'] = rid
            removed = []
            if args.function_only:
                removed = [t for t in request.get('tools', []) if t['type'] != 'function']
                request['tools'] = [t for t in request.get('tools', []) if t['type'] == 'function']
                request.pop('reasoning', None)
                request['temperature'] = 0
                request['max_output_tokens'] = 1024
            payload = json.dumps(request).encode()
            (target / 'forwarded.json').write_bytes(payload)
            log.emit('request_ready', request_id=rid, received_ns=started,
                     owner=args.owner, context_version='initial',
                     client_request_sha256=hashlib.sha256(body).hexdigest(),
                     forwarded_sha256=hashlib.sha256(payload).hexdigest(),
                     adaptation='function_only_no_reasoning' if args.function_only else 'ID_and_model_only',
                     removed_tool_types=[t['type'] for t in removed],
                     adapter_preparation_ns=time.monotonic_ns() - started)
            conn = http.client.HTTPConnection('127.0.0.1', args.backend_port, timeout=300)
            try:
                conn.request('POST', '/v1/responses', body=payload,
                             headers={'Content-Type': 'application/json', 'X-Request-Id': rid})
                response = conn.getresponse()
                log.emit('response_headers', request_id=rid, status=response.status)
                self.send_response(response.status)
                self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                self.send_header('Connection', 'close')
                self.end_headers()
                with (target / 'response.raw').open('wb') as saved:
                    while True:
                        data = response.readline()
                        if not data:
                            break
                        saved.write(data)
                        self.wfile.write(data)
                        self.wfile.flush()
                log.emit('response_delivered', request_id=rid, status=response.status,
                         elapsed_ns=time.monotonic_ns() - started)
            except (OSError, http.client.HTTPException) as exc:
                log.emit('protocol_error', request_id=rid, error=str(exc))
            finally:
                conn.close()
                self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(json.dumps({'port': args.port, 'function_only': args.function_only}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log.finish()


if __name__ == '__main__':
    main()
