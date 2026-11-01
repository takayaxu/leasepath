"""Bounded Codex CLI session with a recording, function-only Chat bridge.

The CLI executes tools. This process forwards only validated model-generated
calls; it does not create commands or repair candidates. Unsupported requests
fail explicitly. Full evidence stays in a new data-disk directory.
"""
import argparse
import gzip
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.codex_protocol import (ChatStream, ProtocolError, parse_strict_text,
                                     response_events, strict_json, strict_text_request, to_chat)
from leasepath.real_observer import Observer


def run_session(output, work, prompt, config, env, *, run_id, owner):
    output, work = Path(output).resolve(), Path(work).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / 'tool-intents').mkdir()
    (output / 'tool-execution').mkdir()
    log = Observer(output / 'gateway.jsonl', run_id=run_id, source='codex32b_gateway')
    log.emit('context_open', owner=owner, context_version='initial',
             agent='Codex CLI', adaptation=config['version'])
    lock, cancelled = threading.Lock(), threading.Event()
    state = {'requests': 0, 'forwarded': 0, 'errors': [], 'tool_calls': 0,
             'tool_results_returned': 0, 'body_hashes': set()}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            request_id = uuid.uuid4().hex
            target = output / ('request-' + request_id)
            target.mkdir()
            backend = None
            started = time.monotonic_ns()
            try:
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                if self.headers.get('Content-Encoding') == 'gzip':
                    body = gzip.decompress(body)
                (target / 'original.json').write_bytes(body)
                if self.path != '/v1/responses':
                    raise ProtocolError('unsupported path/context operation: ' + self.path)
                request = strict_json(body)
                hashed = hashlib.sha256(body).hexdigest()
                with lock:
                    state['requests'] += 1
                    if state['requests'] > config['max_requests'] or hashed in state['body_hashes']:
                        raise ProtocolError('request budget exceeded or implicit retry')
                    state['body_hashes'].add(hashed)
                payload = to_chat(request, model=config['model'], request_id=request_id,
                                  max_tokens=config['output_tokens'])
                original_tools = payload['tools']
                if config.get('tool_protocol') == 'strict-json-envelope-v1':
                    payload = strict_text_request(payload)
                (target / 'forwarded.json').write_text(json.dumps(payload, ensure_ascii=False) + '\n')
                log.emit('client_attempt_received', owner=owner, context_version='initial',
                         request_id=request_id, path=self.path,
                         client_request_id=self.headers.get('X-Request-Id'),
                         original_sha256=hashed, adapter_version=config['version'])
                # This is a non-generating backend render. Save its complete IDs;
                # compare them with actual engine enqueue after the run.
                backend = http.client.HTTPConnection('127.0.0.1', config['backend_port'], timeout=30)
                token_payload = {k: payload[k] for k in ['model', 'messages', 'tools'] if k in payload}
                backend.request('POST', '/tokenize', json.dumps(token_payload), {'Content-Type': 'application/json'})
                token_response = backend.getresponse()
                token_body = token_response.read()
                (target / 'tokenize.json').write_bytes(token_body)
                if token_response.status != 200:
                    raise ProtocolError('backend tokenize failed: ' + token_body.decode(errors='replace'))
                tokens = strict_json(token_body)['tokens']
                if len(tokens) + config['output_tokens'] > config['context_tokens']:
                    raise ProtocolError('context budget exhausted before generation')
                if cancelled.is_set():
                    raise ProtocolError('session cancelled before enqueue')
                prepped = time.monotonic_ns()
                outputs = [x for x in request['input'] if x.get('type') == 'function_call_output']
                state['tool_results_returned'] += len(outputs)
                log.emit('request_ready', owner=owner, context_version='initial',
                         request_id=request_id, logical_request_id=request_id,
                         attempt_id=request_id, retry_of=None, purpose='demand', wire_api='chat',
                         frontend_wire_api='responses', input_tokens=len(tokens),
                         reserved_output=config['output_tokens'], returned_tool_call_ids=[x['call_id'] for x in outputs],
                         adapter_preparation_ns=prepped - started)
                backend.request('POST', '/v1/chat/completions', json.dumps(payload),
                                {'Content-Type': 'application/json', 'X-Request-Id': request_id})
                state['forwarded'] += 1
                response = backend.getresponse()
                if response.status != 200:
                    data = response.read()
                    (target / 'backend-error.raw').write_bytes(data)
                    raise ProtocolError('backend HTTP ' + str(response.status) + ': ' + data.decode(errors='replace'))
                stream = ChatStream(payload.get('tools', []))
                with (target / 'backend.sse').open('wb') as raw:
                    for line in response:
                        raw.write(line)
                        if cancelled.is_set():
                            raise ProtocolError('session cancelled during generation; backend stream closed')
                        if line.startswith(b'data: '):
                            stream.feed(line[6:].decode().strip())
                            if stream.done:
                                break
                generated = time.monotonic_ns()
                stream.validate()
                if config.get('tool_protocol') == 'strict-json-envelope-v1':
                    stream = parse_strict_text(stream, original_tools, request_id)
                for call in stream.calls.values():
                    if call['name'] == 'exec_command':
                        arguments = strict_json(call['arguments'])
                        if arguments.get('shell', '/bin/bash') not in {'bash', '/bin/bash'}:
                            raise ProtocolError('only instrumented non-login bash is supported')
                        (output / 'tool-intents' / (hashlib.sha256(call['id'].encode()).hexdigest() + '.json')).write_text(
                            json.dumps({**call, 'owner': owner, 'run_id': run_id}) + '\n')
                # The backend has finished before any executable event is sent.
                events = response_events(stream, 'resp_' + request_id, config['model'])
                (target / 'normalized.json').write_text(json.dumps(events, ensure_ascii=False) + '\n')
                for call in stream.calls.values():
                    log.emit('model_tool_call', owner=owner, request_id=request_id, **call)
                state['tool_calls'] += len(stream.calls)
                transformed = time.monotonic_ns()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                with (target / 'responses.sse').open('wb') as saved:
                    for event in events:
                        line = ('event: ' + event['type'] + '\ndata: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode()
                        saved.write(line)
                        self.wfile.write(line)
                        self.wfile.flush()
                log.emit('client_response', owner=owner, request_id=request_id, status=200,
                         finish_reason=stream.finish, usage=stream.usage,
                         backend_and_buffer_ns=generated - prepped,
                         validation_and_conversion_ns=transformed - generated,
                         adapter_preparation_ns=prepped - started)
            except Exception as exc:
                error = {'type': type(exc).__name__, 'message': str(exc), 'path': self.path,
                         'request_id': request_id, 'retry_attempted_by_gateway': False}
                state['errors'].append(error)
                (target / 'error.json').write_text(json.dumps(error, ensure_ascii=False) + '\n')
                log.emit('client_error', owner=owner, **error)
                try:
                    data = json.dumps({'error': error}).encode()
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(data)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass
            finally:
                if backend:
                    backend.close()
                self.close_connection = True

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    prefix = 'model_providers.leasepath_codex32b.'
    command = [config['cli'], 'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral', '--json',
               '--skip-git-repo-check', '-s', 'danger-full-access', '-C', str(work),
               '-m', config['model'], '-c', 'approval_policy="never"',
               '-c', 'model_provider="leasepath_codex32b"',
               '-c', prefix + 'name="LeasePath local 32B"',
               '-c', prefix + f'base_url="http://127.0.0.1:{server.server_port}/v1"',
               '-c', prefix + 'wire_api="responses"', '-c', prefix + 'requires_openai_auth=false',
               '-c', prefix + 'request_max_retries=0', '-c', prefix + 'stream_max_retries=0',
               '-c', 'model_catalog_json=' + json.dumps(config['model_catalog']),
               '-c', 'model_context_window=16384', '-c', 'model_auto_compact_token_limit=15000',
               '-c', 'model_reasoning_effort="none"', '-c', 'features.multi_agent=false',
               '-c', 'web_search="disabled"', '-c', 'features.shell_snapshot=false',
               '-c', 'shell_environment_policy.inherit="all"',
               '-c', 'shell_environment_policy.ignore_default_excludes=true',
               '-o', str(output / 'final.txt'), '-']
    (output / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    (output / 'prompt.txt').write_text(prompt)
    (output / 'config.json').write_text(json.dumps(config, indent=2) + '\n')
    env = {**env, 'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost',
           'LEASEPATH_CODEX_EXECUTION_ROOT': str(output), 'BASH_ENV': config['shell_bootstrap']}
    started = time.monotonic()
    process = None
    timed_out = []
    def terminate():
        timed_out.append(True)
        cancelled.set()
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    try:
        with (output / 'stderr.log').open('w') as stderr, (output / 'events.jsonl').open('w') as events:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=stderr, text=True, env=env, start_new_session=True)
            timer = threading.Timer(config['wall_seconds'], terminate)
            timer.start()
            try:
                process.stdin.write(prompt)
                process.stdin.close()
                for line in process.stdout:
                    events.write(line)
                    events.flush()
                    try:
                        event = json.loads(line)
                    except ValueError:
                        event = {'unparsed': line}
                    log.emit('cli_event_received', owner=owner, event=event,
                             timestamp_semantics='receipt_only_not_tool_start_or_process_completion')
                code = process.wait(timeout=15)
            finally:
                timer.cancel()
                if process.poll() is None:
                    terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
    finally:
        cancelled.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        log.emit('context_retire', owner=owner, context_version='initial', reason='ephemeral_CLI_exited')
        log.finish()
    result = {k: v for k, v in state.items() if k != 'body_hashes'}
    result.update(returncode=code, timed_out=bool(timed_out), elapsed_s=time.monotonic() - started,
                  scope='integration_only_independent_correctness_and_backend_mapping_required')
    (output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['output', 'work', 'prompt', 'config', 'environment']:
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--owner', required=True)
    args = parser.parse_args()
    print(json.dumps(run_session(args.output, args.work, args.prompt.read_text(),
          json.loads(args.config.read_text()), json.loads(args.environment.read_text()),
          run_id=args.run_id, owner=args.owner)), flush=True)


if __name__ == '__main__':
    main()
