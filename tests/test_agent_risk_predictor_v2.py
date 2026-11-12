import pytest
from leasepath.agent_risk_predictor_v2 import AgentGBDTV2, AgentEmpiricalV2, encode
from leasepath.distribution_cache import distribution_scores
from leasepath.prediction_training import check_distribution


def features(**changes):
    return dict(elapsed_idle_s=2., compatibility_id='development', prefix_tokens=512,
                tool_name='arbitrary_tool', tool_elapsed_s=None, phase='queued',
                retry_count=0, is_queued=True, queue_length=3, **changes)


@pytest.fixture(scope='module')
def fitted():
    samples, labels = [], []
    for i in range(120):
        current = features()
        if i % 3:
            current.update(is_queued=False, phase='waiting_tool', tool_elapsed_s=2., queue_length=1)
        samples.append(dict(sample_id=str(i), run_id='family-'+str(i % 6),
                            idle_episode_id=str(i), sample_weight=1., split='development', features=current))
        labels.append(dict(sample_id=str(i), outcome='legal_demand' if i%3 < 2 else 'retire',
                           remaining_s=[.1, 15., 45.][i%3]))
    return AgentGBDTV2().fit(samples, labels)


def test_feature_contract_excludes_future_and_tool_identity():
    a=features(); b={**a, 'tool_name':'new_unseen_name'}
    assert encode(a, 0) == encode(b, 0)
    assert encode(a, 0) != encode({**a, 'queue_length':None}, 0)
    assert encode(a, 0) != encode({**a, 'is_queued':False}, 0)
    with pytest.raises(ValueError, match='allowlist'):
        encode({**a, 'remaining_s':1}, 0)


def test_distributions_roundtrip_and_do_not_invent_support(fitted, tmp_path):
    path=tmp_path/'model.json';fitted.save(path)
    restored=AgentGBDTV2.load(path)
    prediction=fitted.predict(features())
    check_distribution(prediction)
    assert prediction == restored.predict(features())
    assert prediction == fitted.predict({**features(), 'tool_name':'totally_new_tool'})
    assert prediction[-1]['supported'] is False
    assert distribution_scores(prediction)['W'] is None  # Fewer than five families remain at risk at 30s.
    assert not fitted.predict({**features(), 'compatibility_id':'other'})[0]['supported']


def test_learns_observable_queue_state(fitted):
    queued=fitted.predict(features())
    running=fitted.predict({**features(), 'is_queued':False, 'phase':'waiting_tool',
                            'tool_elapsed_s':2., 'queue_length':1})
    assert next(p['F_U'] for p in queued if p['right_s']==1) > next(p['F_U'] for p in running if p['right_s']==1)


def test_empirical_groups_ignore_tool_names():
    model=AgentEmpiricalV2()
    assert model._key(features()) == model._key({**features(), 'tool_name':'unseen'})
    assert model._key(features()) != model._key({**features(), 'is_queued':False, 'queue_length':0})
