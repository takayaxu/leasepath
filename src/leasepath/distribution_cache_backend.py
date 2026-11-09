"""Versioned native integration; native slot/drop/abort semantics are unchanged."""
from .distribution_cache import DistributionCache
from .qwen_cache_backend import MultiOwnerEngine


class DistributionEngine(MultiOwnerEngine):
    def __init__(self, *args, cost_table=None, audit_shadows=True, **kwargs):
        self.cost_table, self.audit_shadows = cost_table, audit_shadows
        super().__init__(*args, **kwargs)

    def _reset_controller(self):
        self.cache = DistributionCache(self.capacity, block_size=self.block_size,
            block_bytes=262144*self.block_size, policy=self.policy, predictor=self.predictor,
            cost_table=self.cost_table, audit_shadows=self.audit_shadows)
        self.slots, self.expected = {}, {}
