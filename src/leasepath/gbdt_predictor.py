"""One regularized LightGBM multiclass model shared across risk intervals."""
from bisect import bisect_right
import json
import math
from pathlib import Path

import numpy as np

from .d2_hazards import derive_hazard_rows
from .prediction_state import validate_features
from .reuse_predictor import EmpiricalRiskPredictor, LABELS


DEFAULT_PARAMETERS = {
    'num_boost_round': 40, 'learning_rate': .05, 'num_leaves': 4,
    'max_depth': 2, 'min_data_in_leaf': 20, 'min_sum_hessian_in_leaf': .1,
    'lambda_l1': .1, 'lambda_l2': 10., 'min_gain_to_split': .01,
}
FEATURE_NAMES = ['tool_name', 'phase', 'elapsed_idle_bin', 'interval_index']


class GBDTRiskPredictor:
    def __init__(self, *, right_edges, elapsed_edges=(.03, .1, .3, 1), parameters=None, seed=23):
        self.support = EmpiricalRiskPredictor(right_edges=right_edges, elapsed_edges=elapsed_edges,
                                             conditional=False)
        self.parameters = dict(DEFAULT_PARAMETERS if parameters is None else parameters)
        if set(self.parameters) != set(DEFAULT_PARAMETERS):
            raise ValueError('unexpected GBDT parameter keys')
        for key, value in self.parameters.items():
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError('invalid GBDT parameter')
        for key in ['num_boost_round', 'num_leaves', 'max_depth', 'min_data_in_leaf']:
            if type(self.parameters[key]) is not int or self.parameters[key] < 1:
                raise ValueError('integer GBDT parameters must be positive')
        if self.parameters['num_leaves'] < 2 or not 0 < self.parameters['learning_rate'] <= 1:
            raise ValueError('invalid tree size or learning rate')
        if type(seed) is not int:
            raise ValueError('seed must be an integer')
        self.seed = seed
        self.vocabularies = {}
        self.elapsed_bins = []
        self.booster = None
        self.training_rows = 0

    def _encode(self, features, interval):
        return [self.vocabularies[key].index(features[key]) + 1 for key in ['tool_name', 'phase']] + [
            bisect_right(self.support.elapsed_edges, features['elapsed_idle_s']), interval]

    def fit(self, samples, targets):
        import lightgbm as lgb

        self.support.fit(samples, targets)
        for key in ['tool_name', 'phase']:
            observed = {s['features'][key] for s in samples}
            self.vocabularies[key] = ([None] if None in observed else []) + sorted(v for v in observed if v is not None)
        self.elapsed_bins = sorted({bisect_right(self.support.elapsed_edges, s['features']['elapsed_idle_s'])
                                    for s in samples})
        by_id = {s['sample_id']: s for s in samples}
        rows = [r for r in derive_hazard_rows(samples, targets, self.support.right_edges) if r['loss_mask']]
        matrix = np.asarray([self._encode(by_id[r['sample_id']]['features'], r['interval_index']) for r in rows], dtype=float)
        labels = np.asarray([LABELS.index(r['label']) for r in rows], dtype=int)
        weights = np.asarray([r['sample_weight'] for r in rows], dtype=float)
        train_set = lgb.Dataset(matrix, label=labels, weight=weights, feature_name=FEATURE_NAMES,
                                categorical_feature=['tool_name', 'phase'], free_raw_data=True)
        parameters = {k: v for k, v in self.parameters.items() if k != 'num_boost_round'}
        parameters.update(objective='multiclass', num_class=3, verbosity=-1,
                          num_threads=1, deterministic=True, force_col_wise=True,
                          seed=self.seed, data_random_seed=self.seed, feature_fraction_seed=self.seed,
                          bagging_seed=self.seed, feature_fraction=1., bagging_fraction=1., bagging_freq=0)
        self.booster = lgb.train(parameters, train_set, num_boost_round=self.parameters['num_boost_round'])
        self.training_rows = len(rows)
        return self

    def predict(self, features, horizons=None):
        validate_features(features)
        if self.booster is None:
            raise ValueError('predictor is not fitted')
        output = self.support.predict(features, horizons=horizons)
        if features['compatibility_id'] not in self.support.compatibilities:
            return output
        # A new category/elapsed bucket is a declared distribution shift.
        # Fall back to the same training-fold global baseline, not an arbitrary category code.
        known = all(features[key] in self.vocabularies[key] for key in ['tool_name', 'phase'])
        known = known and bisect_right(self.support.elapsed_edges, features['elapsed_idle_s']) in self.elapsed_bins
        if not known:
            for row in output:
                if row['supported']:
                    row['estimate_source'] = 'global_fallback_unseen_state'
            return output
        full = self.support.predict(features)
        supported = [r for r in full if r['supported']]
        matrix = np.asarray([self._encode(features, r['interval_index']) for r in supported], dtype=float)
        probabilities = self.booster.predict(matrix, num_threads=1)
        cumulative_u, cumulative_d, survival = 0., 0., 1.
        for row, values in zip(supported, probabilities):
            if len(values) != 3 or not np.all(np.isfinite(values)) or np.any(values < 0):
                raise ValueError('invalid GBDT probability')
            hu, hd, h0 = (values / values.sum()).tolist()
            cumulative_u += survival * hu
            cumulative_d += survival * hd
            survival *= h0
            row.update(hU=hu, hD=hd, h0=h0, F_U=cumulative_u, F_D=cumulative_d,
                       survival=survival, estimate_source='gbdt_shared_multiclass')
        chosen = {r['interval_index'] for r in output}
        return [r for r in full if r['interval_index'] in chosen]

    def save(self, path):
        if self.booster is None:
            raise ValueError('predictor is not fitted')
        import lightgbm
        data = {'schema_version': 1, 'kind': 'gbdt_competing_risk', 'labels': list(LABELS),
                'feature_names': FEATURE_NAMES, 'parameters': self.parameters, 'seed': self.seed,
                'lightgbm_version': lightgbm.__version__, 'training_rows': self.training_rows,
                'vocabularies': self.vocabularies, 'elapsed_bins': self.elapsed_bins,
                'support_model': self.support.to_dict(), 'booster_model': self.booster.model_to_string()}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n')

    @classmethod
    def load(cls, path):
        import lightgbm as lgb
        # Reuse the validated empirical JSON loader for the support table.
        import tempfile

        data = json.loads(Path(path).read_text())
        if (data.get('schema_version') != 1 or data.get('kind') != 'gbdt_competing_risk'
                or data.get('labels') != list(LABELS) or data.get('feature_names') != FEATURE_NAMES):
            raise ValueError('unsupported GBDT model format')
        configuration = data['support_model']['configuration']
        model = cls(right_edges=configuration['right_edges'], elapsed_edges=configuration['elapsed_edges'],
                    parameters=data['parameters'], seed=data['seed'])
        with tempfile.TemporaryDirectory(prefix='leasepath-gbdt-support-') as temporary:
            support_path = Path(temporary) / 'support.json'
            support_path.write_text(json.dumps(data['support_model']))
            model.support = EmpiricalRiskPredictor.load(support_path)
        vocabularies = data['vocabularies']
        if set(vocabularies) != {'tool_name', 'phase'}:
            raise ValueError('invalid GBDT vocabularies')
        for values in vocabularies.values():
            if (not isinstance(values, list) or not values or len(set(values)) != len(values)
                    or any(v is not None and (not isinstance(v, str) or not v) for v in values)):
                raise ValueError('invalid GBDT vocabulary values')
        if (not data['elapsed_bins'] or any(type(v) is not int or not 0 <= v <= len(model.support.elapsed_edges)
                                           for v in data['elapsed_bins'])):
            raise ValueError('invalid GBDT elapsed bins')
        model.vocabularies, model.elapsed_bins = vocabularies, data['elapsed_bins']
        model.booster = lgb.Booster(model_str=data['booster_model'])
        if model.booster.feature_name() != FEATURE_NAMES or model.booster.num_model_per_iteration() != 3:
            raise ValueError('GBDT native model schema differs')
        model.training_rows = data['training_rows']
        return model
