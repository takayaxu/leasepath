from copy import deepcopy

import pytest

from leasepath.cache_policy_models import FixedTTLPriority, TransferredPredictor


def features():
    return {'elapsed_idle_s': 0., 'compatibility_id': 'verified-new-native', 'prefix_tokens': 128,
            'tool_name': 'compute', 'tool_elapsed_s': 0., 'phase': 'waiting_tool',
            'retry_count': None, 'is_queued': None, 'queue_length': None}


def test_transfer_rejects_other_domains_and_does_not_relabel_tensor_identity():
    class Model:
        compatibilities = ['old-training-native']
        def predict(self, f): return f
    predictor = TransferredPredictor(Model(), runtime_compatibility='verified-new-native',
                                    training_compatibility='old-training-native')
    current = features()
    original = deepcopy(current)
    translated = predictor.predict(current)
    assert current == original and translated['compatibility_id'] == 'old-training-native'
    assert {k:v for k,v in translated.items() if k != 'compatibility_id'} == {
        k:v for k,v in current.items() if k != 'compatibility_id'}
    current['compatibility_id'] = 'different-model'
    with pytest.raises(ValueError, match='verified runtime'): predictor.predict(current)
    with pytest.raises(ValueError, match='training compatibility'):
        TransferredPredictor(Model(), runtime_compatibility='x', training_compatibility='wrong')


def test_ttl_priority_expires_without_fabricating_a_future_event():
    predictor = FixedTTLPriority(1.)
    f = features()
    assert predictor.predict(f)[0]['F_U'] == 1
    f['elapsed_idle_s'] = 1.
    assert predictor.predict(f)[0]['F_U'] == 0
    assert predictor.predict(f)[0]['survival'] == 1
    with pytest.raises(ValueError): predictor.predict(f | {'actual_end_s': 2.})
