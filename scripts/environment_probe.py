"""Read environment metadata; optionally execute one tiny CUDA kernel.

Never launches a model, installs software, or changes other GPU processes.
"""
import argparse
import datetime
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cuda-device', type=int, help='Physical GPU index for tiny kernel check')
    args = parser.parse_args()
    if args.cuda_device is not None:
        os.environ['CUDA_VISIBLE_DEVICES'] = str(args.cuda_device)
    info = {'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
            'evidence_kind': 'environment_observation', 'packages': {}, 'model_benchmark_run': False}
    for name in ['torch', 'transformers', 'vllm', 'numpy', 'pytest', 'jsonschema', 'huggingface-hub']:
        try:
            info['packages'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            info['packages'][name] = None
    try:
        p = subprocess.run(['nvidia-smi', '--query-gpu=index,name,memory.used,memory.total,utilization.gpu',
                            '--format=csv,noheader'], capture_output=True, text=True, timeout=20)
        info['nvidia_smi'] = {'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        info['nvidia_smi'] = {'error': str(exc)}
    try:
        import torch
        info['torch_cuda_build'] = torch.version.cuda
        info['torch_compiled_architectures'] = torch.cuda.get_arch_list()
        info['cuda_available'] = torch.cuda.is_available()
        if args.cuda_device is not None and torch.cuda.is_available():
            info['cuda_physical_device'] = args.cuda_device
            info['cuda_capability'] = list(torch.cuda.get_device_capability(0))
            try:
                x = torch.ones(1, device='cuda')
                y = x + 1
                torch.cuda.synchronize()
                info['cuda_smoke'] = {'status': 'passed', 'result': y.item()}
            except Exception as exc:
                info['cuda_smoke'] = {'status': 'failed', 'error': str(exc)}
    except Exception as exc:
        info['torch_probe_error'] = str(exc)
    cache = Path(os.environ.get('HF_HOME', str(Path.home()/'.cache/huggingface'))) / 'hub'
    try:
        info['local_hf_cache'] = {'path': str(cache), 'models': sorted(p.name for p in cache.glob('models--*'))}
    except OSError as exc:
        info['local_hf_cache'] = {'path': str(cache), 'error': str(exc)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(info, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps({'output': str(args.output), 'cuda_smoke': info.get('cuda_smoke'),
                      'vllm': info['packages']['vllm']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
