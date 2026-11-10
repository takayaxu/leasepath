"""Identity and memory admission for a completely hashed owned tmpfs model."""
import json
from pathlib import Path

from stage_pressure_model_v1 import digest, identity, memory_admission, resident_bytes, verify_complete


def verify_staged_model(plan):
    registration = plan['staged_model']
    stage_path = Path(registration['plan'])
    checkpoint = Path(registration['checkpoint'])
    for path, key in ((stage_path, 'plan_sha256'), (checkpoint, 'checkpoint_sha256')):
        if digest(path) != registration[key]:
            raise ValueError('staged model evidence changed: ' + str(path))
    stage = json.loads(stage_path.read_text())
    stage['_sha256'] = registration['plan_sha256']
    if digest(Path(stage['script'])) != stage['script_sha256']:
        raise ValueError('staging verifier source changed')
    state = json.loads(checkpoint.read_text())
    model = Path(stage['destination'])
    if not model.resolve().is_relative_to('/dev/shm') or model.is_symlink():
        raise ValueError('owned tmpfs model required')
    if stage['sha256'] != plan['model_sha256'] or stage['bytes'] != plan['memory_budget']['model_bytes']:
        raise ValueError('staged model differs from frozen model')
    if not state['destination_full_hash_verified'] or state['source_stream_sha256'] != stage['sha256']:
        raise ValueError('two complete hash passes required')
    if identity(stage['source']) != stage['source_identity']:
        raise ValueError('original model changed since staging')
    result = verify_complete(stage, state, model)
    return {**result, 'model_path': str(model), 'model_bytes': stage['bytes'],
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
