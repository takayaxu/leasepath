from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from continue_disk_pressure_v1 import comparison, schedule


def test_more_hits_can_still_have_negative_jct_benefit():
    rows = []
    for cycle in range(6):
        for arm, jct, cached, prefill in [('retired_first_lru', 10, 128, 3),
                                         ('gbdt_point', 11, 256, 2),
                                         ('gbdt_distribution', 12, 384, 1)]:
            rows.append({'cycle': cycle, 'policy': arm, 'mean_jct_s': jct,
                         'cached_tokens': cached, 'prefill_tokens': 1024-cached,
                         'prefill_s': prefill, 'decode_s': 1})
    result = {'capacity': 24, 'concurrency': 3, 'metrics': rows}
    benefit = comparison(result)['distribution_benefits']['retired_first_lru']
    assert benefit['additional_hit_tokens_per_batch'] == 256
    assert benefit['prefill_s_saved_per_batch'] == 2
    assert benefit['mean_JCT_s_saved'] == -2
    assert benefit['mean_JCT_reduction_pct'] == -20
    assert benefit['paired_JCT_s_saved'] == [-2] * 6
    rows[-1]['cycle'] = 4
    with pytest.raises(ValueError, match='unpaired'):
        comparison(result)


def test_each_capacity_diagnostic_precedes_natural_cells():
    cells = [{'capacity_blocks': c, 'concurrency': n} for n in (3, 6, 10) for c in (24, 32, 64)]
    steps = schedule(cells)
    assert len(steps) == 12
    accepted = set()
    natural = []
    for step in steps:
        if step['diagnostic']:
            assert step['concurrency'] == 10
            accepted.add(step['capacity'])
        else:
            assert step['capacity'] in accepted
            natural.append((step['capacity'], step['concurrency']))
    assert natural == [(c, n) for n in (3, 6, 10) for c in (24, 32, 64)]
