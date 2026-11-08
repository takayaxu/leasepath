from copy import deepcopy

import pytest

from leasepath.qwen_cache_backend import check_physical


def physical():
    return {'sequences': {'1': [[0, 4, 10], [1, 5, 20]]},
            'used_cells': 2, 'idle_union_cells': 2, 'device_kv_reserved_bytes': {'CUDA0': 2048}}


def check(value, expected=None):
    check_physical(value, expected or {1: [(0, 10), (1, 20)]},
                   block_size=2, capacity=2, headroom=2)


def test_physical_evidence_validated_independently_of_backend_counts():
    check(physical())
    for field, value in [('used_cells', 3), ('idle_union_cells', 1), ('device_kv_reserved_bytes', {})]:
        bad = physical(); bad[field] = value
        with pytest.raises(ValueError): check(bad)
    bad = physical(); bad['sequences']['1'][0][2] = 99
    with pytest.raises(ValueError, match='positions/tokens'): check(bad)
    bad = physical(); bad['sequences']['1'][1][1] = 4
    with pytest.raises(ValueError, match='alias'): check(bad)


def test_unknown_slots_active_leaks_and_cross_block_aliasing_rejected():
    bad = physical(); bad['sequences']['0'] = [[2, 6, 30]]
    with pytest.raises(ValueError, match='active sequence'): check(bad)
    bad = physical(); bad['sequences']['2'] = [[2, 6, 30], [3, 7, 40]]
    with pytest.raises(ValueError, match='membership'): check(bad)
    bad['sequences']['2'][0][1] = 4
    with pytest.raises(ValueError, match='alias'):
        check(bad, {1: [(0, 10), (1, 20)], 2: [(2, 30), (3, 40)]})
