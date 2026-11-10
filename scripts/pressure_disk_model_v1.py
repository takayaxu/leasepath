"""Require a live owner, both full hashes, unchanged inode and actual mlock."""
import json
from pathlib import Path

from pressure_disk_keeper_v1 import process_identity, available_bytes
from resident_windowed_v1 import resident_bytes
from stage_pressure_model_v1 import digest, identity, memory_admission


def verify_disk_model(plan):
    registration = plan['disk_model']
    for key in ('plan', 'ready'):
        if digest(registration[key]) != registration[key + '_sha256']:
            raise ValueError('disk model evidence changed')
    prep = json.loads(Path(registration['plan']).read_text())
    ready = json.loads(Path(registration['ready']).read_text())
    for path, expected in prep['source_sha256'].items():
        if digest(path) != expected:
            raise ValueError('keeper source changed')
    if ready['status'] != 'ready' or ready['plan_sha256'] != registration['plan_sha256']:
        raise ValueError('disk model preparation incomplete')
    size = prep['bytes']
    if size != plan['memory_budget']['model_bytes'] or prep['sha256'] != plan['model_sha256']:
        raise ValueError('wrong model geometry/digest')
    if ready['passes'] != [{'pass': i, 'bytes': size, 'sha256': prep['sha256']} for i in (1, 2)]:
        raise ValueError('two complete model hashes required')
    if ready['preparation_wall_s'] > prep['preparation_wall_budget_s']:
        raise ValueError('model preparation exceeded bound')
    model = Path(prep['model_path'])
    if identity(model) != prep['model_identity'] or ready['model_identity'] != prep['model_identity']:
        raise ValueError('verified disk model identity changed')
    if process_identity(ready['process']['pid']) != ready['process']:
        raise ValueError('model keeper identity changed')
    status = Path(f"/proc/{ready['process']['pid']}/status").read_text()
    locked = int(next(l.split()[1] for l in status.splitlines() if l.startswith('VmLck:'))) * 1024
    if ready['locked_bytes'] != size or locked < size:
        raise ValueError('keeper lock is incomplete')
    if resident_bytes(model, size) != size:
        raise ValueError('disk model has nonresident pages')
    return {'full_sha256': prep['sha256'], 'model_identity': prep['model_identity'],
            'model_path': str(model), 'model_bytes': size, 'registration': registration,
            'keeper_process': ready['process'], 'preparation_wall_s': ready['preparation_wall_s'],
            'verification': 'two full SHA256 passes while original read-only file pages remain mlocked',
            'outside_JCT': True, 'new_model_generations': 0}


def admit_disk_model(plan, verification):
    if verify_disk_model(plan) != verification:
        raise ValueError('live model verification differs')
    result = memory_admission(available_bytes(), verification['model_bytes'],
                              plan['memory_budget']['minimum_MemAvailable_gib'] * 1024**3)
    return {**result, 'model_path': verification['model_path'], 'mincore_all_pages_resident': True,
            'accounting': 'credit original model pages once only after live mlock; unlocked disk cache receives zero credit'}


class SharedDiskKeeper:
    """A verified handle to the supervisor's keeper; never release its locks."""
    def __init__(self, plan):
        self.plan = plan
        self.verification = verify_disk_model(plan)
        self.initial = {'status': 'ready', 'residency': self.verify(),
                        'owner': self.verification['keeper_process'],
                        'verification': self.verification}

    def verify(self):
        if verify_disk_model(self.plan) != self.verification:
            raise ValueError('shared keeper changed')
        return {'files': 1, 'pages': (self.verification['model_bytes'] + 4095) // 4096,
                'nonresident_pages': 0}

    def close(self):
        pass
