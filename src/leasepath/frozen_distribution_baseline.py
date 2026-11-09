"""Accepted equal-cost baseline: fixed predictor, window and eviction semantics.

Capacity is a workload parameter. Changing scoring, predictor bytes, or cost
weighting requires a new named baseline, not an override to this factory.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .cache_policy_models import TransferredPredictor
from .distribution_cache import DistributionCache

BASELINE_ID = 'gbdt-distribution-equalcost-H30-tau10-v1'
FIXED = {
    'policy': 'distribution_value', 'horizon_s': 30., 'tau_s': 10.,
    'min_global_workflows': 5, 'block_tokens': 128, 'block_bytes': 33554432,
    'cost': 'equal_one', 'owner_aggregation': 'max', 'interval_weight': 'right_edge_exp',
    'fallback': 'retired_first_lru', 'eviction': 'pressure_only_legal_leaf',
    'scheduling': 'FCFS', 'adaptive_tau': False, 'retraining': False,
    'prefetch': False, 'offload': False, 'audit_shadows': True,
}


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class FrozenEqualCostBaseline:
    def __init__(self, repository, manifest):
        self.root = Path(repository).resolve()
        self.manifest_path = Path(manifest).resolve()
        self.document = json.loads(self.manifest_path.read_text())
        if (self.document['baseline_id'] != BASELINE_ID or self.document['status'] != 'accepted'
                or self.document['fixed'] != FIXED):
            raise ValueError('accepted baseline parameters differ; use a new version')
        self.manifest_sha256 = file_digest(self.manifest_path)
        self.verify()
        self._predictor = None

    def verify(self):
        if file_digest(self.manifest_path) != self.manifest_sha256:
            raise ValueError('baseline manifest changed after load')
        for name, digest in self.document['protected_sha256'].items():
            path = (self.root / name).resolve()
            if not path.is_relative_to(self.root) or file_digest(path) != digest:
                raise ValueError('frozen baseline source/model differs: ' + name)
        model = self.document['predictor']
        if self.document['protected_sha256'].get(model['path']) != model['sha256']:
            raise ValueError('predictor not protected by baseline manifest')
        if model['kind'] != 'frozen_gbdt_competing_risk':
            raise ValueError('unexpected predictor kind')

    def predictor(self, runtime_compatibility):
        if self._predictor is None:
            from .gbdt_predictor import GBDTRiskPredictor
            self._predictor = GBDTRiskPredictor.load(self.root / self.document['predictor']['path'])
        return TransferredPredictor(self._predictor,
            runtime_compatibility=runtime_compatibility,
            training_compatibility=self.document['predictor']['training_compatibility'])

    def cache(self, capacity, *, runtime_compatibility, arm='gbdt_distribution'):
        policies = {'retired_first_lru': 'retired_first_lru', 'gbdt_point': 'predicted_reuse',
                    'gbdt_distribution': 'distribution_value'}
        if arm not in policies:
            raise ValueError('only LRU, point, and accepted equal-cost distribution arms')
        return DistributionCache(capacity, block_size=FIXED['block_tokens'],
            block_bytes=FIXED['block_bytes'], policy=policies[arm],
            predictor=self.predictor(runtime_compatibility), horizon_s=FIXED['horizon_s'],
            tau_s=FIXED['tau_s'], min_workflows=FIXED['min_global_workflows'],
            cost_table=None, audit_shadows=True)

    def evidence(self):
        return {'baseline_id': BASELINE_ID, 'manifest_sha256': self.manifest_sha256,
                'predictor_sha256': self.document['predictor']['sha256'],
                'fixed': deepcopy(FIXED)}

    def engine(self, *args, arm='gbdt_distribution', **kwargs):
        from .distribution_cache_backend import DistributionEngine
        if set(kwargs) & {'policy', 'predictor', 'cost_table', 'audit_shadows', 'block_size'}:
            raise ValueError('cannot override frozen baseline engine settings')
        policies = {'retired_first_lru':'retired_first_lru', 'gbdt_point':'predicted_reuse',
                    'gbdt_distribution':'distribution_value'}
        if arm not in policies:
            raise ValueError('unknown comparison arm')
        cache_factory = self

        class BaselineEngine(DistributionEngine):
            def _reset_controller(engine):
                engine.cache = cache_factory.cache(engine.capacity,
                    runtime_compatibility=engine.compatibility, arm=arm)
                engine.slots, engine.expected = {}, {}

        return BaselineEngine(*args, block_size=128, policy=policies[arm],
            predictor=self.predictor(kwargs.get('compatibility', 'qwen32b-cache-stage2')), **kwargs)
