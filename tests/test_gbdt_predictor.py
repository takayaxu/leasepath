from copy import deepcopy
import json

import pytest

pytest.importorskip('lightgbm')

from leasepath.gbdt_predictor import DEFAULT_PARAMETERS, GBDTRiskPredictor
from leasepath.prediction_training import check_distribution


def features(**changes):
    return {'elapsed_idle_s': .05, 'compatibility_id': 'backend', 'prefix_tokens': 128,
            'tool_name': 'bash', 'tool_elapsed_s': None, 'phase': 'waiting_tool',
            'retry_count': None, 'is_queued': None, 'queue_length': None, **changes}


def training_data():
    samples, targets = [], []
    for index in range(90):
        state = index % 3
        current = features(phase=['queued', 'waiting_tool', None][state])
        samples.append({'sample_id': str(index), 'run_id': 'run-' + str(index),
                        'idle_episode_id': 'episode-' + str(index), 'features': current,
                        'sample_weight': 1., 'split': 'pilot_development_only'})
        targets.append({'sample_id': str(index), 'outcome': 'retire' if state == 2 else 'legal_demand',
                        'remaining_s': [.005, .15, .02][state]})
    return samples, targets


@pytest.fixture(scope='module')
def fitted():
    samples, targets = training_data()
    return GBDTRiskPredictor(right_edges=[.01, .03, .1, .3, 1]).fit(samples, targets)


def test_one_shared_three_class_tree_model_learns_state_and_interval(fitted):
    queued = fitted.predict(features(phase='queued'))
    tool = fitted.predict(features())
    check_distribution(queued)
    check_distribution(tool)
    assert queued[0]['hU'] > tool[0]['hU']
    assert tool[0]['h0'] > queued[0]['h0']
    assert fitted.booster.num_model_per_iteration() == 3
    assert fitted.booster.feature_name() == ['tool_name', 'phase', 'elapsed_idle_bin', 'interval_index']
    assert max(t['num_leaves'] for t in fitted.booster.dump_model()['tree_info']) <= 4
    assert all(r['estimate_source'] == 'gbdt_shared_multiclass' for r in queued[:4])
    assert queued[-1]['supported'] is False and queued[-1]['hU'] is None


def test_model_roundtrip_and_selected_horizon_preserve_cumulative_probabilities(fitted, tmp_path):
    path = tmp_path / 'model.json'
    fitted.save(path)
    restored = GBDTRiskPredictor.load(path)
    assert restored.predict(features()) == fitted.predict(features())
    assert restored.predict(features(), [.3]) == [fitted.predict(features())[3]]
    data = json.loads(path.read_text())
    data['feature_names'].append('future_outcome')
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='format'):
        GBDTRiskPredictor.load(path)


def test_unseen_states_fall_back_without_extending_training_vocabulary(fitted):
    vocabulary = deepcopy(fitted.vocabularies)
    prediction = fitted.predict(features(tool_name='held-out-tool'))
    assert prediction[0]['estimate_source'] == 'global_fallback_unseen_state'
    assert prediction == [{**r, 'estimate_source': 'global_fallback_unseen_state'} if r['supported'] else r
                          for r in fitted.support.predict(features(tool_name='held-out-tool'))]
    assert fitted.vocabularies == vocabulary
    assert fitted.predict(features(elapsed_idle_s=10.))[0]['estimate_source'] == 'global_fallback_unseen_state'
    assert fitted.predict(features(compatibility_id='other-backend'))[0]['supported'] is False
    with pytest.raises(ValueError, match='allowlist'):
        fitted.predict(features(outcome='legal_demand'))


def test_partial_censor_rows_are_not_given_fake_waiting_or_a_fourth_label():
    samples, targets = training_data()
    samples, targets = samples[:3], targets[:3]
    targets[0].update(outcome='legal_demand', remaining_s=.005)
    targets[1].update(outcome='retire', remaining_s=.02)
    targets[2].update(outcome='right_censored', remaining_s=.12)
    model = GBDTRiskPredictor(right_edges=[.01, .03, .1, .3], parameters={
        **DEFAULT_PARAMETERS, 'num_boost_round': 2, 'min_data_in_leaf': 1}).fit(samples, targets)
    # U: 1 row, D: 2 rows, C: 3 fully observed waiting rows; partial fourth is masked.
    assert model.training_rows == 6
    assert model.booster.num_model_per_iteration() == 3
    assert model.predict(features())[-1]['supported'] is False


def test_fixed_seed_reproduces_native_model_and_predictions(fitted):
    samples, targets = training_data()
    again = GBDTRiskPredictor(right_edges=[.01, .03, .1, .3, 1]).fit(samples, targets)
    assert again.booster.model_to_string() == fitted.booster.model_to_string()
    assert again.predict(features()) == fitted.predict(features())
