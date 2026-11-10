"""Identity and memory admission for a completely hashed owned tmpfs model."""
import json
from pathlib import Path

from stage_pressure_model_v1 import digest, identity, memory_admission, resident_bytes
from verify_pressure_model_v3 import read_state


def verify_staged_model(plan):
    registration = plan['staged_model']
    stage_path = Path(registration['plan'])
    checkpoint = Path(registration['checkpoint'])
    for path, key in ((stage_path, 'plan_sha256'), (checkpoint, 'checkpoint_sha256')):
        if digest(path) != registration[key]:
            raise ValueError('staged model evidence changed: ' + str(path))
    stage = json.loads(stage_path.read_text())
    for path, expected in stage['source_sha256'].items():
        if digest(Path(path)) != expected:
            raise ValueError('verification source/evidence changed: ' + path)
    state = read_state(checkpoint)
    model = Path(stage['model_path'])
    if not model.resolve().is_relative_to('/dev/shm') or model.is_symlink() or model.stat().st_mode & 0o222:
        raise ValueError('immutable own tmpfs model required')
    if stage['sha256'] != plan['model_sha256'] or stage['bytes'] != plan['memory_budget']['model_bytes']:
        raise ValueError('staged model differs from frozen model')
    if state['status'] != 'complete' or state['plan_sha256'] != registration['plan_sha256']:
        raise ValueError('complete matching verification required')
    if not state['destination_full_hash_verified'] or state['sha256'] != stage['sha256']:
        raise ValueError('complete destination hash required')
    if len(state['passes']) != 2 or any(p['pass'] != i+1 or p['sha256'] != stage['sha256'] or p['bytes'] != stage['bytes'] or not p['all_copy_chunk_hashes_checked'] for i,p in enumerate(state['passes'])):
        raise ValueError('two independent full destination passes required')
    if identity(model) != state['destination_identity'] or identity(model) != stage['model_identity']:
        raise ValueError('verified model identity changed')
    source = json.loads(Path(stage['copy_plan']).read_text())
    if identity(source['source']) != source['source_identity']:
        raise ValueError('original source changed')
    return {'full_sha256': state['sha256'], 'model_identity': state['destination_identity'],
            'verification': state['verification'], 'outside_JCT': True,
            'preparation_wall_s': stage['prior_total_elapsed_s'] + state['elapsed_s'],
            'model_path': str(model), 'model_bytes': stage['bytes'],
            'registration': registration, 'new_model_generations': state['new_model_generations']}


def admit_staged_model(plan, verification):
    model = Path(verification['model_path'])
    if identity(model) != verification['model_identity']:
        raise ValueError('model stat changed before admission')
    size = verification['model_bytes']
    resident = resident_bytes(model, size)
    if resident != size:
        raise ValueError('staged model has nonresident pages; preparation incomplete')
    info = {k: int(v.split()[0]) * 1024 for k, v in
            (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())}
    result = memory_admission(info['MemAvailable'], resident, plan['memory_budget']['minimum_MemAvailable_gib'] * 1024**3)
    return {**result, 'model_path': str(model), 'mincore_all_pages_resident': True,
            'accounting': 'original220GiB budget; credit only already allocated owned resident model, exactly once'}
