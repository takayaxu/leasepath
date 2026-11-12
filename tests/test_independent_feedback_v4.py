from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_independent_agent_loop_v4 import calls_with_actual_feedback
from leasepath.independent_agent_protocol import certify_loop


def examples():
    # Canonical persisted JSON sorts keys, unlike the original worker result.
    calls = [dict(call_id=str(i), name='evaluate_candidate', status='ok',
                  started_ns=i*10, ended_ns=i*10+1, arguments={'candidate': [i]},
                  result={'objective_minimize': 0, 'valid': i == 2, 'violations': []}) for i in (1, 2)]
    ends = [dict(kind='tool_end', data=dict(owner='A', tool_name='evaluate_candidate',
                actual_observed_monotonic_s=(i*10+2)/1e9,
                result={'valid': i == 2, 'objective_minimize': 0, 'violations': []})) for i in (1, 2)]
    turns = [dict(input_ns=13, prompt='{"valid":false,"objective_minimize":0,"violations":[]}'),
             dict(input_ns=23, prompt='{"valid":true,"objective_minimize":0,"violations":[]}')]
    return calls, ends, turns


def test_canonical_key_order_does_not_erase_observed_feedback_refinement():
    calls, ends, turns = examples()
    assert not certify_loop(turns, calls, successful=True)['all_feedback_consumed']
    linked = calls_with_actual_feedback(calls, ends, 'A')
    assert certify_loop(turns, linked, successful=True)['behavioral_loop_supported']


def test_changed_feedback_and_missing_events_are_rejected():
    calls, ends, _ = examples()
    with pytest.raises(ValueError, match='count differs'):
        calls_with_actual_feedback(calls, ends[:1], 'A')
    ends[0]['data']['result']['valid'] = True
    with pytest.raises(ValueError, match='feedback differs'):
        calls_with_actual_feedback(calls, ends, 'A')


def test_feedback_cannot_predate_tool_completion():
    calls, ends, _ = examples()
    ends[0]['data']['actual_observed_monotonic_s'] = 0
    with pytest.raises(ValueError, match='before actual'):
        calls_with_actual_feedback(calls, ends, 'A')
