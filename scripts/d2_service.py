"""Start/status/stop the isolated D2 service; leave existing Qwen services alone."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.artifacts/d2'
CONFIG = ROOT / 'configs/prediction/d2_first_round.json'


def runtime_env(config):
    result = subprocess.run(['bash', '-c', 'source "$1" && env -0', 'd2-runtime',
                             config['runtime_environment']], capture_output=True, check=True)
    env = dict(entry.decode().split('=', 1) for entry in result.stdout.split(b'\0') if b'=' in entry)
    env['PYTHONPATH'] = ':'.join([str(STATE / 'runtime-plugin'), str(ROOT / 'src'), env.get('PYTHONPATH', '')])
    env['ASCEND_RT_VISIBLE_DEVICES'] = str(config['device'])
    env['ASCEND_VISIBLE_DEVICES'] = str(config['device'])
    env['VLLM_PLUGINS'] = 'ascend,leasepath_d2'
    env['PYTHONHASHSEED'] = '23'
    env['VLLM_CACHE_ROOT'] = str(STATE / 'vllm-cache')
    env['TRITON_CACHE_DIR'] = str(STATE / 'triton-cache')
    env['LEASEPATH_D2_RAW'] = str(STATE / 'raw.jsonl')
    env['VLLM_SERVER_DEV_MODE'] = '1' if config['enable_dev_cache_controls'] else '0'
    return env


def command(config):
    return [config['runtime_python'], '-m', 'vllm.entrypoints.openai.api_server',
            '--model', str((ROOT / config['model_directory']).resolve()),
            '--served-model-name', config['served_model_name'],
            '--host', config['host'], '--port', str(config['port']),
            '--dtype', config['dtype'], '--tensor-parallel-size', '1',
            '--max-model-len', str(config['max_model_len']),
            '--max-num-seqs', str(config['max_num_seqs']),
            '--max-num-batched-tokens', str(config['max_num_batched_tokens']),
            '--block-size', str(config['block_size']),
            '--gpu-memory-utilization', str(config['memory_utilization']),
            '--seed', '23', '--enable-prefix-caching', '--enable-chunked-prefill',
            '--generation-config', 'vllm', '--enforce-eager', '--no-async-scheduling']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'status', 'stop'])
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    STATE.mkdir(parents=True, exist_ok=True)
    pid_file = STATE / 'server.pid'
    if args.action == 'status':
        try:
            with urllib.request.urlopen(f"http://{config['host']}:{config['port']}/health", timeout=3) as r:
                print(json.dumps({'healthy': r.status == 200, 'pid': pid_file.read_text().strip()}))
        except Exception as exc:
            print(json.dumps({'healthy': False, 'error': str(exc)}))
            raise SystemExit(1)
        return
    if pid_file.exists():
        pid = int(pid_file.read_text())
        proc = Path(f'/proc/{pid}/cmdline')
        alive = proc.exists() and b'vllm.entrypoints.openai.api_server' in proc.read_bytes() and str(config['port']).encode() in proc.read_bytes()
        if alive:
            if args.action == 'stop':
                os.killpg(pid, signal.SIGTERM)
                print(json.dumps({'stopping': pid}))
                return
            raise SystemExit(f'D2 process already running: {pid}')
    if args.action == 'stop':
        return
    model = ROOT / config['model_directory']
    receipt = json.loads((model / 'download-complete.json').read_text())
    assert receipt['verified'] and receipt['hf_revision'] == config['model_revision']
    env = runtime_env(config)
    fingerprint = {
        'configuration': config, 'model_sha256': receipt['sha256'],
        'probe_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (ROOT / 'src/leasepath').glob('d2_*.py')},
    }
    runtime = subprocess.run([config['runtime_python'], '-c',
        'import importlib.metadata as m,json; print(json.dumps({p:m.version(p) for p in '
        '["vllm","vllm-ascend","torch","torch-npu","transformers"]}))'],
        env=env, capture_output=True, text=True, check=True)
    fingerprint['runtime_versions'] = json.loads(runtime.stdout)
    compatibility = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    env['LEASEPATH_D2_COMPATIBILITY'] = compatibility
    fingerprint['compatibility_id'] = compatibility
    fingerprint['command'] = command(config)
    (STATE / 'run-manifest.json').write_text(json.dumps(fingerprint, indent=2) + '\n')
    # Install only the general-plugin metadata/package into an isolated target.
    subprocess.run([str(ROOT / '.venv/bin/python'), '-m', 'pip', 'install', '--no-deps', '--no-build-isolation',
                    '--upgrade', '--target', str(STATE / 'runtime-plugin'), str(ROOT)],
                   env={**os.environ, 'PYTHONPATH': ''}, check=True, stdout=sys.stderr)
    with (STATE / 'server.log').open('ab') as log:
        process = subprocess.Popen(command(config), cwd=ROOT, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    pid_file.write_text(str(process.pid) + '\n')
    print(json.dumps({'pid': process.pid, 'log': str(STATE / 'server.log'), 'port': config['port']}))


if __name__ == '__main__':
    main()
