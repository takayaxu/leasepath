"""One declared protocol revision within the original stage's total cap."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .cache_matrix import MatrixBudget
from .prediction_training import sha256


class ContinuedMatrixBudget(MatrixBudget):
    def __init__(self, path, plan, *, scope=None):
        prior = Path(plan['prior_failed_ledger'])
        if not prior.with_suffix('.sealed.json').exists():
            raise ValueError('prior attempt must be sealed')
        if sha256(prior) != plan['prior_failed_ledger_sha256']:
            raise ValueError('prior ledger changed')
        rows = [json.loads(line) for line in prior.read_text().splitlines()]
        used = sum(r['kind'] == 'generation_reserved' for r in rows)
        if sha256(plan['prior_failed_audit']) != plan['prior_failed_audit_sha256']:
            raise ValueError('prior audit changed')
        audit = json.loads(Path(plan['prior_failed_audit']).read_text())
        if (audit['status'] != 'passed' or audit['experiment_status'] != 'quality_failed_and_stopped'
                or audit['requests_checked'] != used
                or audit['ledger_sha256'] != sha256(prior)
                or used != plan['prior_failed_generations']
                or plan['stage_generation_total_cap'] != 3117):
            raise ValueError('prior attempt is not independently accounted')
        remaining = 3117-used
        limits = {'probe': 39, 'pilot': remaining-39, 'total': remaining}
        if plan['budget_limits'] != limits:
            raise ValueError('continuation exceeds original total cap or reallocates silently')
        baseline = deepcopy(plan)
        baseline['budget_limits'] = {'probe': 39, 'pilot': 3078, 'total': 3117}
        super().__init__(path, baseline, scope=scope)
        self.limits = limits
        self.plan_sha256 = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
