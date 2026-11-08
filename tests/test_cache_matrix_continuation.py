from pathlib import Path
import json

import pytest

from leasepath.cache_matrix_continuation import ContinuedMatrixBudget
from leasepath.prediction_training import sha256


def test_failed_attempt_is_sealed_and_counted_before_one_bounded_revision(tmp_path):
    root = Path(__file__).resolve().parents[1]
    plan = json.loads((root/'configs/prediction/qwen_cache_stage3b_2026-11-08.json').read_text())
    ledger = tmp_path/'prior.jsonl'
    ledger.write_text((json.dumps({'kind':'generation_reserved'})+'\n')*113)
    audit = tmp_path/'audit.json'
    audit.write_text(json.dumps({'status':'passed','experiment_status':'quality_failed_and_stopped',
                                'requests_checked':113,'ledger_sha256':sha256(ledger)}))
    plan.update(prior_failed_ledger=str(ledger),prior_failed_ledger_sha256=sha256(ledger),
                prior_failed_audit=str(audit),prior_failed_audit_sha256=sha256(audit))
    with pytest.raises(ValueError, match='sealed'):
        ContinuedMatrixBudget(tmp_path/'new', plan)
    ledger.with_suffix('.sealed.json').write_text('{}')
    budget = ContinuedMatrixBudget(tmp_path/'new', plan)
    assert budget.limits['total']+113 == 3117
    budget.reserve('probe')
    assert budget.summary()['generations'] == 1
    plan['budget_limits']['total'] += 1
    with pytest.raises(ValueError, match='cap'):
        ContinuedMatrixBudget(tmp_path/'other', plan)
    ledger.write_text(ledger.read_text()+'{}\n')
    with pytest.raises(ValueError, match='changed'):
        ContinuedMatrixBudget(tmp_path/'other', plan)
