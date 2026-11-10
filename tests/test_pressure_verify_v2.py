import ctypes
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verify_pressure_model_v2 import ResumableSHA256, self_test, verify, read_state
from stage_pressure_model_v1 import identity, digest, write_json

LIB = '/usr/lib/x86_64-linux-gnu/libcrypto.so.3'


def test_serialized_sha_matches_hashlib_at_partial_blocks():
    self_test(ctypes.CDLL(LIB))
    data = bytearray(b'abcd' * 101)
    h = ResumableSHA256(ctypes.CDLL(LIB)); h.update(data, 91)
    with pytest.raises(ValueError, match='offset'):
        ResumableSHA256(ctypes.CDLL(LIB), h.export(), 92)


def fixture(tmp_path):
    model = tmp_path / 'model'; model.write_bytes(bytes(range(256)) * 11 + b'end')
    source = tmp_path / 'source'; source.write_bytes(model.read_bytes())
    model.chmod(0o444)
    copy_plan = tmp_path / 'copy-plan.json'
    write_json(copy_plan, {'source': str(source), 'source_identity': identity(source), 'chunk_bytes': 256})
    data = model.read_bytes()
    chunks = [{'offset': i, 'bytes': len(data[i:i+256]), 'sha256': hashlib.sha256(data[i:i+256]).hexdigest()}
              for i in range(0, len(data), 256)]
    copy_checkpoint = tmp_path / 'copy-checkpoint.json'; write_json(copy_checkpoint, {'bytes': len(data), 'chunks': chunks})
    plan = tmp_path / 'plan.json'
    write_json(plan, {'model_path': str(model), 'model_identity': identity(model), 'source_sha256': {},
                     'copy_plan': str(copy_plan), 'copy_checkpoint': str(copy_checkpoint), 'bytes': len(data),
                     'sha256': digest(model), 'libcrypto': LIB, 'buffer_bytes': 67, 'remaining_budget_s': 60,
                     'minimum_total_available_bytes': 0})
    return plan, tmp_path / 'checkpoint.json', model


def test_verification_resume_preserves_both_passes(tmp_path):
    plan, checkpoint, model = fixture(tmp_path)
    with pytest.raises(TimeoutError):
        verify(plan, checkpoint, stop_after_chunks=4)
    state = read_state(checkpoint)
    assert state['status'] == 'paused' and state['offset'] == 1024
    final = verify(plan, checkpoint)
    assert final['status'] == 'complete'
    assert len(final['passes']) == 2
    assert all(p['sha256'] == digest(model) for p in final['passes'])
    assert final['attempts'][1]['resume_offset'] == 1024
    assert verify(plan, checkpoint)['status'] == 'complete'


def test_resume_rejects_file_and_checkpoint_mutation(tmp_path):
    plan, checkpoint, model = fixture(tmp_path)
    with pytest.raises(TimeoutError):
        verify(plan, checkpoint, stop_after_chunks=4)
    state = json.loads(checkpoint.read_text()); state['offset'] += 1
    write_json(checkpoint, state)
    with pytest.raises(ValueError, match='checkpoint checksum'):
        verify(plan, checkpoint)
    model.chmod(0o644); model.write_bytes(b'X' * model.stat().st_size); model.chmod(0o444)
    with pytest.raises(ValueError, match='immutable model identity'):
        verify(plan, checkpoint)


def test_full_hash_must_match_known_model(tmp_path):
    plan, checkpoint, model = fixture(tmp_path)
    value = json.loads(plan.read_text()); value['sha256'] = '0' * 64; write_json(plan, value)
    with pytest.raises(ValueError, match='complete model SHA256 mismatch'):
        verify(plan, checkpoint)
    assert read_state(checkpoint)['status'] == 'failed'
