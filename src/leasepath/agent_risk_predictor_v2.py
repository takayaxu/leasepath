"""Development-only candidate: transferable causal state, not tool/family IDs.

This leaves the accepted predictor and equal-cost cache factory untouched.
Unobserved risk intervals still have no estimate; the cache's five-workflow
support threshold is unchanged. None carries an explicit missing indicator.
"""
from bisect import bisect_right
import json
import math
from pathlib import Path
import tempfile

import numpy as np

from .d2_hazards import RIGHT_EDGES, derive_hazard_rows
from .gbdt_predictor import DEFAULT_PARAMETERS
from .prediction_state import validate_features
from .reuse_predictor import EmpiricalRiskPredictor, LABELS

FEATURE_NAMES = [
    'log_elapsed_idle', 'log_tool_elapsed', 'tool_elapsed_known',
    'ready', 'ready_known', 'log_queue_length', 'queue_known',
    'log_prefix_tokens', 'waiting_tool', 'phase_unknown',
    'log_retry_count', 'retry_known', 'interval_index',
]


def encode(features, interval):
    validate_features(features)
    def value(key):
        return math.log1p(features[key]) if features[key] is not None else -1.
    return [value('elapsed_idle_s'), value('tool_elapsed_s'),
            float(features['tool_elapsed_s'] is not None),
            float(features['is_queued']) if features['is_queued'] is not None else -1.,
            float(features['is_queued'] is not None), value('queue_length'),
            float(features['queue_length'] is not None), value('prefix_tokens'),
            float(features['phase'] == 'waiting_tool'), float(features['phase'] is None),
            value('retry_count'), float(features['retry_count'] is not None), interval]


class AgentEmpiricalV2(EmpiricalRiskPredictor):
    """State/elapsed/queue strata, shrunk to the training-fold global table."""
    def _key(self, features):
        queue = features['queue_length']
        tool = features['tool_elapsed_s']
        return json.dumps([
            features['is_queued'], features['phase'] == 'waiting_tool',
            bisect_right((.1, 1, 3, 10, 30, 60), features['elapsed_idle_s']),
            None if queue is None else bisect_right((1, 3, 6), queue),
            None if tool is None else bisect_right((.1, 1, 3, 10, 30), tool),
        ], separators=(',', ':'))


class AgentGBDTV2:
    def __init__(self):
        self.support = EmpiricalRiskPredictor(right_edges=RIGHT_EDGES, conditional=False)
        self.parameters = dict(DEFAULT_PARAMETERS)
        self.booster = None
        self.training_rows = 0

    def fit(self, samples, targets):
        import lightgbm as lgb
        self.support.fit(samples, targets)
        by_id = {s['sample_id']: s for s in samples}
        rows = [r for r in derive_hazard_rows(samples, targets, self.support.right_edges) if r['loss_mask']]
        dataset = lgb.Dataset(
            np.asarray([encode(by_id[r['sample_id']]['features'], r['interval_index']) for r in rows]),
            label=np.asarray([LABELS.index(r['label']) for r in rows]),
            weight=np.asarray([r['sample_weight'] for r in rows]),
            feature_name=FEATURE_NAMES, free_raw_data=True)
        parameters = {k: v for k, v in self.parameters.items() if k != 'num_boost_round'}
        parameters.update(objective='multiclass', num_class=3, verbosity=-1,
                          num_threads=1, deterministic=True, force_col_wise=True,
                          seed=23, data_random_seed=23, feature_fraction_seed=23,
                          bagging_seed=23, feature_fraction=1., bagging_fraction=1., bagging_freq=0)
        self.booster = lgb.train(parameters, dataset, num_boost_round=self.parameters['num_boost_round'])
        self.training_rows = len(rows)
        return self

    def predict(self, features, horizons=None):
        validate_features(features)
        if self.booster is None:
            raise ValueError('candidate predictor is not fitted')
        output = self.support.predict(features, horizons=horizons)
        if features['compatibility_id'] not in self.support.compatibilities:
            return output
        full = self.support.predict(features)
        supported = [row for row in full if row['supported']]
        if not supported:
            return output
        probabilities = self.booster.predict(
            np.asarray([encode(features, row['interval_index']) for row in supported]), num_threads=1)
        cumulative_u, cumulative_d, survival = 0., 0., 1.
        for row, values in zip(supported, probabilities):
            if len(values) != 3 or not np.all(np.isfinite(values)) or np.any(values < 0) or values.sum() <= 0:
                raise ValueError('invalid candidate hazard probabilities')
            hu, hd, h0 = (values / values.sum()).tolist()
            cumulative_u += survival * hu
            cumulative_d += survival * hd
            survival *= h0
            row.update(hU=hu, hD=hd, h0=h0, F_U=cumulative_u, F_D=cumulative_d,
                       survival=survival, estimate_source='agent_causal_gbdt_v2')
        chosen = {row['interval_index'] for row in output}
        return [row for row in full if row['interval_index'] in chosen]

    def save(self, path):
        if self.booster is None:
            raise ValueError('candidate predictor is not fitted')
        data = dict(schema_version=2, kind='agent_causal_gbdt', feature_names=FEATURE_NAMES,
                    parameters=self.parameters, training_rows=self.training_rows,
                    support_model=self.support.to_dict(), booster_model=self.booster.model_to_string())
        Path(path).write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')

    @classmethod
    def load(cls, path):
        import lightgbm as lgb
        data = json.loads(Path(path).read_text())
        if (data['schema_version'] != 2 or data['kind'] != 'agent_causal_gbdt'
                or data['feature_names'] != FEATURE_NAMES or data['parameters'] != DEFAULT_PARAMETERS):
            raise ValueError('candidate model schema/parameters differ')
        model = cls()
        with tempfile.TemporaryDirectory() as temporary:
            support = Path(temporary) / 'support.json'
            support.write_text(json.dumps(data['support_model']))
            model.support = EmpiricalRiskPredictor.load(support)
        model.booster = lgb.Booster(model_str=data['booster_model'])
        if model.booster.feature_name() != FEATURE_NAMES or model.booster.num_model_per_iteration() != 3:
            raise ValueError('native candidate model schema differs')
        model.training_rows = data['training_rows']
        return model
