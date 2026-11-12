from types import SimpleNamespace
import pytest
from leasepath.agent_queue_features_v2 import refresh_queue_features
from leasepath.prediction_state import validate_features


def fixture():
    base = dict(elapsed_idle_s=2., compatibility_id='backend', prefix_tokens=256,
                tool_name=None, tool_elapsed_s=None, phase=None, retry_count=None,
                is_queued=None, queue_length=None)
    observed = {}
    engine = SimpleNamespace(cache=SimpleNamespace(idle={'a': (base, 10.), 'b': (base, 10.)}),
                             observe=lambda owner, features: observed.update({owner: features}))
    states = {'a': dict(done=False, tool_running=False, last_tool='inspect_problem'),
              'b': dict(done=False, tool_running=True, last_tool='evaluate_candidate', tool_started=9.)}
    events = []
    controller = SimpleNamespace(emit=lambda *args, **kw: events.append((args, kw)))
    return engine, states, controller, observed, events


def test_actual_ready_and_running_states():
    engine, states, controller, observed, events = fixture()
    refresh_queue_features(engine, states, [(11., 'a')], controller, now=12.)
    for features in observed.values():
        validate_features(features)
    assert observed['a']['phase'] == 'queued' and observed['a']['is_queued'] is True
    assert observed['a']['tool_elapsed_s'] is None
    assert observed['b']['phase'] == 'waiting_tool' and observed['b']['is_queued'] is False
    assert observed['b']['tool_elapsed_s'] == 3.
    assert observed['a']['queue_length'] == observed['b']['queue_length'] == 1
    assert observed['a']['elapsed_idle_s'] == 4.
    assert events[0][1]['ready_owners'] == ['a']


@pytest.mark.parametrize('ready', [[(11., 'a'), (12., 'a')], [(11., 'b')]])
def test_inconsistent_queue_rejected(ready):
    engine, states, controller, _, _ = fixture()
    with pytest.raises(ValueError):
        refresh_queue_features(engine, states, ready, controller, now=12.)
