"""Version 1: pressure-only, prefix-constrained discounted recompute value.

The discount is a deterministic weight, not a cache-survival probability.
Costs use only an owner's observed prefix. No future suffix or arrival is read.
Old PredictiveCache and its measured experiments remain unchanged.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import time

from .prediction_training import check_distribution
from .predictive_cache import CapacityUnavailable, PredictiveCache, Resident

VERSION = 'distribution-value-v1'
POLICIES = {'retired_first_lru', 'predicted_reuse', 'distribution_value'}


def distribution_scores(rows, *, horizon=30., tau=10., min_workflows=5):
    """Return separate support verdicts for the old 3s point and new window."""
    if not math.isfinite(horizon) or horizon < 3 or not math.isfinite(tau) or tau <= 0:
        raise ValueError('invalid discount window')
    if type(min_workflows) is not int or min_workflows < 1:
        raise ValueError('invalid support threshold')
    check_distribution(rows)
    edges = [r['right_s'] for r in rows]
    if not edges or any(not math.isfinite(t) or t <= 0 for t in edges) or any(
            b <= a for a, b in zip(edges, edges[1:])):
        raise ValueError('invalid interval edges')
    point = next((r for r in rows if r['right_s'] == 3), None)
    result = {'F_U_3s': point['F_U'] if point and point['supported'] else None,
              'W': None, 'reason': None, 'intervals': []}
    window = [r for r in rows if r['right_s'] <= horizon]
    if not window or window[-1]['right_s'] != horizon:
        return result | {'reason': 'missing_common_horizon'}
    previous, weighted = 0., 0.
    for row in window:
        count = row.get('global_risk_workflows')
        if not row['supported'] or type(count) is not int or count < min_workflows:
            return result | {'reason': 'unsupported_common_window'}
        mass = max(0., row['F_U']-previous)
        weight = math.exp(-row['right_s']/tau)
        result['intervals'].append({'right_s': row['right_s'], 'U_mass': mass,
                                    'weight': weight, 'global_risk_workflows': count})
        weighted += mass*weight
        previous = row['F_U']
    return result | {'W': weighted, 'F_U_H': previous, 'F_D_H': window[-1]['F_D'],
                     'survival_H': window[-1]['survival']}


class MarginalCostTable:
    """Exact-grid, monotone T(P,h), seconds; no interpolation or extrapolation.

    Backend identity must be supplied independently by the caller. Calibration
    provenance/validation must pass before publishing the table to live runs.
    """
    def __init__(self, document, *, backend_identity):
        self.document = deepcopy(document)
        if document['version'] != 1 or document['backend_identity'] != backend_identity:
            raise ValueError('cost table backend/version mismatch')
        if document.get('validation', {}).get('status') != 'passed':
            raise ValueError('cost table calibration unvalidated')
        self.block_size = document['block_tokens']
        if type(self.block_size) is not int or self.block_size < 1:
            raise ValueError('invalid cost geometry')
        self.curves = {}
        for curve in document['curves']:
            prefix = curve['observed_prefix_tokens']
            if type(prefix) is not int or prefix < self.block_size or prefix % self.block_size or prefix in self.curves:
                raise ValueError('invalid/duplicate observed prefix')
            values = {}
            for row in curve['points']:
                h, seconds = row['hit_tokens'], row['prefill_s']
                if type(h) is not int or not 0 <= h <= prefix or h % self.block_size or h in values:
                    raise ValueError('invalid/duplicate hit length')
                if not math.isfinite(seconds) or seconds < 0:
                    raise ValueError('invalid prefill time')
                values[h] = seconds
            ordered = sorted(values)
            if not ordered or any(values[b] > values[a] for a, b in zip(ordered, ordered[1:])):
                raise ValueError('cost curve must be monotone in hit length')
            self.curves[prefix] = values
        self.sha256 = hashlib.sha256(json.dumps(document, sort_keys=True, allow_nan=False).encode()).hexdigest()

    @classmethod
    def load(cls, path, *, backend_identity):
        return cls(json.loads(Path(path).read_text()), backend_identity=backend_identity)

    def marginal(self, prefix, resident_tokens):
        curve = self.curves.get(prefix, {})
        if resident_tokens not in curve or resident_tokens-self.block_size not in curve:
            return None
        return curve[resident_tokens-self.block_size]-curve[resident_tokens]


class _Snapshot:
    def __init__(self, cache, owner, now):
        self.predictor = cache.predictor
        self.parameters = dict(horizon=cache.horizon, tau=cache.tau, min_workflows=cache.min_workflows)
        self.features, self.values = {}, {}
        self.calls, self.ns = 0, 0
        for idle_owner, (features, observed_at) in cache.idle.items():
            if idle_owner == owner:
                continue
            current = deepcopy(features)
            current['elapsed_idle_s'] += now-observed_at
            if current['tool_elapsed_s'] is not None:
                current['tool_elapsed_s'] += now-observed_at
            self.features[idle_owner] = current

    def get(self, owner):
        if owner not in self.values:
            began = time.perf_counter_ns()
            self.calls += 1
            try:
                rows = self.predictor.predict(deepcopy(self.features[owner]))
                score = distribution_scores(rows, **self.parameters)
                # Serialize only a validated distribution; never leak NaN into evidence.
                score['distribution'] = deepcopy(rows)
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                score = {'F_U_3s': None, 'W': None, 'reason': 'invalid_prediction',
                         'error': type(exc).__name__+': '+str(exc)}
            finally:
                self.ns += time.perf_counter_ns()-began
            self.values[owner] = score
        return self.values[owner]


class DistributionCache(PredictiveCache):
    def __init__(self, capacity_blocks, *, block_size=128, block_bytes=33554432,
                 policy='distribution_value', predictor=None, horizon_s=30., tau_s=10.,
                 min_workflows=5, cost_table=None, audit_shadows=False):
        super().__init__(capacity_blocks, block_size=block_size, block_bytes=block_bytes)
        if policy not in POLICIES or (policy != 'retired_first_lru' or audit_shadows) and predictor is None:
            raise ValueError('policy/predictor mismatch')
        if not math.isfinite(horizon_s) or horizon_s < 3 or not math.isfinite(tau_s) or tau_s <= 0:
            raise ValueError('invalid horizon/discount')
        if type(min_workflows) is not int or min_workflows < 1:
            raise ValueError('invalid support threshold')
        if cost_table is not None and cost_table.block_size != block_size:
            raise ValueError('cost block geometry mismatch')
        self.policy, self.predictor = policy, predictor
        self.horizon, self.tau, self.min_workflows = horizon_s, tau_s, min_workflows
        self.cost_table, self.audit_shadows = cost_table, audit_shadows
        self.cost_fallback_sweeps = 0

    def _validate_admission(self, owner, keys, now):
        if not math.isfinite(now) or now < self.now:
            raise ValueError('time must be finite and monotonic')
        if not isinstance(owner, str) or not owner or any(not isinstance(k, str) or not k for k in keys) or len(set(keys)) != len(keys):
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

    def _clone(self, policy):
        clone = DistributionCache(self.capacity, block_size=self.block_size, block_bytes=self.block_bytes,
            policy=policy, predictor=self.predictor, horizon_s=self.horizon, tau_s=self.tau,
            min_workflows=self.min_workflows)
        clone.blocks, clone.bindings, clone.idle = deepcopy((self.blocks, self.bindings, self.idle))
        clone.now, clone.clock = self.now, self.clock
        return clone

    def start(self, owner, keys, now):
        began = time.perf_counter_ns()
        keys = tuple(keys)
        self._validate_admission(owner, keys, now)
        snapshot = _Snapshot(self, owner, now)
        shadows = {p: self._clone(p) for p in ('retired_first_lru', 'predicted_reuse')} if self.audit_shadows else {}
        result = self._admit(owner, keys, now, snapshot)
        alternatives = {p: c._admit(owner, keys, now, snapshot) for p, c in shadows.items()}
        self.prediction_calls += snapshot.calls
        self.prediction_ns += snapshot.ns
        result['distribution_audit'].update(owner_scores=deepcopy(snapshot.values),
            observed_idle_features=snapshot.features, prediction_calls=snapshot.calls, prediction_ns=snapshot.ns)
        actual = [r['key'] for r in result['evicted']]
        if shadows:
            lru = [r['key'] for r in alternatives['retired_first_lru']['evicted']]
            point = [r['key'] for r in alternatives['predicted_reuse']['evicted']]
            result['same_state_diagnostic'] = {'retired_first_lru_victims': lru,
                'F_U_3s_victims': point, 'victim_sequence_differs': actual != lru,
                'victim_set_differs': set(actual) != set(lru),
                'point_victim_sequence_differs': actual != point,
                'point_victim_set_differs': set(actual) != set(point),
                'owner_F_U_3s': {o: v['F_U_3s'] for o, v in snapshot.values.items()},
                'observed_idle_features': snapshot.features,
                'supported_probability_spread': len({v['F_U_3s'] for v in snapshot.values.values() if v['F_U_3s'] is not None}) > 1,
                'admission_with_shadow_total_ns': time.perf_counter_ns()-began}
        return result

    def _admit(self, owner, keys, now, snapshot):
        self.advance(now)
        hit = self.prefix_hits(keys)
        self.idle.pop(owner, None)
        self.bindings[owner] = keys
        missing = len(keys)-hit
        self.last_evictions = []
        sweeps, fallback, cost_fallback = [], False, False
        while len(self.blocks)+missing > self.capacity:
            owners = self._live_owners()
            parents = {b.parent for b in self.blocks.values() if b.parent is not None}
            leaves = sorted(k for k, b in self.blocks.items() if k not in keys and not b.active and k not in parents)
            if not leaves:
                raise AssertionError('no legal leaf despite validated admission')
            # Unbound leaves do not need prediction, and always precede live ones.
            unbound = [k for k in leaves if not owners.get(k)]
            candidates = unbound or leaves
            live_owners = sorted({o for k in candidates for o in owners.get(k, ())})
            field = 'F_U_3s' if self.policy == 'predicted_reuse' else 'W'
            scores = {o: snapshot.get(o) for o in live_owners} if self.policy != 'retired_first_lru' else {}
            fallback = fallback or any(s[field] is None for s in scores.values())
            costs = {}
            if self.policy == 'distribution_value' and not fallback and self.cost_table is not None:
                for o in live_owners:
                    prefix = snapshot.features[o]['prefix_tokens']
                    resident = self.prefix_hits(self.bindings[o])*self.block_size
                    costs[o] = self.cost_table.marginal(prefix, resident)
                cost_fallback = cost_fallback or any(c is None for c in costs.values())
            self.fallback_sweeps += bool(fallback and live_owners)
            self.cost_fallback_sweeps += bool(cost_fallback and live_owners)
            ranks, details = {}, []
            for key in candidates:
                block, live = self.blocks[key], sorted(owners.get(key, ()))
                owner_values = []
                for o in live:
                    cost = costs.get(o) if self.cost_table is not None and not cost_fallback and not fallback and self.policy == 'distribution_value' else 1.
                    weight = scores[o][field] if scores and not fallback else 0.
                    owner_values.append({'owner': o, 'weight': weight, 'marginal_cost': cost,
                        'observed_prefix_tokens': snapshot.features[o]['prefix_tokens'],
                        'resident_prefix_tokens': self.prefix_hits(self.bindings[o])*self.block_size,
                        'weighted_cost': cost*weight})
                value = max((v['weighted_cost'] for v in owner_values), default=0.)/self.block_bytes
                rank = (bool(live), value, block.touched, -block.depth, key)
                ranks[key] = rank
                details.append({'key': key, 'owners': owner_values, 'block_bytes': self.block_bytes,
                                'value': value, 'rank': list(rank)})
            victim = min(candidates, key=ranks.__getitem__)
            reason = ('unbound_leaf' if unbound else 'lru_fallback_unsupported' if fallback else self.policy)
            sweeps.append({'legal_leaves': leaves, 'candidates': details, 'victim': victim,
                           'distribution_fallback': fallback, 'cost_fallback': cost_fallback,
                           'cost_units': 'prefill_seconds' if costs and not cost_fallback and not fallback else 'equal_cost_unit'})
            self.last_evictions.append({'key': victim, 'live_owners': sorted(owners.get(victim, ())),
                'reason': reason, 'value': ranks[victim][1]})
            del self.blocks[victim]
            self.evictions += 1
        for index, key in enumerate(keys):
            self.clock += 1
            if key not in self.blocks:
                self.blocks[key] = Resident(keys[index-1] if index else None, index, self.clock)
            self.blocks[key].active.add(owner)
            self.blocks[key].touched = self.clock
        self.check()
        return {'cached_blocks': hit, 'evicted': deepcopy(self.last_evictions), 'resident_blocks': len(self.blocks),
            'distribution_audit': {'version': VERSION, 'policy': self.policy, 't0_s': now,
                'H_s': self.horizon, 'tau_s': self.tau, 'min_global_workflows': self.min_workflows,
                'cost_table_sha256': self.cost_table.sha256 if self.cost_table else None, 'sweeps': sweeps}}
