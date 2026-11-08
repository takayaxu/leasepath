from copy import deepcopy

import pytest

from leasepath.predictive_cache import CapacityUnavailable, PredictiveCache


def features():
    return {'elapsed_idle_s': 0., 'compatibility_id': 'b', 'prefix_tokens': 128,
            'tool_name': 'compute', 'tool_elapsed_s': 0., 'phase': 'waiting_tool',
            'retry_count': None, 'is_queued': None, 'queue_length': None}


class Predictor:
    def __init__(self):
        self.inputs = []

    def predict(self, f):
        self.inputs.append(deepcopy(f))
        p = .9 if f['tool_name'] == 'compute' else .1
        return [{'right_s': 3., 'supported': True, 'hU': p, 'hD': 0., 'h0': 1-p,
                 'F_U': p, 'F_D': 0., 'survival': 1-p}]


def test_prediction_evicts_lower_reuse_even_when_newer_and_observes_only_current_state():
    predictor = Predictor()
    c = PredictiveCache(3, policy='predicted_reuse', predictor=predictor)
    c.start('a', ('a',), 0); c.release('a', features(), 0)
    c.start('b', ('b',), 0)
    f = features(); f['tool_name'] = 'inspect_input'
    c.release('b', f, 0)
    c.start('c', ('c', 'cc'), 1)
    assert set(c.blocks) == {'a', 'c', 'cc'}
    assert all(x['elapsed_idle_s'] == 1 and x['tool_elapsed_s'] == 1 for x in predictor.inputs)
    bad = features() | {'remaining_s': .1}
    with pytest.raises(ValueError, match='allowlist'):
        c.observe('a', bad, 1)


def test_active_request_capacity_failure_preserves_state_and_shared_owner_retire():
    c = PredictiveCache(2)
    c.start('a', ('shared',), 0); c.release('a', features(), 0)
    c.start('b', ('shared', 'tail'), 0); c.release('b', features(), 0)
    c.retire('a', 0)
    assert set(c.blocks) == {'shared', 'tail'} and 'b' in c.idle
    c.start('b', ('shared', 'tail'), 0)
    before = deepcopy(c.blocks)
    with pytest.raises(CapacityUnavailable):
        c.start('c', ('new',), 0)
    assert c.blocks == before
    with pytest.raises(ValueError, match='active'):
        c.retire('b', 0)


def test_eviction_preserves_prefix_ancestry_and_expired_relation_is_not_hit_loss():
    c = PredictiveCache(3)
    c.start('a', ('root', 'mid', 'leaf'), 0); c.release('a', features(), 0)
    c.retire('a', 0)
    result = c.start('b', ('new', 'newleaf'), 0)
    assert [r['key'] for r in result['evicted']] == ['leaf', 'mid']
    assert c.prefix_hits(('root',)) == 1
    c.check()


def test_no_prediction_without_pressure_and_unknown_support_falls_back():
    class Unsupported(Predictor):
        def predict(self, f):
            self.inputs.append(f)
            return [{'right_s': 3., 'supported': False, 'hU': None, 'hD': None,
                     'h0': None, 'F_U': None, 'F_D': None, 'survival': None}]
    predictor = Unsupported()
    c = PredictiveCache(2, policy='predicted_reuse', predictor=predictor)
    c.start('a', ('a',), 0); c.release('a', features(), 0)
    assert not predictor.inputs
    c.start('b', ('b', 'bb'), 0)
    assert c.last_evictions[0]['reason'] == 'lru_fallback_unsupported'
    assert c.prediction_calls == 1
