"""Independent block lifecycle invariants and fail-closed request accounting."""
from copy import deepcopy

import pytest

from leasepath.real_audit import audit_blocks, audit_requests


def row(kind, **data):
    return {'kind': kind, 'data': data}


def block(ref=1, generation=1, hash_value=None):
    return {'slot': 1, 'generation': generation, 'hash': hash_value, 'ref_count': ref, 'is_null': False}


def transitions():
    return [row('block_allocate', blocks=[block()]),
            row('block_cache_insert', before=[block()], after=[block(hash_value='h')]),
            row('block_free', before=[block(hash_value='h')], after=[block(0, hash_value='h')]),
            row('block_touch', before=[block(0, hash_value='h')], after=[block(hash_value='h')]),
            row('block_free', before=[block(hash_value='h')], after=[block(0, hash_value='h')]),
            row('block_evict', before=block(0, hash_value='h'), after=block(0)),
            row('block_allocate', blocks=[block(generation=2)])]


def test_shared_touch_and_slot_reallocation_have_distinct_lifetimes():
    result = audit_blocks(transitions())
    assert result['max_generation'] == 2
    assert result['capacity_evictions'] == 1
    assert result['counts']['block_touch'] == 1


@pytest.mark.parametrize('index,key,value', [(1, 'ref_count', 2), (2, 'generation', 2),
                                          (5, 'ref_count', 1), (5, 'hash', 'different')])
def test_corrupt_lifecycle_transition_rejected(index, key, value):
    rows = transitions()
    before = rows[index]['data']['before']
    (before[0] if isinstance(before, list) else before)[key] = value
    with pytest.raises(ValueError):
        audit_blocks(rows)


def test_generation_bump_does_not_allow_reallocating_an_active_slot():
    with pytest.raises(ValueError, match='active slot'):
        audit_blocks(transitions()[:2] + [row('block_allocate', blocks=[block(generation=2)])])


def test_duplicate_rank_and_duplicate_enqueue_rejected():
    config = row('cache_config', num_blocks=10)
    rank = row('rank_cache_config', rank=0, num_blocks=10, physical_kv_bytes=100)
    with pytest.raises(ValueError, match='duplicate rank'):
        audit_requests([config, rank, deepcopy(rank)])
    enqueue = row('request_enqueue', request_id='real-request')
    with pytest.raises(ValueError, match='enqueue'):
        audit_requests([config, enqueue, deepcopy(enqueue)])


def test_abort_before_full_prefill_is_auditable_but_not_an_idle_prefix():
    records = [row('cache_config', num_blocks=10),
               {'kind': 'request_enqueue', 'ts_ns': 1,
                'data': {'request_id': 'cancel', 'prompt_token_ids': [1, 2]}},
               {'kind': 'cache_lookup', 'ts_ns': 3,
                'data': {'request_id': 'cancel', 'lookup_started_ns': 2, 'cached_tokens': 0}},
               {'kind': 'request_release', 'ts_ns': 4,
                'data': {'request_id': 'cancel', 'status': 'FINISHED_ABORTED',
                         'prefix_token_ids': [1, 2], 'block_size': 2,
                         'blocks_before_release': [[]]}}]
    result = audit_requests(records)['requests'][0]
    assert result['complete'] and not result['normal_completion']
    assert not result['idle_prefix_eligible']
    records[-1]['data']['status'] = 'FINISHED_STOPPED'
    with pytest.raises(ValueError, match='physical hashed block'):
        audit_requests(records)
