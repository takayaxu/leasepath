"""Run isolated Codex/32B acceptance with streaming CLI event observations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.real_observer import Observer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--prompt-file', type=Path, required=True)
    parser.add_argument('--gateway-port', type=int, default=8203)
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--python-bin', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    args.work.mkdir(parents=True, exist_ok=True)
    log = Observer(args.output / 'runner.jsonl', run_id=args.run_id, source='codex_runner')
    command = ['codex', 'exec', '--ignore-user-config', '--ephemeral', '--json',
               '--skip-git-repo-check', '-s', 'danger-full-access', '-C', str(args.work.absolute()),
               '-m', 'leasepath-qwen25-coder32b', '-c', 'model_provider="leasepath_stage0"',
               '-c', 'model_providers.leasepath_stage0.name="LeasePath 32B acceptance"',
               '-c', f'model_providers.leasepath_stage0.base_url="http://127.0.0.1:{args.gateway_port}/v1"',
               '-c', 'model_providers.leasepath_stage0.wire_api="responses"',
               '-c', 'model_providers.leasepath_stage0.requires_openai_auth=false',
               '-c', 'model_providers.leasepath_stage0.request_max_retries=0',
               '-c', 'model_providers.leasepath_stage0.stream_max_retries=0',
               '-c', 'model_context_window=16384', '-c', 'model_reasoning_effort="none"',
               '-c', 'features.multi_agent=false', '-c', 'web_search="disabled"',
               '-o', str((args.output / 'final.txt').absolute()), '-']
    prompt = args.prompt_file.read_text()
    (args.output / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    (args.output / 'prompt.txt').write_text(prompt)
    env = {**os.environ, 'PYTHONPATH': str(args.work.absolute() / 'src'),
           'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost',
           'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
           'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
    if args.python_bin:
        env['PATH'] = str(args.python_bin.absolute()) + ':' + env['PATH']
    log.emit('session_open', owner=args.output.name, context_version='initial', agent='Codex CLI',
             prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
    start = time.monotonic()
    import threading
    with (args.output / 'stderr.log').open('w') as stderr, (args.output / 'events.jsonl').open('w') as events:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr,
                                   text=True, env=env, start_new_session=True)
        import signal
        timed_out = []
        def terminate():
            timed_out.append(True)
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        timer = threading.Timer(args.timeout, terminate)
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
                    log.emit('non_json_stdout', line=line)
                    continue
                log.emit('cli_event_delivered', event=event,
                         timestamp_semantics='runner_receipt_of_CLI_event_not_backend_or_exact_shell_start')
            code = process.wait(timeout=15)
        finally:
            timer.cancel()
    log.emit('context_retire', owner=args.output.name, reason='ephemeral_Codex_process_exited',
             returncode=code, timed_out=bool(timed_out))
    log.finish()
    result = {'returncode': code, 'timed_out': bool(timed_out), 'elapsed_s': time.monotonic() - start,
              'agent': 'Codex CLI', 'model': 'Qwen2.5-Coder-32B-Instruct',
              'scope': 'integration_attempt; task correctness and backend mapping independently evaluated'}
    (args.output / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    raise SystemExit(0 if code == 0 else 1)


if __name__ == '__main__':
    main()
