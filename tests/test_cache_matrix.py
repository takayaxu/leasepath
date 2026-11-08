from copy import deepcopy
import json
from pathlib import Path

import pytest

from leasepath.cache_matrix import AuditedCache, MatrixBudget
from leasepath.qwen_data_ledger import GenerationBudget
from test_predictive_cache import features, Predictor


def plan():
    return json.loads((Path(__file__).resolve().parents[1]/
                       'configs/prediction/qwen_cache_stage3_2026-11-08.json').read_text())


def test_shadow_is_same_state_and_does_not_overwrite_live_prediction_decision():
    cache = AuditedCache(3, policy='predicted_reuse', predictor=Predictor())
    cache.start('a', ('a',), 0); cache.release('a', features(), 0)
    cache.start('b', ('b',), 0)
    f = features(); f['tool_name'] = 'inspect_input'
    cache.release('b', f, 0)
    result = cache.start('c', ('c', 'cc'), 1)
    assert set(cache.blocks) == {'a', 'c', 'cc'}
    assert [r['key'] for r in result['evicted']] == ['b']
    audit = result['same_state_diagnostic']
    assert audit['retired_first_lru_victims'] == ['a']
    assert audit['victim_set_differs'] and audit['supported_probability_spread']
    assert audit['owner_F_U_3s'] == {'a': .9, 'b': .1}
    cache.check()


def test_matrix_budget_is_global_durable_and_does_not_relax_old_collection(tmp_path):
    p = plan(); path = tmp_path/'matrix.jsonl'
    with pytest.raises(ValueError):
        GenerationBudget(path, limits=p['budget_limits'])
    budget = MatrixBudget(path, p, scope={'capacity': 24, 'concurrency': 3})
    reservation = budget.reserve('pilot', owner='a')
    assert reservation['capacity'] == 24
    assert MatrixBudget(path, p).summary()['pilot'] == 1
    # A persisted exhausted lane is rejected even by a fresh process/object.
    row = {'kind': 'generation_reserved', 'lane': 'pilot', 'budget_limits': p['budget_limits']}
    path.write_text((json.dumps(row)+'\n')*p['budget_limits']['pilot'])
    with pytest.raises(RuntimeError, match='exhausted'):
        MatrixBudget(path, p).reserve('pilot')
    budget.reserve('probe')
    path.with_suffix('.sealed.json').write_text('{}')
    with pytest.raises(RuntimeError, match='sealed'):
        budget.reserve('probe')
    changed = deepcopy(p); changed['budget_limits']['total'] += 1
    with pytest.raises(ValueError):
        MatrixBudget(tmp_path/'other', changed)
