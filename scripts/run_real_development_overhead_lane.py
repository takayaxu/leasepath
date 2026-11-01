"""Finish one prestarted crossover lane, swapping probe mode on the same cards."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

from preflight_real_development import parse_npu, add_process_evidence, resource_gate

SCRIPTS = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first', type=Path, required=True)
    parser.add_argument('--second', type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((args.first / 'service-manifest.json').read_text())

    def wait_for(test, seconds=600):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if test():
                return
            time.sleep(3)
        raise TimeoutError('crossover lane prerequisite did not complete')

    def service(action, run, *extra):
        subprocess.run([sys.executable, str(SCRIPTS / 'real_development_service.py'), action,
                        '--run', str(run), *extra], check=True)

    def close(run):
        current = json.loads((run / 'service-manifest.json').read_text())
        if current['probe_mode'] == 'on':
            req = urllib.request.Request(f"http://127.0.0.1:{current['port']}/collective_rpc",
                data=json.dumps({'method': 'leasepath_checkpoint', 'timeout': 60}).encode(),
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=70) as response:
                (run / 'checkpoint.json').write_bytes(response.read())
        service('stop', run)

    wait_for(lambda: (args.first / 'overhead-workload.json').exists())
    close(args.first)

    def idle():
        npu = subprocess.check_output(['npu-smi', 'info'], text=True)
        return resource_gate(add_process_evidence(parse_npu(npu), npu), manifest['devices'])['ready']

    wait_for(idle, 120)
    mode = 'on' if manifest['probe_mode'] == 'off' else 'off'
    service('start', args.second, '--devices', *map(str, manifest['devices']), '--port', str(manifest['port']),
            '--max-num-seqs', '8', '--num-gpu-blocks-override', '192', '--probe-mode', mode)

    def healthy():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{manifest['port']}/health", timeout=2) as response:
                return response.status == 200
        except OSError:
            return False

    wait_for(healthy)
    with (args.second / 'workload-progress.log').open('x') as log:
        subprocess.run([sys.executable, str(SCRIPTS / 'measure_real_development_overhead.py'),
                        '--run', str(args.second)], check=True, stdout=log, stderr=subprocess.STDOUT)
    close(args.second)
    print(json.dumps({'crossover_lane_complete': manifest['devices'], 'second_mode': mode}), flush=True)


if __name__ == '__main__':
    main()
