import json
from pathlib import Path
import pytest
from leasepath.qwen_data_ledger import GenerationBudget


def test_planned_shared_phases_respect_existing_caps_and_preserve_order_balance():
    root=Path(__file__).resolve().parents[1]
    plan=json.loads((root/'configs/prediction/qwen_shared_server_paired_2026-11-09.json').read_text())
    total=0
    for phase in plan['phases']:
        total+=phase['budget_limits']['total']
        assert phase['budget_limits']['total']<=252
        assert phase['budget_limits']['pilot']<=228
        assert all(sorted(order)==sorted(plan['policies']) for order in phase['orders'])
        for policy in plan['policies']:
            assert len({sum(order[i]==policy for order in phase['orders']) for i in range(3)})==1
    assert total==plan['total_generation_hard_cap']
    assert len({tuple(x) for x in plan['phases'][1]['orders']})==6
    assert plan['primary_metric'].startswith('natural real-tool')
