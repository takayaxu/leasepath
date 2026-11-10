import hashlib
import json
from pathlib import Path
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pressure_staged_model_v1 as admission
from stage_pressure_model_v1 import digest, identity, stage, write_json


def test_native_identity_requires_completed_unchanged_staging(tmp_path):
    with tempfile.TemporaryDirectory(prefix='leasepath-admission-test-', dir='/dev/shm') as td:
        source = tmp_path / 'source'; source.write_bytes(b'frozen-model' * 100)
        destination = Path(td) / 'model'
        stage_script = Path(__file__).resolve().parents[1] / 'scripts/stage_pressure_model_v1.py'
        stage_plan = {'source': str(source), 'source_identity': identity(source), 'destination': str(destination),
                      'bytes': source.stat().st_size, 'sha256': digest(source), 'chunk_bytes': 128,
                      'total_budget_s': 30, 'attempt_budget_s': 20,
                      'minimum_total_available_bytes': 0, 'tmpfs_reserve_bytes': 0,
                      'script': str(stage_script), 'script_sha256': digest(stage_script)}
        plan_path = tmp_path / 'stage-plan.json'; write_json(plan_path, stage_plan)
        checkpoint = tmp_path / 'checkpoint.json'; stage(plan_path, checkpoint)
        native_plan = {'model_sha256': digest(source),
                       'memory_budget': {'model_bytes': source.stat().st_size, 'minimum_MemAvailable_gib': 0},
                       'staged_model': {'plan': str(plan_path), 'plan_sha256': digest(plan_path),
                                        'checkpoint': str(checkpoint), 'checkpoint_sha256': digest(checkpoint)}}
        verified = admission.verify_staged_model(native_plan)
        assert admission.admit_staged_model(native_plan, verified)['owned_model_bytes'] == source.stat().st_size
        state = json.loads(checkpoint.read_text()); state['destination_full_hash_verified'] = False
        write_json(checkpoint, state)
        native_plan['staged_model']['checkpoint_sha256'] = digest(checkpoint)
        with pytest.raises(ValueError, match='two complete hash'):
            admission.verify_staged_model(native_plan)


def test_unresident_model_not_credited(tmp_path, monkeypatch):
    path = tmp_path / 'model'; path.write_bytes(b'model')
    monkeypatch.setattr(admission, 'resident_bytes', lambda path, size: size - 1)
    with pytest.raises(ValueError, match='nonresident'):
        admission.admit_staged_model({'memory_budget': {'minimum_MemAvailable_gib': 0}},
                                    {'model_path': str(path), 'model_identity': identity(path), 'model_bytes': 5})
