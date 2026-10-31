"""Test-first repair protocol and observed retry features, separate schema v2."""
from .d2_expansion import EXTRA_FEATURES, project_extra

INSPECT = 'cat README.md solution.py test_visible.py test_workload.py'
SUITE = 'python -m pytest -q test_visible.py test_workload.py'
SUBMIT = 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'
SUITE_ID = 'visible-workload-v2'
REPAIR_FEATURES = EXTRA_FEATURES | frozenset({
    'current_suite_attempt', 'current_suite_is_retry', 'current_source_changed_since_failure',
    'suite_retries_started', 'failed_full_suites', 'last_full_suite_passed',
})


class RepairState:
    """Local policy state; records only actions actually started/completed."""
    def __init__(self, initial_source_hash):
        self.initial_hash = initial_source_hash
        self.inspected = False
        self.reproduced = False
        self.success_hash = None
        self.attempts = 0
        self.retries = 0
        self.failed_tests = 0
        self.pending_failure = None

    def rejection(self, command, source_hash):
        if not self.inspected and command != INSPECT:
            return 'Inspect first using exactly: ' + INSPECT
        if self.inspected and not self.reproduced and command not in {INSPECT, SUITE}:
            return 'Before any edit, reproduce the existing defect using exactly: ' + SUITE
        if not self.reproduced and source_hash != self.initial_hash:
            return 'Initial source changed before defect reproduction; preserve the failed run for audit.'
        if command == SUBMIT and (not self.reproduced or self.success_hash != source_hash):
            return 'Submit only after a real initial failure, source repair and successful current-source suite: ' + SUITE
        if 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' in command and command != SUBMIT:
            return 'Submission must be the standalone command: ' + SUBMIT
        return None

    def start(self, command, tool_id, source_hash):
        data = {'source_sha256': source_hash, 'prior_failed_tests': self.failed_tests}
        if command == SUITE:
            self.success_hash = None
            self.attempts += 1
            if self.pending_failure:
                self.retries += 1
            data.update(test_suite_id=SUITE_ID, suite_attempt_no=self.attempts,
                        retry_of_tool_id=self.pending_failure[0] if self.pending_failure else None,
                        source_changed_since_failure=(source_hash != self.pending_failure[1]) if self.pending_failure else None,
                        suite_retries_started=self.retries)
        return data

    def end(self, command, tool_id, before_hash, after_hash, returncode, command_category):
        if command == INSPECT and returncode == 0:
            self.inspected = True
        if command_category == 'test' and returncode != 0:
            self.failed_tests += 1
        if command != SUITE:
            return
        if returncode != 0:
            self.pending_failure = (tool_id, after_hash)
            if returncode == 1 and before_hash == after_hash == self.initial_hash:
                self.reproduced = True
        else:
            self.success_hash = after_hash if before_hash == after_hash else None
            self.pending_failure = None


def project_repair(raw, workflow, episode, as_of_ns):
    """Project explicit suite attempts from the observed prefix; never emit IDs."""
    result = project_extra(raw, workflow, episode, as_of_ns)
    running, starts = {}, {}
    failures = retries = 0
    last_passed = None
    rid = episode.removeprefix('idle-')
    for row in raw:
        if row.get('workflow_id') != workflow:
            continue
        if row['ts_ns'] > as_of_ns:
            break
        if row['kind'] == 'tool_start':
            starts[row['tool_id']] = row
            if row.get('test_suite_id') == SUITE_ID and row.get('retry_of_tool_id') is not None:
                retries += 1
            if row.get('request_id') == rid:
                running[row['tool_id']] = row
        elif row['kind'] == 'tool_end':
            start = starts[row['tool_id']]
            running.pop(row['tool_id'], None)
            if start.get('test_suite_id') == SUITE_ID:
                last_passed = row['returncode'] == 0
                failures += row['returncode'] != 0
    current = next(iter(running.values())) if len(running) == 1 else None
    suite = current if current and current.get('test_suite_id') == SUITE_ID else None
    result.update(current_suite_attempt=suite.get('suite_attempt_no') if suite else None,
                  current_suite_is_retry=(suite.get('retry_of_tool_id') is not None) if suite else None,
                  current_source_changed_since_failure=suite.get('source_changed_since_failure') if suite else None,
                  suite_retries_started=retries, failed_full_suites=failures,
                  last_full_suite_passed=last_passed)
    assert set(result) == REPAIR_FEATURES
    return result
