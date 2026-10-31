from copy import deepcopy
import json

import pytest

from leasepath.reuse_predictor import EmpiricalRiskPredictor


def features(**changes):
    return {'elapsed_idle_s': 0., 'compatibility_id': 'backend', 'prefix_tokens': 128,
            'tool_name': 'bash', 'tool_elapsed_s': None, 'phase': 'waiting_tool',
            'retry_count': None, 'is_queued': None, 'queue_length': None, **changes}


def data(specifications):
    samples, targets = [], []
    for index, (outcome, remaining, weight) in enumerate(specifications):
        identity = str(index)
        samples.append({'sample_id': identity, 'run_id': 'run-' + identity,
                        'idle_episode_id': 'episode-' + identity, 'features': features(),
                        'sample_weight': weight, 'split': 'pilot_development_only'})
        targets.append({'sample_id': identity, 'outcome': outcome, 'remaining_s': remaining})
    return samples, targets


def test_competing_events_share_risk_set_and_partial_censor_is_masked():
    samples, targets = data([('legal_demand', .05, 1.), ('retire', .15, 1.),
                             ('right_censored', .12, 1.)])
    model = EmpiricalRiskPredictor(right_edges=[.1, .2, .3], conditional=False).fit(samples, targets)
    first, second, tail = model.predict(features())
    assert [first[k] for k in ['hU', 'hD', 'h0']] == pytest.approx([1/3, 0, 2/3])
    assert [second[k] for k in ['hU', 'hD', 'h0']] == pytest.approx([0, 1, 0])
    assert [second[k] for k in ['F_U', 'F_D', 'survival']] == pytest.approx([1/3, 2/3, 0])
    assert second['global_risk_workflows'] == 1
    assert tail['supported'] is False
    assert tail['h0'] is None and tail['F_U'] is None


def test_boundary_censor_provides_waiting_but_never_becomes_a_competing_event():
    samples, targets = data([('legal_demand', .1, 1.), ('right_censored', .1, 1.)])
    model = EmpiricalRiskPredictor(right_edges=[.1, .2], conditional=False).fit(samples, targets)
    first, second = model.predict(features())
    assert [first[k] for k in ['hU', 'hD', 'h0']] == [.5, 0., .5]
    assert second['supported'] is False


def test_sample_weights_change_risk_estimate_and_repeated_snapshots_do_not_add_mass():
    samples, targets = data([('legal_demand', .05, 2.), ('retire', .05, 1.)])
    original = EmpiricalRiskPredictor(right_edges=[.1], conditional=False).fit(samples, targets)
    duplicate, label = deepcopy(samples[0]), deepcopy(targets[0])
    samples[0]['sample_weight'] = duplicate['sample_weight'] = 1.
    duplicate['sample_id'] = label['sample_id'] = 'duplicate'
    samples.append(duplicate)
    targets.append(label)
    repeated = EmpiricalRiskPredictor(right_edges=[.1], conditional=False).fit(samples, targets)
    assert repeated.predict(features()) == original.predict(features())
    assert repeated.predict(features())[0]['hU'] == pytest.approx(2/3)


def test_conditional_estimate_is_shrunk_and_sparse_or_unseen_group_falls_back():
    samples, targets = data([('legal_demand', .05, 1.), ('legal_demand', .05, 1.),
                             ('retire', .05, 1.), ('retire', .05, 1.)])
    for sample in samples[2:]:
        sample['features']['phase'] = 'queued'
    model = EmpiricalRiskPredictor(right_edges=[.1], min_group_workflows=2, prior_weight=2.).fit(samples, targets)
    predicted = model.predict(features())[0]
    assert predicted['estimate_source'] == 'group_shrunk_to_global'
    assert [predicted[k] for k in ['hU', 'hD', 'h0']] == [.75, .25, 0.]
    unknown = model.predict(features(tool_name='new-tool'))[0]
    assert unknown['estimate_source'] == 'global_fallback'
    assert unknown['hU'] == .5
    sparse = EmpiricalRiskPredictor(right_edges=[.1], min_group_workflows=3).fit(samples, targets)
    assert sparse.predict(features())[0]['hU'] == .5


def test_many_snapshots_from_one_workflow_do_not_meet_workflow_support_threshold():
    samples, targets = data([('legal_demand', .05, 1/3)] * 3 + [('retire', .05, 1.)])
    for sample in samples[:3]:
        sample['run_id'] = 'same-run'
    samples[-1]['features']['phase'] = 'queued'
    model = EmpiricalRiskPredictor(right_edges=[.1], min_group_workflows=2).fit(samples, targets)
    row = model.predict(features())[0]
    assert row['estimate_source'] == 'global_fallback'
    assert row['hU'] == pytest.approx(.5)


def test_inference_rejects_future_fields_and_new_backend_is_not_extrapolated():
    samples, targets = data([('legal_demand', .05, 1.)])
    model = EmpiricalRiskPredictor(right_edges=[.1]).fit(samples, targets)
    with pytest.raises(ValueError, match='allowlist'):
        model.predict(features(outcome='legal_demand'))
    with pytest.raises(ValueError, match='allowlist'):
        model.predict({'features': features(), 'sample_id': 'training-id'})
    row = model.predict(features(compatibility_id='other-backend'))[0]
    assert row['supported'] is False and row['hU'] is None


def test_selected_horizon_accumulates_all_preceding_intervals():
    samples, targets = data([('legal_demand', .05, 1.), ('retire', .15, 1.)])
    model = EmpiricalRiskPredictor(right_edges=[.1, .2], conditional=False).fit(samples, targets)
    row, = model.predict(features(), horizons=[.2])
    assert [row[k] for k in ['F_U', 'F_D', 'survival']] == [.5, .5, 0.]
    with pytest.raises(ValueError, match='configured'):
        model.predict(features(), horizons=[.15])


def test_model_roundtrip_and_saved_counts_validation(tmp_path):
    samples, targets = data([('legal_demand', .05, 1.), ('retire', .15, 1.)])
    model = EmpiricalRiskPredictor(right_edges=[.1, .2]).fit(samples, targets)
    path = tmp_path / 'model.json'
    model.save(path)
    assert EmpiricalRiskPredictor.load(path).predict(features()) == model.predict(features())
    saved = json.loads(path.read_text())
    saved['global_counts'][0]['counts'][0] = -1.
    path.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match='risk counts'):
        EmpiricalRiskPredictor.load(path)


def test_no_observed_interval_cannot_be_fit_or_replaced_with_a_prior():
    samples, targets = data([('right_censored', .01, 1.)])
    with pytest.raises(ValueError, match='no fully observed'):
        EmpiricalRiskPredictor(right_edges=[.1]).fit(samples, targets)
    samples[0]['sample_weight'] = float('nan')
    with pytest.raises(ValueError, match='weights'):
        EmpiricalRiskPredictor().fit(samples, targets)


def test_target_identity_mismatch_or_mixed_backend_cannot_silently_fit():
    samples, targets = data([('legal_demand', .05, 1.), ('retire', .15, 1.)])
    with pytest.raises(ValueError, match='identity mismatch'):
        EmpiricalRiskPredictor().fit(samples, targets[:1])
    samples[1]['features']['compatibility_id'] = 'other-backend'
    with pytest.raises(ValueError, match='one backend'):
        EmpiricalRiskPredictor().fit(samples, targets)
