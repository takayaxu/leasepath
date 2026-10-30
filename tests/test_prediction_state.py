"""Causal projection must not depend on future validation or mutable payloads."""
from copy import deepcopy
import json
from pathlib import Path
import runpy

from leasepath.prediction_state import snapshot
from leasepath.contract_fixtures import expand_cases

ROOT = Path(__file__).resolve().parents[1]


def test_future_poison_is_not_read_and_snapshot_does_not_alias_events():
    config = json.loads((ROOT / 'data/fixtures/event_contract.json').read_text())
    _, events = next(expand_cases(config))
    prefix = [deepcopy(e) for e in events if e['ts_ns'] <= 9]
    result = snapshot(prefix, 'd2', 'idle-1', 9)
    # This is deliberately not a valid future event. A causal projection cannot
    # require its data, event type, or successful future trace validation.
    poison = {'run_id': 'd2', 'ts_ns': 10}
    assert snapshot(prefix + [poison], 'd2', 'idle-1', 9) == result
    idle = next(e for e in prefix if e['event_type'] == 'idle_start')
    idle['data']['prefix_token_ids'].append(999)
    idle['data']['compatibility_id'] = 'changed'
    assert result['features']['prefix_tokens'] == 2
    assert result['features']['compatibility_id'] == 'model-v1'


def test_contract_acceptance_and_repeatable_artifacts(tmp_path):
    import hashlib

    api = runpy.run_path(str(ROOT / 'scripts/check_prediction_d1.py'))
    first = api['run_checks'](tmp_path / 'first')
    second = api['run_checks'](tmp_path / 'second')
    assert first == second
    assert first['status'] == 'passed'
    for filename, expected in first['output_sha256'].items():
        assert hashlib.sha256((tmp_path / 'first' / filename).read_bytes()).hexdigest() == expected
