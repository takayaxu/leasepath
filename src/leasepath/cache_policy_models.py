"""Explicit statistical transfer between verified backend compatibility domains."""
from copy import deepcopy

from .prediction_state import validate_features


class TransferredPredictor:
    def __init__(self, predictor, *, runtime_compatibility, training_compatibility):
        self.predictor = predictor
        self.runtime = runtime_compatibility
        self.training = training_compatibility
        support = getattr(predictor, 'support', predictor)
        if support.compatibilities != [training_compatibility]:
            raise ValueError('unexpected training compatibility domain')

    def predict(self, features):
        validate_features(features)
        if features['compatibility_id'] != self.runtime:
            raise ValueError('statistical transfer requires verified runtime domain')
        current = deepcopy(features)
        current['compatibility_id'] = self.training
        return self.predictor.predict(current)


class FixedTTLPriority:
    """Fixed-duration soft protection, re-evaluated at pressure; never blocks active work."""
    def __init__(self, ttl_s=1.):
        self.ttl = ttl_s

    def predict(self, features):
        validate_features(features)
        p = float(features['elapsed_idle_s'] < self.ttl)
        return [{'right_s': 3., 'supported': True, 'hU': p, 'hD': 0., 'h0': 1-p,
                 'F_U': p, 'F_D': 0., 'survival': 1-p, 'estimate_source': 'fixed_ttl_soft_priority'}]
