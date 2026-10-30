import pytest
from leasepath.cache import Cache, block_keys


def test_prefix_identity_uses_tokens_and_compatibility():
    a = block_keys([1,2,3,4], 'v1', 2)
    b = block_keys([1,2,5,6], 'v1', 2)
    assert a[0] == b[0] and a[1] != b[1]
    assert a != block_keys([1,2,3,4], 'v2', 2)
    assert a[1] != block_keys([8,9,3,4], 'v1', 2)[1]


def test_expiry_leaves_cache_hit_and_counts_union_time():
    c = Cache(4, 3, 1024)
    keys = ('shared',)
    assert c.start('a', keys) == 0
    c.finish('a', keys, 1)
    assert c.start('b', keys) == 1
    c.finish('b', keys, 2)
    c.advance(1.5)
    assert c.blocks['shared'].pins == {'b': 2}
    assert c.protected_byte_seconds == 1536
    c.advance(3)
    assert not c.blocks['shared'].pins
    assert c.start('c', keys) == 1
    assert c.protected_byte_seconds == 2048


def test_active_takeover_does_not_wait_for_own_pin():
    c = Cache(3, 2, 1)
    c.start('a', ('x','y'))
    c.finish('a', ('x','y'), 100)
    assert c.start('a', ('x','y','z')) == 2
    assert len(c.blocks) == 3
    assert all(not b.pins and b.active == {'a'} for b in c.blocks.values())


def test_shared_protection_does_not_count_twice():
    c = Cache(3, 2, 1)
    c.start('a', ('x','y')); c.finish('a', ('x','y'), 5)
    c.start('b', ('x','y')); c.finish('b', ('x','y'), 5)
    assert c.admitted == 2 and c.rejected == 0
    c.unpin('a')
    assert all(b.pins == {'b': 5} for b in c.blocks.values())


def test_physical_slot_generation_changes_on_reuse():
    c = Cache(1, 0, 1)
    c.start('a', ('x',)); old = c.physical_ids(('x',))[0]
    c.finish('a', ('x',), 0)
    c.start('b', ('y',)); new = c.physical_ids(('y',))[0]
    assert old[:3] == new[:3] and new[3] > old[3]


def test_active_other_owner_cannot_be_evicted():
    c = Cache(2, 1, 1)
    c.start('a', ('x','y'))
    assert c.start('b', ('z',)) is None


def test_full_request_too_large_rejected():
    with pytest.raises(ValueError, match='exceeds'):
        Cache(1, 0, 1).start('a', ('x','y'))
