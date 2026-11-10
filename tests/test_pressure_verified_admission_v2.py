import json
from pathlib import Path
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pressure_staged_model_v2 import verify_staged_model
from verify_pressure_model_v3 import verify, read_state, seal_state
from stage_pressure_model_v1 import digest, identity, write_json


def test_native_requires_both_verified_destination_passes_and_identity():
    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='leasepath-native-verify-test-') as td:
        root = Path(td); model = root / 'model'; model.write_bytes(b'known model bytes')
        source = root / 'source'; source.write_bytes(model.read_bytes()); model.chmod(0o444)
        copy = root / 'copy.json'; write_json(copy, {'source':str(source), 'source_identity':identity(source), 'chunk_bytes':64})
        chunks = root / 'chunks.json'; write_json(chunks, {'bytes':17, 'chunks':[{'offset':0,'bytes':17,'sha256':digest(model)}]})
        plan = root / 'plan.json'
        write_json(plan, {'model_path':str(model), 'model_identity':identity(model), 'source_sha256':{},
                          'copy_plan':str(copy), 'copy_checkpoint':str(chunks), 'bytes':17, 'sha256':digest(model),
                          'libcrypto':'/usr/lib/x86_64-linux-gnu/libcrypto.so.3', 'buffer_bytes':5,
                          'remaining_budget_s':30, 'prior_total_elapsed_s':0, 'minimum_total_available_bytes':0})
        checkpoint = root / 'verified.json'; verify(plan, checkpoint)
        native = {'staged_model':{'plan':str(plan), 'plan_sha256':digest(plan), 'checkpoint':str(checkpoint),
                                  'checkpoint_sha256':digest(checkpoint)},
                  'model_sha256':digest(model), 'memory_budget':{'model_bytes':17}}
        assert verify_staged_model(native)['full_sha256'] == digest(model)
        state = read_state(checkpoint); state['passes'].pop(); seal_state(checkpoint,state)
        native['staged_model']['checkpoint_sha256'] = digest(checkpoint)
        with pytest.raises(ValueError,match='two independent'):
            verify_staged_model(native)
        model.chmod(0o644)
        with pytest.raises(ValueError,match='immutable'):
            verify_staged_model(native)
