import pytest

from leasepath.d2_hazards import administrative_views, derive_hazard_rows, summarize_horizons


def sample():
    return {'sample_id': 's', 'run_id': 'r', 'idle_episode_id': 'e',
            'sample_weight': 1., 'split': 'pilot_development_only'}


def test_future_demand_teaches_waiting_before_event_and_stops_risk_set():
    target = {'sample_id': 's', 'outcome': 'legal_demand', 'remaining_s': 2.}
    rows = derive_hazard_rows([sample()], [target], [.1, 1, 3, 10])
    assert [r['label'] for r in rows] == ['waiting', 'waiting', 'U']
    assert all(r['loss_mask'] for r in rows)
    assert target['outcome'] == 'legal_demand'
    views = administrative_views([target], [.1, 3])
    assert [(r['outcome'], r['event_observed']) for r in views] == [('right_censored', False), ('legal_demand', True)]


def test_real_censoring_masks_partial_interval_instead_of_teaching_waiting():
    target = {'sample_id': 's', 'outcome': 'right_censored', 'remaining_s': .15}
    rows = derive_hazard_rows([sample()], [target], [.1, .3, 1])
    assert [(r['label'], r['loss_mask']) for r in rows] == [('waiting', True), (None, False)]
    counts = summarize_horizons([target], [.1, .3])
    assert counts[0]['counts']['known_waiting'] == 1
    assert counts[1]['counts']['unknown_after_censoring'] == 1


def test_event_boundary_and_censor_boundary_have_distinct_meaning():
    rows = derive_hazard_rows([sample()], [{'sample_id': 's', 'outcome': 'retire', 'remaining_s': .1}], [.1, .3])
    assert [r['label'] for r in rows] == ['D']
    rows = derive_hazard_rows([sample()], [{'sample_id': 's', 'outcome': 'right_censored', 'remaining_s': .1}], [.1, .3])
    assert [(r['label'], r['loss_mask']) for r in rows] == [('waiting', True), (None, False)]


def test_hazard_join_rejects_misaligned_labels():
    with pytest.raises(ValueError, match='identity mismatch'):
        derive_hazard_rows([sample()], [{'sample_id': 'other', 'outcome': 'retire', 'remaining_s': 1.}])
