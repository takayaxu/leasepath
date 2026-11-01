"""Read-only stage-0 evidence. Never starts a model or stops another service."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from zoneinfo import ZoneInfo


def parse_npu(text):
    devices = []
    current = None
    for line in text.splitlines():
        match = re.search(r"\|\s*(\d+)\s+910B2\s+\|", line)
        if match:
            current = int(match[1])
        memory = re.search(r"(\d+)\s*/\s*(65536)\s*\|", line)
        if memory and current is not None:
            devices.append(dict(device=current, used_mib=int(memory[1]), total_mib=int(memory[2])))
            current = None
    return devices


def add_process_evidence(devices, text):
    """Ascend retains ~3.3 GiB without a user process; require both signals."""
    empty = {int(n) for n in re.findall(r'No running processes found in NPU (\d+)', text)}
    for device in devices:
        device['no_running_processes'] = device['device'] in empty
    return devices


def idle(device):
    return device['used_mib'] <= 4096 and device.get('no_running_processes') is True


def resource_gate(devices, reserved):
    """A fresh idle snapshot AND an explicit dedicated reservation are required."""
    by_id = {d['device']: d for d in devices}
    reasons = []
    if sum(idle(d) for d in devices) < 2:
        reasons.append('fewer_than_two_idle_devices')
    if len(reserved) < 2 or len(set(reserved)) != len(reserved):
        reasons.append('need_at_least_two_distinct_reserved_devices')
    for device in reserved:
        if device not in by_id:
            reasons.append(f'device_{device}_not_observed')
        elif not idle(by_id[device]):
            reasons.append(f'device_{device}_has_existing_memory_allocation')
    if not devices:
        reasons.append('npu_snapshot_unavailable_or_unrecognized')
    return {'ready': not reasons, 'reasons': reasons,
            'note': 'Read-only preflight, not an allocator or authorization to terminate processes.'}


def capture(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return dict(command=command, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(command=command, error=str(exc))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='Fresh evidence directory')
    parser.add_argument('--reserved-devices', type=int, nargs='+', default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    npu = capture(['npu-smi', 'info'])
    (args.output / 'npu-smi.json').write_text(json.dumps(npu, indent=2) + '\n')
    devices = parse_npu(npu.get('stdout', '')) if npu.get('returncode') == 0 else []
    add_process_evidence(devices, npu.get('stdout', ''))
    snapshot = {
        'observed_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
        'architecture': platform.machine(), 'logical_cpus': os.cpu_count(),
        'cpu_affinity': sorted(os.sched_getaffinity(0)),
        'memory': {line.split(':')[0]: line.split(':')[1].strip()
                   for line in Path('/proc/meminfo').read_text().splitlines()
                   if line.startswith(('MemTotal:', 'MemAvailable:'))},
        'disks': {str(p): dict(zip(('total', 'used', 'free'), shutil.disk_usage(p)))
                  for p in (Path.cwd(), Path('/home/ma-user/work'))},
        'devices': devices, 'reserved_devices': args.reserved_devices,
        'resource_gate': resource_gate(devices, args.reserved_devices),
        'codex': capture(['codex', '--version']),
        'git_head': capture(['git', 'rev-parse', 'HEAD']),
        'evidence_scope': 'stage0_read_only_preflight_not_inference',
    }
    (args.output / 'snapshot.json').write_text(json.dumps(snapshot, indent=2) + '\n')
    manifest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir()}
    (args.output / 'sha256.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(snapshot['resource_gate']))
    raise SystemExit(0 if snapshot['resource_gate']['ready'] else 2)


if __name__ == '__main__':
    main()
