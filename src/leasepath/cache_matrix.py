"""Fixed stage-3 budget and same-state eviction diagnostics.

The shadow LRU never generates requests or changes the live cache. Its timings
are included in JCT for every policy; it explains decisions, not causal savings.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from .predictive_cache import PredictiveCache
from .qwen_cache_backend import MultiOwnerEngine
from .qwen_data_ledger import GenerationBudget


class MatrixBudget(GenerationBudget):
    """A fresh explicitly bounded experiment; old collection caps stay intact."""
    def __init__(self, path, plan, *, scope=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        expected = (len(plan['capacities_blocks'])*sum(plan['concurrencies'])*
                    len(plan['policies'])*plan['repeats']*plan['model_steps_per_task_max'])
        self.limits = plan['budget_limits']
        if (self.limits != {'probe': 13*len(plan['capacities_blocks']),
                            'pilot': expected, 'total': expected+13*len(plan['capacities_blocks'])}
                or plan['capacities_blocks'] != [24, 32, 64]
                or plan['concurrencies'] != [3, 6, 10]
                or plan['policies'] != ['retired_first_lru', 'conditional', 'gbdt']
                or plan['repeats'] != 3 or plan['model_steps_per_task_max'] != 6):
            raise ValueError('matrix differs from fixed bounded contract')
        self.scope = dict(scope or {})
        self.plan_sha256 = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()

    def reserve(self, lane, **metadata):
        return super().reserve(lane, **{**metadata, **self.scope, 'matrix_plan_sha256': self.plan_sha256})


class AuditedCache(PredictiveCache):
    def _probabilities(self):
        self.audit_probabilities = super()._probabilities()
        return self.audit_probabilities

    def start(self, owner, keys, now):
        began = time.perf_counter_ns()
        shadow = PredictiveCache(self.capacity, block_size=self.block_size,
                                 block_bytes=self.block_bytes, policy='retired_first_lru')
        shadow.blocks, shadow.bindings, shadow.idle = deepcopy((self.blocks, self.bindings, self.idle))
        shadow.clock, shadow.now = self.clock, self.now
        self.audit_probabilities = {}
        observed_features = {}
        for idle_owner, (features, observed_at) in self.idle.items():
            if idle_owner == owner:
                continue
            current = deepcopy(features)
            current['elapsed_idle_s'] += now-observed_at
            if current['tool_elapsed_s'] is not None:
                current['tool_elapsed_s'] += now-observed_at
            observed_features[idle_owner] = current
        decision = super().start(owner, keys, now)
        actual = [r['key'] for r in decision['evicted']]
        baseline = shadow.start(owner, keys, now)
        alternative = [r['key'] for r in baseline['evicted']]
        # All recorded information was available at this exact admission.
        decision['same_state_diagnostic'] = {
            'retired_first_lru_victims': alternative,
            'victim_sequence_differs': actual != alternative,
            'victim_set_differs': set(actual) != set(alternative),
            'owner_F_U_3s': self.audit_probabilities,
            'observed_idle_features': observed_features,
            'supported_probability_spread':
                len({v for v in self.audit_probabilities.values() if v is not None}) > 1,
            'admission_with_shadow_total_ns': time.perf_counter_ns()-began}
        return decision


class AuditedEngine(MultiOwnerEngine):
    def _reset_controller(self):
        self.cache = AuditedCache(self.capacity, block_size=self.block_size,
            block_bytes=262144*self.block_size, policy=self.policy,
            predictor=self.predictor, horizon_s=3.)
        self.slots, self.expected = {}, {}
