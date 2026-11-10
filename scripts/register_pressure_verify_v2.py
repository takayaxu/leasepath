"""Freeze remaining preparation budget, including the killed prior attempt."""
import hashlib
import json
from pathlib import Path
import shutil
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / 'results/cache_policy/pressure_verify_v2_2026-11-10'
    tmp = Path('/dev/shm/leasepath-model-stage-v1-20261110')
    target = out / 'plan.json'
    if target.exists():
        raise ValueError('verification preregistration already exists')
    old = json.loads((out / 'prior-checkpoint.json').read_text())
    stop = json.loads((ROOT / 'results/cache_policy/pressure_staged_matrix_v1_2026-11-10/state.json').read_text())
    copy_plan = ROOT / 'results/cache_policy/pressure_model_staging_v1_2026-11-10/plan.json'
    copy = json.loads(copy_plan.read_text())
    missing = (stop['finished_wall_ns'] - old['attempts'][-1]['started_wall_ns']) / 1e9
    spent = old['total_elapsed_s'] + missing
    if not 0 < 10800 - spent < 3600:
        raise ValueError('unexpected remaining preparation budget')
    model = Path(copy['destination'])
    model.chmod(0o444)
    files = [ROOT / 'scripts/verify_pressure_model_v2.py', ROOT / 'scripts/stage_pressure_model_v1.py',
             ROOT / 'native/leasepath_storage_preexec_v2.c', tmp / 'storage-launcher-v2',
             out / 'prior-checkpoint.json', copy_plan, Path('/usr/lib/x86_64-linux-gnu/libcrypto.so.3')]
    def sha(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    plan = {'version': 2, 'created_wall_ns': time.time_ns(), 'model_path': str(model),
            'model_identity': {k: getattr(model.stat(), k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')},
            'bytes': copy['bytes'], 'sha256': copy['sha256'], 'source_sha256': {str(p): sha(p) for p in files},
            'copy_plan': str(copy_plan), 'copy_checkpoint': str(out / 'prior-checkpoint.json'),
            'libcrypto': '/usr/lib/x86_64-linux-gnu/libcrypto.so.3', 'buffer_bytes': 1048576,
            'remaining_budget_s': int(10800 - spent), 'original_total_budget_s': 10800,
            'prior_recorded_elapsed_s': old['total_elapsed_s'], 'previous_unrecorded_attempt_s': missing,
            'prior_total_elapsed_s': spent, 'minimum_total_available_bytes': 220 * 1024**3,
            'storage_controls': {'cpus': [0,28], 'numa_node': 0, 'THP_disabled': True, 'memory_limit_gib': 2, 'cpu_limit_s': 1800},
            'method': 'Two independent full destination SHA256 hashes; every chunk checked against original copied chunk; serialized OpenSSL SHA256 state after each64MiB; reuse only with immutable stat, exact verifier/libcrypto and checkpoint checksum. No claim of newly reading source model in full.',
            'scope': 'Storage preparation only, no JCT/generation or global controls. Original wall budget unchanged; missing killed runtime charged. No retry after resource/integrity failure.'}
    target.write_text(json.dumps(plan, indent=2) + '\n')
    for file in tmp.iterdir():
        if file.name.startswith(('original-', 'staged-')) and file.suffix in ('.log', '.strace'):
            shutil.copyfile(file, out / file.name)
    shutil.copyfile(tmp / 'read-probe.py', out / 'read-probe.py')
    print(json.dumps({'prior_spent_s': spent, 'remaining_s': plan['remaining_budget_s'], 'plan': str(target)}), flush=True)


if __name__ == '__main__':
    main()
