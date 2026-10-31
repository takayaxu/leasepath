from copy import deepcopy

from leasepath.d2_expansion import EXTRA_FEATURES, project_extra


def test_future_tool_outcome_does_not_leak_and_history_advances_only_on_end():
    rows = [
        {'ts_ns': 1, 'kind': 'tool_start', 'workflow_id': 'w', 'request_id': 'r',
         'tool_id': 't', 'command_category': 'test', 'workload_family': 'csv_aggregation',
         'work_units': 500000, 'work_unit': 'rows', 'timeout_s': 60},
        {'ts_ns': 3_000_000_001, 'kind': 'tool_end', 'workflow_id': 'w', 'tool_id': 't',
         'request_id': 'r', 'returncode': 1},
    ]
    before = project_extra(rows, 'w', 'idle-r', 1_000_000_001)
    changed = deepcopy(rows)
    changed[-1].update(ts_ns=50_000_000_001, returncode=0)
    assert project_extra(changed, 'w', 'idle-r', 1_000_000_001) == before
    assert project_extra(rows[:1], 'w', 'idle-r', 1_000_000_001) == before
    assert set(before) == EXTRA_FEATURES
    assert before['prior_failed_tests'] == 0
    assert before['last_completed_tool_s'] is None
    assert before['work_units'] == 500000
    after = project_extra(rows, 'w', 'idle-r', 3_000_000_001)
    assert after['prior_failed_tests'] == 1
    assert after['last_completed_tool_s'] == 3
    assert after['tool_state'] == 'completed'
    assert after['work_units'] is None


def test_other_episode_tool_does_not_become_current_and_other_workflow_not_history():
    rows = [{'ts_ns': 1, 'kind': 'tool_start', 'workflow_id': 'w',
             'request_id': 'other', 'tool_id': 't', 'command_category': 'test'},
            {'ts_ns': 2, 'kind': 'tool_end', 'workflow_id': 'w', 'tool_id': 't', 'returncode': 1},
            {'ts_ns': 3, 'kind': 'tool_start', 'workflow_id': 'another', 'tool_id': 'x'}]
    f = project_extra(rows, 'w', 'idle-r', 3)
    assert f['command_category'] is None
    assert f['tool_state'] is None
    assert f['completed_tool_count'] == 1
    assert f['prior_failed_tests'] == 1
