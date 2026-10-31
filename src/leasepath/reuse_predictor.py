"""Weighted discrete competing-risk baseline using observed risk intervals."""
from bisect import bisect_right
from collections import defaultdict
import json
import math
from pathlib import Path

from .d2_hazards import RIGHT_EDGES, derive_hazard_rows
from .prediction_state import validate_features


LABELS = ('U', 'D', 'waiting')


def _edges(values):
    values = tuple(values)
    if (not values or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values)
            or any(a >= b for a, b in zip(values, values[1:]))):
        raise ValueError('edges must be finite, positive and strictly increasing')
    return values


class EmpiricalRiskPredictor:
    """Estimate hU/hD/h0, then accumulate F_U/F_D/survival.

    Fit accepts offline samples and separate targets. Predict accepts only the
    current feature allowlist. No future state is substituted in later bins.
    Weights are supplied by the caller; the runner balances episodes/workflows.
    """

    def __init__(self, *, right_edges=RIGHT_EDGES, conditional=True,
                 elapsed_edges=(.03, .1, .3, 1), min_group_workflows=3,
                 prior_weight=1.):
        self.right_edges = _edges(right_edges)
        self.elapsed_edges = _edges(elapsed_edges)
        if type(conditional) is not bool:
            raise ValueError('conditional must be boolean')
        if type(min_group_workflows) is not int or min_group_workflows < 1:
            raise ValueError('min_group_workflows must be positive')
        if type(prior_weight) not in (int, float) or not math.isfinite(prior_weight) or prior_weight < 0:
            raise ValueError('prior_weight must be finite and nonnegative')
        self.conditional = conditional
        self.min_group_workflows = min_group_workflows
        self.prior_weight = prior_weight
        self.global_counts = None
        self.groups = {}
        self.compatibilities = []

    def _key(self, features):
        return json.dumps([features['tool_name'], features['phase'],
                           bisect_right(self.elapsed_edges, features['elapsed_idle_s'])],
                          ensure_ascii=False, separators=(',', ':'))

    def fit(self, samples, targets):
        if not samples:
            raise ValueError('empty training samples')
        by_id = {}
        for sample in samples:
            validate_features(sample['features'])
            weight = sample['sample_weight']
            if type(weight) not in (int, float) or not math.isfinite(weight) or weight <= 0:
                raise ValueError('sample weights must be finite and positive')
            if not isinstance(sample['run_id'], str) or not sample['run_id']:
                raise ValueError('missing workflow identity')
            by_id[sample['sample_id']] = sample
        if len({s['features']['compatibility_id'] for s in samples}) != 1:
            raise ValueError('fit one backend compatibility at a time')
        rows = derive_hazard_rows(samples, targets, self.right_edges)
        tables = {'global': [[0., 0., 0.] for _ in self.right_edges]}
        workflows = defaultdict(set)
        for row in rows:
            if not row['loss_mask']:
                continue
            sample = by_id[row['sample_id']]
            keys = ['global']
            if self.conditional:
                keys.append(self._key(sample['features']))
            for key in keys:
                counts = tables.setdefault(key, [[0., 0., 0.] for _ in self.right_edges])
                counts[row['interval_index']][LABELS.index(row['label'])] += row['sample_weight']
                workflows[key, row['interval_index']].add(row['run_id'])
        self.global_counts = [
            {'counts': counts, 'workflows': len(workflows['global', k])}
            for k, counts in enumerate(tables.pop('global'))]
        if not any(sum(r['counts']) for r in self.global_counts):
            self.global_counts = None
            raise ValueError('no fully observed risk intervals')
        self.groups = {
            key: [{'counts': counts, 'workflows': len(workflows[key, k])}
                  for k, counts in enumerate(table)] for key, table in tables.items()}
        self.compatibilities = sorted({s['features']['compatibility_id'] for s in samples})
        return self

    def predict(self, features, horizons=None):
        """Return configured bins, optionally selecting exact right-edge horizons.

        A zero global risk set has no estimate. It is not assigned h0=1 or an
        invented event prior, and unsupported cumulative outputs remain null.
        """
        validate_features(features)
        if self.global_counts is None:
            raise ValueError('predictor is not fitted')
        horizons = self.right_edges if horizons is None else tuple(horizons)
        if not horizons or any(type(h) not in (int, float) or h not in self.right_edges for h in horizons):
            raise ValueError('horizons must select configured right edges')
        if any(a >= b for a, b in zip(horizons, horizons[1:])):
            raise ValueError('horizons must be strictly increasing')
        group = self.groups.get(self._key(features))
        cumulative_u, cumulative_d, survival = 0., 0., 1.
        cumulative_supported = True
        output = []
        for k, right in enumerate(self.right_edges):
            record = self.global_counts[k]
            risk = sum(record['counts'])
            row = {'interval_index': k, 'left_s': 0. if k == 0 else self.right_edges[k - 1],
                   'right_s': right, 'global_risk_weight': risk,
                   'global_risk_workflows': record['workflows']}
            source = 'global'
            if features['compatibility_id'] not in self.compatibilities:
                source = 'unsupported_compatibility'
            elif not risk:
                source = 'unsupported_risk_set'
            if source.startswith('unsupported'):
                cumulative_supported = False
                row.update(supported=False, estimate_source=source,
                           hU=None, hD=None, h0=None, F_U=None, F_D=None, survival=None)
            else:
                probabilities = [v / risk for v in record['counts']]
                local = group[k] if group is not None else None
                if (self.conditional and local is not None and sum(local['counts']) > 0
                        and local['workflows'] >= self.min_group_workflows):
                    total = sum(local['counts']) + self.prior_weight
                    probabilities = [(v + self.prior_weight * p) / total
                                     for v, p in zip(local['counts'], probabilities)]
                    source = 'group_shrunk_to_global'
                elif self.conditional:
                    source = 'global_fallback'
                hu, hd, h0 = probabilities
                cumulative_u += survival * hu
                cumulative_d += survival * hd
                survival *= h0
                row.update(supported=cumulative_supported, estimate_source=source,
                           hU=hu, hD=hd, h0=h0,
                           F_U=cumulative_u if cumulative_supported else None,
                           F_D=cumulative_d if cumulative_supported else None,
                           survival=survival if cumulative_supported else None)
            if right in horizons:
                output.append(row)
        return output

    def to_dict(self):
        if self.global_counts is None:
            raise ValueError('predictor is not fitted')
        return {'schema_version': 1, 'kind': 'empirical_competing_risk',
                'labels': list(LABELS), 'configuration': {
                    'right_edges': list(self.right_edges), 'conditional': self.conditional,
                    'elapsed_edges': list(self.elapsed_edges),
                    'min_group_workflows': self.min_group_workflows, 'prior_weight': self.prior_weight},
                'compatibilities': self.compatibilities,
                'global_counts': self.global_counts, 'groups': self.groups}

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False) + '\n')

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        if (data.get('schema_version') != 1 or data.get('kind') != 'empirical_competing_risk'
                or data.get('labels') != list(LABELS)):
            raise ValueError('unsupported empirical model format')
        model = cls(**data['configuration'])
        for table in [data['global_counts'], *data['groups'].values()]:
            if len(table) != len(model.right_edges):
                raise ValueError('model interval count differs')
            for record in table:
                counts = record['counts']
                if (len(counts) != 3 or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in counts)
                        or type(record['workflows']) is not int or record['workflows'] < 0
                        or (sum(counts) > 0) != (record['workflows'] > 0)):
                    raise ValueError('invalid saved risk counts')
        compatibilities = data['compatibilities']
        if len(compatibilities) != 1 or not isinstance(compatibilities[0], str) or not compatibilities[0]:
            raise ValueError('invalid saved compatibility')
        if not any(sum(r['counts']) for r in data['global_counts']):
            raise ValueError('empty saved risk set')
        model.global_counts, model.groups = data['global_counts'], data['groups']
        model.compatibilities = compatibilities
        return model
