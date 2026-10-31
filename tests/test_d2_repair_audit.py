from copy import deepcopy

import pytest

from leasepath.d2_repair import SUITE_ID
from leasepath.d2_repair_audit import audit_repair


def rows():
    return [
        dict(kind='tool_start', ts_ns=1, workflow_id='w', request_id='r1', tool_id='a',
             command_category='test', test_suite_id=SUITE_ID, suite_attempt_no=1,
             retry_of_tool_id=None, source_changed_since_failure=None, suite_retries_started=0,
             source_sha256='broken', prior_failed_tests=0),
        dict(kind='tool_end', ts_ns=5, workflow_id='w', request_id='r1', tool_id='a',
             returncode=1, source_sha256='broken', source_changed=False, duration_ns=4),
        dict(kind='tool_start', ts_ns=6, workflow_id='w', request_id='r2', tool_id='edit',
             command_category='edit', source_sha256='broken', prior_failed_tests=1),
        dict(kind='tool_end', ts_ns=7, workflow_id='w', request_id='r2', tool_id='edit',
             returncode=0, source_sha256='fixed', source_changed=True, duration_ns=1),
        dict(kind='tool_start', ts_ns=10, workflow_id='w', request_id='r3', tool_id='b',
             command_category='test', test_suite_id=SUITE_ID, suite_attempt_no=2,
             retry_of_tool_id='a', source_changed_since_failure=True, suite_retries_started=1,
             source_sha256='fixed', prior_failed_tests=1),
        dict(kind='tool_end', ts_ns=20, workflow_id='w', request_id='r3', tool_id='b',
             returncode=0, source_sha256='fixed', source_changed=False, duration_ns=10),
    ]


def test_actual_failure_edit_and_success_have_auditable_links():
    result = audit_repair(rows(), 'w', 'broken')
    assert result['initial_unchanged_defect_reproduced']
    assert result['source_repair_then_successful_retry']
    assert result['suite_retries'] == 1
    assert result['chains'][0]['source_edit_tool_ids'] == ['edit']


@pytest.mark.parametrize('field,value', [('retry_of_tool_id', 'future-failure'),
                                       ('source_changed_since_failure', False),
                                       ('prior_failed_tests', 9)])
def test_forged_failure_links_or_future_counters_are_rejected(field, value):
    changed = deepcopy(rows())
    changed[-2][field] = value
    with pytest.raises(ValueError):
        audit_repair(changed, 'w', 'broken')


def test_missing_end_and_success_without_source_repair_are_not_repair_proof():
    with pytest.raises(ValueError, match='no end'):
        audit_repair(rows()[:-1], 'w', 'broken')
    changed = rows()
    changed[3].update(source_changed=False, source_sha256='broken')
    changed[4].update(source_changed_since_failure=False, source_sha256='broken')
    changed[5]['source_sha256'] = 'broken'
    result = audit_repair(changed, 'w', 'broken')
    assert result['initial_unchanged_defect_reproduced']
    assert not result['source_repair_then_successful_retry']
