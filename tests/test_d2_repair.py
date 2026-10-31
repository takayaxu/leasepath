from copy import deepcopy

from leasepath.d2_repair import INSPECT, SUITE, SUBMIT, SUITE_ID, RepairState, project_repair


def test_protocol_requires_real_unchanged_failure_and_current_source_retest():
    state = RepairState('broken')
    assert state.rejection('cat > solution.py', 'broken')
    assert state.rejection(INSPECT, 'broken') is None
    state.end(INSPECT, 'inspect', 'broken', 'broken', 0, 'inspect')
    assert state.rejection('cat > solution.py', 'broken')
    first = state.start(SUITE, 'a', 'broken')
    assert first['suite_attempt_no'] == 1 and first['retry_of_tool_id'] is None
    state.end(SUITE, 'a', 'broken', 'broken', 1, 'test')
    assert state.reproduced and state.failed_tests == 1
    assert state.rejection('cat > solution.py', 'broken') is None
    retry = state.start(SUITE, 'b', 'fixed')
    assert retry['retry_of_tool_id'] == 'a' and retry['source_changed_since_failure']
    assert state.rejection(SUBMIT, 'fixed')
    state.end(SUITE, 'b', 'fixed', 'fixed', 0, 'test')
    assert state.rejection(SUBMIT, 'fixed') is None
    assert state.rejection(SUBMIT, 'edited-again')


def test_timeout_and_modified_source_are_not_initial_defect_reproduction():
    for before, after, rc in [('broken', 'broken', -1), ('broken', 'modified', 1)]:
        state = RepairState('broken')
        state.end(SUITE, 'a', before, after, rc, 'test')
        assert not state.reproduced


def test_retry_features_read_only_observed_outcomes_and_link_current_episode():
    rows = [
        dict(kind='tool_start', ts_ns=1, workflow_id='w', request_id='r1', tool_id='a',
             test_suite_id=SUITE_ID, suite_attempt_no=1, retry_of_tool_id=None, command_category='test'),
        dict(kind='tool_end', ts_ns=5, workflow_id='w', request_id='r1', tool_id='a', returncode=1),
        dict(kind='tool_start', ts_ns=10, workflow_id='w', request_id='r2', tool_id='b',
             test_suite_id=SUITE_ID, suite_attempt_no=2, retry_of_tool_id='a',
             source_changed_since_failure=True, command_category='test'),
        dict(kind='tool_end', ts_ns=20, workflow_id='w', request_id='r2', tool_id='b', returncode=0),
    ]
    first = project_repair(rows, 'w', 'idle-r1', 3)
    assert first['failed_full_suites'] == 0 and first['last_full_suite_passed'] is None
    retry = project_repair(rows, 'w', 'idle-r2', 15)
    assert retry['failed_full_suites'] == 1 and retry['last_full_suite_passed'] is False
    assert retry['current_suite_is_retry'] and retry['current_source_changed_since_failure']
    assert retry['suite_retries_started'] == 1 and retry['current_suite_attempt'] == 2
    changed = deepcopy(rows)
    changed[-1].update(ts_ns=999, returncode=1)
    assert project_repair(changed, 'w', 'idle-r2', 15) == retry
    assert project_repair(rows[:-1], 'w', 'idle-r2', 15) == retry
    after = project_repair(rows, 'w', 'idle-r2', 20)
    assert after['last_full_suite_passed'] and after['current_suite_is_retry'] is None
    assert project_repair(rows, 'w', 'idle-unrelated', 15)['current_suite_attempt'] is None
    assert 'retry_of_tool_id' not in retry and 'source_sha256' not in retry
