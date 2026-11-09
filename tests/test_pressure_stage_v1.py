import json
from pathlib import Path
import sys
import pytest
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline
from leasepath.pressure_matrix import PressureEngine,check_topology

ROOT=Path(__file__).resolve().parents[1]


def test_matrix_preserves_preregistered_nested_order_and_crossovers():
    original=json.loads((ROOT/'results/cache_policy/kv_distribution_next_stage_2026-11-09/plan.json').read_text())['known_parent_matrix']
    paths=list((ROOT/'configs/prediction/pressure_cells_v1').glob('*.json'))
    assert len(paths)==9
    cells=set()
    for path in paths:
        p=json.loads(path.read_text());n=p['concurrency'];c=p['capacity_blocks'];cells.add((c,n))
        assert p['matrix']==original
        assert p['live_tasks']==original['nested_task_order'][:n]
        assert len(p['matrix']['orders'])==6 and all(len(set(o))==3 for o in p['matrix']['orders'])
    assert cells=={(c,n) for c in (24,32,64) for n in (3,6,10)}


def test_pressure_engine_reset_uses_frozen_factory_for_every_arm():
    baseline=FrozenEqualCostBaseline(ROOT,ROOT/'configs/baselines/kv_equal_cost_distribution_v1.json')
    # Controller reset has no native allocation. Native execution is separately gated.
    engine=PressureEngine.__new__(PressureEngine);engine.baseline=baseline;engine.compatibility='metadata-test'
    for capacity in (24,32,64):
        engine.capacity=capacity
        for arm in ('retired_first_lru','gbdt_point','gbdt_distribution'):
            engine.arm=arm;engine._reset_controller()
            expected=baseline.cache(capacity,runtime_compatibility='metadata-test',arm=arm)
            assert engine.cache.capacity==capacity
            assert engine.cache.policy==expected.policy
            assert engine.cache.cost_table is None
    with pytest.raises(ValueError):engine.select_arm('gbdt_cost')


def test_actual_topology_has_disjoint_tool_and_model_cores():
    p=json.loads((ROOT/'configs/prediction/qwen_distribution_pressure_2026-11-10.json').read_text())
    assert check_topology(p)['status']=='passed'
    p['tool_controls']['ridge_validation']=p['tool_controls']['logistic_holdout']
    with pytest.raises(ValueError,match='overlap'):check_topology(p)
