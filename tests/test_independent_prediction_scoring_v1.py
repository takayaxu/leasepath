from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_independent_predictions_v1 import horizon_label, event_probability, summarize, extract_snapshots


def distribution():
    return [dict(right_s=1, supported=True, F_U=.2, F_D=.1, survival=.7),
            dict(right_s=3, supported=True, F_U=.5, F_D=.2, survival=.3),
            dict(right_s=30, supported=True, F_U=.6, F_D=.3, survival=.1)]


def test_censoring_is_not_a_negative_label_before_horizon():
    assert horizon_label('C', 2, 3) is None
    assert horizon_label('C', 3, 3) == 0
    assert horizon_label('D', 2, 3) == 0
    assert horizon_label('U', 3, 3) == 1
    assert horizon_label('U', 4, 3) == 0


def test_event_probability_uses_increment_and_censor_survival():
    rows = distribution()
    assert event_probability(rows, 'U', 2) == pytest.approx(.3)
    assert event_probability(rows, 'D', 2) == pytest.approx(.1)
    assert event_probability(rows, 'C', 2) == .7
    assert event_probability(rows, 'C', .5) is None
    assert event_probability(rows, 'U', 100) == .1


def test_episode_weights_do_not_count_dense_snapshots_as_independent_episodes():
    rows = [dict(model='frozen_gbdt', event='U', remaining_s=.5, weight=.5, distribution=distribution()) for _ in range(2)]
    rows += [dict(model='frozen_gbdt', event='D', remaining_s=.5, weight=1., distribution=distribution())]
    score = summarize(rows)['frozen_gbdt']['horizons_s']['1']
    assert score['snapshots'] == 3 and score['episode_weight_sum'] == 2
    assert score['observed_U_fraction'] == .5
    assert score['Brier_U'] == pytest.approx((.8**2 + .2**2) / 2)


@pytest.mark.parametrize('changed', [False, True])
def test_real_lookup_miss_is_demand_but_prefix_change_is_not_invented_retirement(tmp_path, changed):
    import time
    from leasepath.real_observer import Observer
    raw = tmp_path / 'raw'
    controller = Observer(raw / 'controller.jsonl', run_id='test', source='controller')
    backend = Observer(raw / 'backend.jsonl', run_id='test', source='backend')
    base = time.monotonic() - 10
    def request(owner, rid, at, prefix, observed):
        backend.emit('engine_request', owner=owner, request_id=rid, tokens=prefix + [99], hit=0,
            lookup_ns=int(at * 1e9), release_ns=int((at + .5) * 1e9),
            policy_decision={'evicted': [{'key': 'old'}], 'distribution_audit':
                {'t0_s': at, 'observed_idle_features': observed}})
    request('A', 'a1', base, [1] * 128, {})
    request('B', 'b1', base + 2, [2] * 128, {'A': {'prefix_tokens': 128}})
    request('A', 'a2', base + 3, [3 if changed else 1] * 128, {})
    controller.emit('context_retire', owner='A')
    controller.emit('observation_end', complete=True)
    controller.finish(); backend.finish()
    rows, exclusions = extract_snapshots(tmp_path, {'A': {'source_family': 'test'}})
    if changed:
        assert not rows and exclusions[0]['reason'] == 'next_lookup_changes_observed_prefix'
    else:
        assert not exclusions and rows[0]['event'] == 'U'
        assert rows[0]['remaining_s'] == pytest.approx(1.)
