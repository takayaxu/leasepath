import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile

import pytest

spec = importlib.util.spec_from_file_location('staging', Path(__file__).resolve().parents[1] / 'scripts/stage_pressure_model_v1.py')
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


def test_memory_credit_counts_model_once():
    assert staging.memory_admission(160, 60, 220)['effective_available_bytes'] == 220
    with pytest.raises(RuntimeError):
        staging.memory_admission(159, 60, 220)


def test_prefix_rejects_corruption_and_gaps():
    rows = [{'offset': 0, 'bytes': 3, 'sha256': hashlib.sha256(b'abc').hexdigest()}]
    assert staging.verify_prefix(io.BytesIO(b'abcUNCOMMITTED'), rows, 4) == 3
    with pytest.raises(ValueError, match='bytes differ'):
        staging.verify_prefix(io.BytesIO(b'abd'), rows, 4)
    rows[0]['offset'] = 1
    with pytest.raises(ValueError, match='noncontiguous'):
        staging.verify_prefix(io.BytesIO(b'abc'), rows, 4)


def test_resume_hashes_acknowledged_prefix_discards_only_tail(tmp_path, monkeypatch):
    with tempfile.TemporaryDirectory(prefix='leasepath-stage-test-', dir='/dev/shm') as td:
        source = tmp_path / 'original'; source.write_bytes(b'abcde' * 100)
        destination = Path(td) / 'model'
        plan = {'source': str(source), 'source_identity': staging.identity(source),
                'destination': str(destination), 'bytes': source.stat().st_size,
                'sha256': staging.digest(source), 'chunk_bytes': 64,
                'total_budget_s': 30, 'attempt_budget_s': 20,
                'minimum_total_available_bytes': 0, 'tmpfs_reserve_bytes': 0}
        plan_path = tmp_path / 'plan.json'; staging.write_json(plan_path, plan)
        checkpoint = tmp_path / 'checkpoint.json'
        original = staging.resources
        calls = []
        def interrupt(*args):
            calls.append(1)
            if len(calls) == 3:
                raise TimeoutError('test interruption')
            return original(*args)
        monkeypatch.setattr(staging, 'resources', interrupt)
        with pytest.raises(TimeoutError):
            staging.stage(plan_path, checkpoint)
        state = json.loads(checkpoint.read_text())
        assert state['status'] == 'paused' and state['bytes'] == 128
        with destination.open('ab') as stream:
            stream.write(b'uncommitted tail')
        monkeypatch.setattr(staging, 'resources', original)
        result = staging.stage(plan_path, checkpoint)
        assert result['full_sha256'] == staging.digest(source) == staging.digest(destination)
        state = json.loads(checkpoint.read_text())
        assert len(state['attempts']) == 2
        assert staging.resident_bytes(destination, 500) == 500
        assert staging.stage(plan_path, checkpoint)['full_sha256'] == plan['sha256']
        destination.chmod(0o644)
        destination.write_bytes(b'X' * 500)
        with pytest.raises(ValueError, match='stat changed'):
            staging.stage(plan_path, checkpoint)


def test_wrong_expected_hash_never_produces_complete(tmp_path):
    with tempfile.TemporaryDirectory(prefix='leasepath-stage-test-', dir='/dev/shm') as td:
        source = tmp_path / 'original'; source.write_bytes(b'model')
        plan = {'source': str(source), 'source_identity': staging.identity(source),
                'destination': str(Path(td) / 'model'), 'bytes': 5, 'sha256': '0' * 64,
                'chunk_bytes': 64, 'total_budget_s': 30, 'attempt_budget_s': 20,
                'minimum_total_available_bytes': 0, 'tmpfs_reserve_bytes': 0}
        plan_path = tmp_path / 'plan.json'; staging.write_json(plan_path, plan)
        checkpoint = tmp_path / 'checkpoint.json'
        with pytest.raises(ValueError, match='digest/stat'):
            staging.stage(plan_path, checkpoint)
        assert json.loads(checkpoint.read_text())['status'] == 'failed'
        with pytest.raises(ValueError, match='integrity failure'):
            staging.stage(plan_path, checkpoint)
