import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import continue_staged_pressure_v2 as continuation


@pytest.mark.parametrize('returncodes,expected_calls', [([0, 1], 2), ([0, 0, 1], 3)])
def test_native_or_audit_failure_prevents_later_cells(tmp_path, monkeypatch, returncodes, expected_calls):
    monkeypatch.setattr(continuation, 'OLD', tmp_path / 'runtime-root')
    monkeypatch.setattr(continuation, 'same_process', lambda *args: False)
    monkeypatch.setattr(continuation.signal, 'signal', lambda *args: None)
    commands = []
    def fake_run(command, *args):
        commands.append(command)
        return returncodes[len(commands) - 1]
    monkeypatch.setattr(continuation, 'run_command', fake_run)
    checkpoint = tmp_path / 'checkpoint.json'
    checkpoint.write_text('{"status":"complete","bytes":100,"elapsed_s":1}')
    (tmp_path / 'staging.json').write_text('{"prior_total_elapsed_s": 0}')
    matrix = tmp_path / 'matrix.json'
    matrix.write_text(json.dumps({'matrix': {'cells': [
        {'capacity_blocks': c, 'concurrency': n,
         'batches': [{'generation_limits': {'total': n * 108}}]}
        for n in (3, 6, 10) for c in (24, 32, 64)]}}))
    evidence = tmp_path / 'evidence'
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'evidence': str(evidence), 'overall_wall_budget_s': 60,
        'source_sha256': {}, 'staging_plan': str(tmp_path / 'staging.json'),
        'staging_checkpoint': str(checkpoint), 'preparation_pid': 0, 'preparation_start_ticks': 0,
        'matrix_plan': str(matrix), 'diagnostic_wall_budget_s': 10, 'natural_cell_wall_budget_s': 10,
        'report': str(tmp_path / 'report.md')}))
    monkeypatch.setattr(sys, 'argv', ['continue', '--plan', str(plan)])
    with pytest.raises(SystemExit) as exc:
        continuation.main()
    assert exc.value.code == 1
    assert len(commands) == expected_calls
    state = json.loads((evidence / 'state.json').read_text())
    assert state['status'] == 'stopped' and len(state['steps']) == 1
    assert state['steps'][0]['diagnostic']
    assert state['natural_JCT_passed'] is False
    assert '**stopped**' in (tmp_path / 'report.md').read_text()


def test_child_deadline_interrupts_and_reaps_process(tmp_path):
    log = tmp_path / 'child.log'
    with pytest.raises(__import__('subprocess').TimeoutExpired):
        continuation.run_command([sys.executable, '-c', 'import time; time.sleep(30)'],
                                 log, os.environ.copy(), 1)
    assert 'KeyboardInterrupt' in log.read_text()
