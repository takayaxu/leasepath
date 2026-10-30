import copy
import pytest
from leasepath.trace import validate_events, derive_idle_labels


def e(kind, t, **kw):
    return dict(schema_version='0.1', run_id='r', event_id=f'e{t}', event_type=kind,
                ts_ns=t, clock_domain='one', evidence_kind='synthetic', data={}, **kw)


def idle_events():
    start = e('idle_start', 0, idle_episode_id='i')
    start['data'] = dict(context_id='c', context_version='v', compatibility_id='model', prefix_token_ids=[1,2])
    return [start, e('observation_end', 10)]


def test_right_censor_not_retire():
    assert derive_idle_labels(idle_events())[0]['outcome'] == 'right_censored'


def test_explicit_retire():
    events = idle_events()
    retire = e('context_retire', 5, idle_episode_id='i'); retire['data']={'reason':'abandoned'}
    events.insert(1, retire)
    assert derive_idle_labels(events)[0]['outcome'] == 'retire'


@pytest.mark.parametrize('compat,tokens', [('wrong',[1,2]), ('model',[3,4])])
def test_invalid_reuse_rejected(compat,tokens):
    events = idle_events()
    demand = e('legal_demand',5,idle_episode_id='i',request_id='next')
    demand['data'] = {'compatibility_id':compat, 'request_token_ids':tokens}
    events.insert(1,demand)
    with pytest.raises(ValueError, match='compatible token prefix'):
        validate_events(events)


def test_duplicates_and_unaligned_clocks_rejected():
    events = idle_events()
    events.insert(1, copy.deepcopy(events[0]))
    with pytest.raises(ValueError, match='duplicate'):
        validate_events(events)
    events = idle_events(); events[1]['clock_domain']='two'
    with pytest.raises(ValueError, match='unaligned'):
        validate_events(events)


def test_incomplete_observation_rejected():
    with pytest.raises(ValueError, match='observation_end'):
        validate_events(idle_events()[:1])
