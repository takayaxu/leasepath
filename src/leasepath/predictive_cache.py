"""Causal, pressure-only KV eviction. No future requests or labels enter here.

The controller uses probability of reuse within a fixed horizon as an equal-
block recompute-value proxy. It does not claim calibrated milliseconds saved.
Active blocks are never evicted; only leaves are removed, preserving prefix
closure. Expensive hard leases that can block ready work are deliberately absent.
"""
from dataclasses import dataclass, field
import math
from copy import deepcopy
import time

from .prediction_state import validate_features
from .prediction_training import check_distribution


class CapacityUnavailable(RuntimeError):
    pass


@dataclass
class Resident:
    parent: str | None
    depth: int
    touched: int
    active: set = field(default_factory=set)


class PredictiveCache:
    def __init__(self, capacity_blocks, *, block_size=128, block_bytes=1,
                 policy='retired_first_lru', predictor=None, horizon_s=3.):
        if type(capacity_blocks) is not int or capacity_blocks < 1:
            raise ValueError('positive integer block capacity required')
        if type(block_size) is not int or block_size < 1 or type(block_bytes) is not int or block_bytes < 1:
            raise ValueError('positive block geometry required')
        if policy not in {'lru', 'retired_first_lru', 'predicted_reuse'}:
            raise ValueError('unknown policy')
        if (policy == 'predicted_reuse') != (predictor is not None):
            raise ValueError('predictor privilege mismatch')
        if not math.isfinite(horizon_s) or horizon_s <= 0:
            raise ValueError('invalid horizon')
        self.capacity, self.block_size, self.block_bytes = capacity_blocks, block_size, block_bytes
        self.policy, self.predictor, self.horizon = policy, predictor, horizon_s
        self.blocks, self.bindings, self.idle = {}, {}, {}
        self.clock, self.now = 0, 0.
        self.evictions, self.prediction_calls, self.prediction_ns = 0, 0, 0
        self.fallback_sweeps = 0
        self.last_evictions = []

    def advance(self, now):
        if not math.isfinite(now) or now < self.now:
            raise ValueError('time must be finite and monotonic')
        self.now = now

    def prefix_hits(self, keys):
        for index, key in enumerate(keys):
            if key not in self.blocks:
                return index
        return len(keys)

    def _live_owners(self):
        owners = {}
        for owner, keys in self.bindings.items():
            if owner not in self.idle:
                continue
            for key in keys[:self.prefix_hits(keys)]:
                owners.setdefault(key, set()).add(owner)
        return owners

    def _probabilities(self):
        values = {}
        for owner, (features, observed_at) in self.idle.items():
            # Only advance clocks of a state already observed; never infer a future tool end.
            current = deepcopy(features)
            elapsed = self.now - observed_at
            current['elapsed_idle_s'] += elapsed
            if current['tool_elapsed_s'] is not None:
                current['tool_elapsed_s'] += elapsed
            began = time.perf_counter_ns()
            distribution = self.predictor.predict(current)
            self.prediction_ns += time.perf_counter_ns() - began
            self.prediction_calls += 1
            check_distribution(distribution)
            row = next((r for r in distribution if r['right_s'] == self.horizon), None)
            values[owner] = row['F_U'] if row is not None and row['supported'] else None
        return values

    def start(self, owner, keys, now):
        """Atomically reject insufficient active capacity; then evict idle leaves.

        Returns logical actions for a native adapter to enact and independently
        verify. This class itself stores metadata, never device KV tensors.
        """
        self.advance(now)
        keys = tuple(keys)
        if not isinstance(owner, str) or not owner or len(set(keys)) != len(keys):
            raise ValueError('invalid owner or prefix')
        if len(keys) > self.capacity:
            raise ValueError('request exceeds cache capacity')
        if any(owner in b.active for b in self.blocks.values()):
            raise ValueError('owner already active')
        active = {k for k, b in self.blocks.items() if b.active}
        if len(active | set(keys)) > self.capacity:
            raise CapacityUnavailable('active KV prevents admission')
        for index, key in enumerate(keys):
            if key in self.blocks and self.blocks[key].parent != (keys[index-1] if index else None):
                raise ValueError('inconsistent prefix ancestry')
        hit = self.prefix_hits(keys)
        self.idle.pop(owner, None)
        self.bindings[owner] = keys
        missing = len(keys) - hit
        owners = self._live_owners()
        values = self._probabilities() if self.predictor is not None and len(self.blocks) + missing > self.capacity else {}
        self.last_evictions = []
        while len(self.blocks) + missing > self.capacity:
            parents = {b.parent for b in self.blocks.values() if b.parent is not None}
            candidates = [k for k, b in self.blocks.items() if k not in keys and not b.active and k not in parents]
            if not candidates:
                raise AssertionError('no legal leaf despite checked active capacity')
            fallback = self.policy == 'predicted_reuse' and any(
                values.get(o) is None for k in candidates for o in owners.get(k, ()))
            self.fallback_sweeps += fallback
            def rank(key):
                block = self.blocks[key]
                live = owners.get(key, set())
                if self.policy == 'lru':
                    return (block.touched, -block.depth, key)
                # max prevents double-counting the same physical block shared by owners.
                value = max((values[o] for o in live), default=0.) / self.block_bytes if not fallback and values else 0.
                return (bool(live), value if self.policy == 'predicted_reuse' else 0.,
                        block.touched, -block.depth, key)
            victim = min(candidates, key=rank)
            live = owners.get(victim, set())
            self.last_evictions.append({'key': victim, 'live_owners': sorted(live),
                'reason': 'lru_fallback_unsupported' if fallback else self.policy,
                'reuse_probability_proxy': max((values[o] for o in live), default=0.) if values and not fallback else None})
            del self.blocks[victim]
            self.evictions += 1
        for index, key in enumerate(keys):
            self.clock += 1
            if key not in self.blocks:
                self.blocks[key] = Resident(keys[index-1] if index else None, index, self.clock)
            self.blocks[key].active.add(owner)
            self.blocks[key].touched = self.clock
        self.check()
        return {'cached_blocks': hit, 'evicted': deepcopy(self.last_evictions),
                'resident_blocks': len(self.blocks)}

    def release(self, owner, features, now):
        self.advance(now)
        validate_features(features)
        if owner not in self.bindings or not all(owner in self.blocks[k].active for k in self.bindings[owner]):
            raise ValueError('release requires the active prefix')
        for key in self.bindings[owner]:
            self.blocks[key].active.remove(owner)
        self.idle[owner] = (deepcopy(features), now)
        self.check()

    def observe(self, owner, features, now):
        self.advance(now)
        validate_features(features)
        if owner not in self.idle:
            raise ValueError('observation requires open idle context')
        self.idle[owner] = (deepcopy(features), now)

    def retire(self, owner, now):
        self.advance(now)
        if any(owner in b.active for b in self.blocks.values()):
            raise ValueError('cannot retire active request')
        self.idle.pop(owner, None)
        self.bindings.pop(owner, None)
        # Retiring one owner leaves shared/resident cells intact until pressure.

    def check(self):
        if len(self.blocks) > self.capacity:
            raise AssertionError('capacity exceeded')
        for key, block in self.blocks.items():
            if block.parent is not None and block.parent not in self.blocks:
                raise AssertionError('orphaned suffix')
            for owner in block.active:
                if key not in self.bindings[owner] or owner in self.idle:
                    raise AssertionError('invalid active ownership')
