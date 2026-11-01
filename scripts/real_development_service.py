"""Start an isolated stage-0 TP service on explicitly selected idle devices."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import urllib.request
from zoneinfo import ZoneInfo

from preflight_real_development import parse_npu, add_process_evidence, resource_gate

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ENV = '/home/ma-user/work/qwen-services/runtime_env.sh'
RUNTIME_PYTHON = '/home/ma-user/anaconda3/envs/PyTorch-2.7.1/bin/python'


def runtime_env(run):
    result = subprocess.run(['bash', '-c', 'source "$1" && env -0', 'stage0', RUNTIME_ENV],
                            capture_output=True, check=True)
    env = dict(s.decode().split('=', 1) for s in result.stdout.split(b'\0') if b'=' in s)
    env['PYTHONPATH'] = ':'.join([str(run / 'plugin'), str(ROOT / 'src'), env.get('PYTHONPATH', '')])
    for key in ['VLLM_CACHE_ROOT', 'TRITON_CACHE_DIR']:
        env[key] = str(run / key.lower())
    env.update(LEASEPATH_REAL_RAW=str(run / 'raw'), LEASEPATH_REAL_RUN=run.name,
               VLLM_PLUGINS='ascend,leasepath_real', PYTHONHASHSEED='23',
               VLLM_SERVER_DEV_MODE='1', OMP_NUM_THREADS='8',
               OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', VLLM_WORKER_MULTIPROC_METHOD='spawn')
    env.pop('LEASEPATH_D2_RAW', None)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start', 'status', 'stop'])
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--devices', nargs='+', type=int, default=[0, 1])
    parser.add_argument('--port', type=int, default=8202)
    parser.add_argument('--max-num-seqs', type=int, default=1)
    parser.add_argument('--memory-utilization', type=float, default=0.85)
    parser.add_argument('--num-gpu-blocks-override', type=int)
    parser.add_argument('--probe-mode', choices=['on', 'off'], default='on',
                        help='off installs no engine hooks; separate overhead runs only')
    args = parser.parse_args()
    args.run = args.run.resolve()
    if args.action != 'start':
        manifest = json.loads((args.run / 'service-manifest.json').read_text())
        if args.action == 'status':
            with urllib.request.urlopen(f"http://127.0.0.1:{manifest['port']}/health", timeout=3) as r:
                print(json.dumps({'healthy': r.status == 200}))
            return
        pid = int((args.run / 'server.pid').read_text())
        cmdline = Path(f'/proc/{pid}/cmdline')
        if cmdline.exists() and b'vllm.entrypoints.openai.api_server' in cmdline.read_bytes() and str(manifest['port']).encode() in cmdline.read_bytes():
            os.killpg(pid, signal.SIGTERM)
            print(json.dumps({'sent_SIGTERM': pid}))
        return
    args.run.mkdir(parents=True, exist_ok=False)
    npu = subprocess.check_output(['npu-smi', 'info'], text=True)
    devices = add_process_evidence(parse_npu(npu), npu)
    gate = resource_gate(devices, args.devices)
    (args.run / 'preflight.json').write_text(json.dumps({'npu_smi': npu, 'devices': devices, 'gate': gate}, indent=2) + '\n')
    if not gate['ready']:
        raise SystemExit(json.dumps(gate))
    config = json.loads((ROOT / 'configs/prediction/real_development_stage0.json').read_text())
    receipt = json.loads((Path(config['model_directory']) / 'download-complete.json').read_text())
    if not receipt['verified'] or receipt['revision'] != config['model_revision']:
        raise ValueError('model receipt not verified at the pinned revision')
    plugin = args.run / 'plugin'
    subprocess.run([str(ROOT / '.venv/bin/python'), '-m', 'pip', 'install', '--no-deps',
                    '--no-build-isolation', '--target', str(plugin), str(ROOT)],
                   env={**os.environ, 'PYTHONPATH': ''}, check=True, stdout=sys.stderr)
    env = runtime_env(args.run)
    env['LEASEPATH_REAL_PROBE_MODE'] = args.probe_mode
    for key in ['ASCEND_RT_VISIBLE_DEVICES', 'ASCEND_VISIBLE_DEVICES']:
        env[key] = ','.join(map(str, args.devices))
    command = [RUNTIME_PYTHON, '-m', 'vllm.entrypoints.openai.api_server',
               '--model', config['model_directory'], '--served-model-name', 'leasepath-qwen25-coder32b',
               '--host', '127.0.0.1', '--port', str(args.port), '--dtype', 'bfloat16',
               '--tensor-parallel-size', str(len(args.devices)), '--max-model-len', '16384',
               '--max-num-seqs', str(args.max_num_seqs), '--max-num-batched-tokens', '2048',
               '--block-size', '128', '--gpu-memory-utilization', str(args.memory_utilization),
               '--seed', '23', '--enable-prefix-caching', '--enable-chunked-prefill',
               '--generation-config', 'vllm', '--enforce-eager', '--no-async-scheduling',
               '--enable-auto-tool-choice', '--tool-call-parser', 'hermes',
               '--worker-cls', 'leasepath.real_worker.ObservedNPUWorker', '--enable-request-id-headers']
    if args.num_gpu_blocks_override:
        command += ['--num-gpu-blocks-override', str(args.num_gpu_blocks_override)]
    versions = subprocess.run([RUNTIME_PYTHON, '-c', 'import importlib.metadata as m,json; print(json.dumps({k:m.version(k) for k in ["vllm","vllm-ascend","torch","torch-npu","transformers"]}))'],
                              env=env, capture_output=True, text=True, check=True)
    manifest = {'observed_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
                'devices': args.devices, 'port': args.port, 'max_num_seqs': args.max_num_seqs,
                'probe_mode': args.probe_mode,
                'command': command, 'model': receipt, 'runtime_versions': json.loads(versions.stdout),
                'probe_sources_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in [ROOT / 'src/leasepath/real_probe.py', ROOT / 'src/leasepath/real_worker.py',
                              ROOT / 'src/leasepath/real_observer.py']}}
    compatibility = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    env['LEASEPATH_REAL_COMPATIBILITY'] = compatibility
    manifest['compatibility_id'] = compatibility
    (args.run / 'service-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    with (args.run / 'server.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    (args.run / 'server.pid').write_text(str(process.pid) + '\n')
    print(json.dumps({'pid': process.pid, 'run': str(args.run), 'port': args.port}))


if __name__ == '__main__':
    main()
