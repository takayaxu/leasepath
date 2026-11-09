from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline, FIXED, file_digest
from leasepath.qwen_data_ledger import GenerationBudget

ROOT=Path(__file__).resolve().parents[1]
MANIFEST=ROOT/'configs/baselines/kv_equal_cost_distribution_v1.json'


def test_frozen_baseline_integrity_and_tamper_rejection(tmp_path):
    baseline=FrozenEqualCostBaseline(ROOT,MANIFEST)
    assert baseline.evidence()['fixed']==FIXED
    changed=json.loads(MANIFEST.read_text());changed['fixed']['tau_s']=5.
    path=tmp_path/'modified.json';path.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='parameters differ'):FrozenEqualCostBaseline(ROOT,path)
    changed=json.loads(MANIFEST.read_text());changed['protected_sha256'][changed['predictor']['path']]='0'*64
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='source/model differs'):FrozenEqualCostBaseline(ROOT,path)
    changed=json.loads(MANIFEST.read_text());path.write_text(json.dumps(changed))
    loaded=FrozenEqualCostBaseline(ROOT,path);path.write_text(json.dumps(changed,indent=2))
    with pytest.raises(ValueError,match='manifest changed'):loaded.verify()


def test_capacity_varies_but_cost_discount_and_arm_are_fixed(monkeypatch):
    baseline=FrozenEqualCostBaseline(ROOT,MANIFEST)
    class Predictor:
        def predict(self,features):raise AssertionError('not needed before pressure')
    monkeypatch.setattr(baseline,'predictor',lambda compatibility:Predictor())
    for capacity in [24,32,64]:
        for arm,policy in [('retired_first_lru','retired_first_lru'),('gbdt_point','predicted_reuse'),('gbdt_distribution','distribution_value')]:
            cache=baseline.cache(capacity,runtime_compatibility='verified-runtime',arm=arm)
            assert cache.capacity==capacity and cache.policy==policy
            assert cache.horizon==30 and cache.tau==10 and cache.min_workflows==5
            assert cache.block_size==128 and cache.block_bytes==33554432
            assert cache.cost_table is None and cache.audit_shadows
    with pytest.raises(ValueError,match='only LRU'):baseline.cache(24,runtime_compatibility='x',arm='gbdt_cost')
    with pytest.raises(ValueError,match='cannot override'):baseline.engine('unused','unused',None,cost_table=object())
    with pytest.raises(ValueError,match='unknown comparison'):baseline.engine('unused','unused',None,arm='gbdt_cost')


def test_next_stage_orders_and_caps_are_consistent(tmp_path):
    spec=importlib.util.spec_from_file_location('freeze_next_stage',ROOT/'scripts/freeze_distribution_next_stage.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    plan=json.loads((ROOT/'results/cache_policy/kv_distribution_next_stage_2026-11-09/plan.json').read_text())
    mod.validate(plan)
    counts=plan['known_parent_matrix']['counts'];cells=plan['known_parent_matrix']['cells']
    assert sum(len(c['batches']) for c in cells)==counts['batches']
    assert sum(len(c['batches'])*c['concurrency'] for c in cells)==counts['task_executions']
    assert sum(b['generation_limits']['total'] for c in cells for b in c['batches'])==counts['live_generation_hard_cap']
    for c in cells:
        for b in c['batches']:
            GenerationBudget(tmp_path/b['ledger'],limits=b['generation_limits'])
    for limits in plan['known_parent_matrix']['native_probe_ledgers'].values():
        GenerationBudget(tmp_path/'native/ledger.jsonl',limits=limits)
    modified=deepcopy(plan);modified['known_parent_matrix']['cells'][0]['batches'][0]['policy']='gbdt_cost'
    with pytest.raises(ValueError,match='order changed'):mod.validate(modified)
    modified=deepcopy(plan);modified['new_parent_agent_loops']['suite_status']='collected'
    with pytest.raises(ValueError,match='uncollected'):mod.validate(modified)
