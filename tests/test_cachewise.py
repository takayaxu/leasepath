"""Audit genuine data limitations without converting proxy outcomes to KV labels."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from leasepath.cachewise import (
    ARCHIVE_SHA256, COMMIT, audit_dataset, audit_session, verify_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]


def example():
    llm = {'event_type': 'llm_call', 'message_id': 'm', 'request_id': 'r',
           'model': 'model', 'timestamp': '2020-01-01T00:00:00+00:00'}
    tool = {
        'event_type': 'tool_call', 'tool_use_id': 't', 'tool_name': 'Bash',
        'tool_input': {'content': '<redacted>'}, 'has_result': True,
        'timestamp': '2020-01-01T00:00:01+00:00',
        'result_timestamp': '2020-01-01T00:00:03+00:00',
        'execution_time_ms': 2000.0, 'associated_llm_call_id': 'm', 'is_error': False,
    }
    raw = [
        {'timestamp': tool['timestamp'], 'isSidechain': False,
         'message': {'model': 'model', 'content': [
             {'type': 'tool_use', 'id': 't', 'name': 'Bash', 'input': tool['tool_input']},
         ]}},
        {'timestamp': tool['result_timestamp'], 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 't', 'content': '<redacted>'},
        ]}},
    ]
    return [llm, tool], raw


def test_features_do_not_contain_future_result_or_session_fraction():
    events, raw = example()
    first = audit_session('p', 's', events, raw)
    changed = deepcopy(events)
    changed[-1]['result_timestamp'] = '2020-01-01T00:00:06+00:00'
    changed[-1]['execution_time_ms'] = 5000
    changed[-1]['is_error'] = True
    changed_raw = deepcopy(raw)
    changed_raw[-1]['timestamp'] = changed[-1]['result_timestamp']
    second = audit_session('p', 's', changed, changed_raw)
    assert first['features'] == second['features']
    assert first['labels'] != second['labels']
    assert first['features'][0]['features'] == {'tool_name': 'Bash', 'elapsed_s': 0.0}
    assert first['labels'][0]['exact_kv_outcome'] is None
    assert first['quality'][0]['candidate_for_tool_span_proxy']


def test_duplicate_results_quarantined_instead_of_last_result_overwrite():
    events, raw = example()
    copy = deepcopy(raw[-1])
    copy['timestamp'] = '2020-01-01T00:10:00+00:00'
    result = audit_session('p', 's', events, raw + [copy])
    assert result['counts']['raw_duplicate_result_id_groups'] == 1
    assert not result['quality'][0]['candidate_for_tool_span_proxy']
    assert 'raw_tool_result_not_unique' in result['quality'][0]['flags']
    assert result['labels'][0]['observed_log_span_s'] == 2


def test_duplicate_tool_ids_and_zero_rows_are_not_silently_deduplicated():
    events, raw = example()
    copy = deepcopy(events[-1])
    copy['timestamp'] = copy['result_timestamp']
    copy['execution_time_ms'] = 0
    result = audit_session('p', 's', events + [copy], raw)
    assert len(result['features']) == len(result['labels']) == len(result['quality']) == 2
    assert result['counts']['duplicate_tool_id_groups'] == 1
    assert not any(r['candidate_for_tool_span_proxy'] for r in result['quality'])
    assert 'zero_duration_resolution_or_copy' in result['quality'][1]['flags']


def test_missing_tool_result_does_not_become_retire_or_censor():
    events, raw = example()
    events[-1].update(has_result=False, execution_time_ms=None, result_timestamp=None)
    result = audit_session('p', 's', events, raw[:1])
    assert result['labels'][0]['observed_log_span_s'] is None
    assert result['labels'][0]['exact_kv_outcome'] is None
    assert not result['quality'][0]['candidate_for_tool_span_proxy']


def test_verify_rejects_changed_source_file(tmp_path):
    source = tmp_path / 'source'
    upstream = source / 'upstream'
    upstream.mkdir(parents=True)
    data = b'fixed snapshot'
    (upstream / 'data.json').write_bytes(data)
    (source / 'provenance.json').write_text(json.dumps({
        'commit': COMMIT, 'archive_sha256': ARCHIVE_SHA256,
        'files_sha256': {'data.json': hashlib.sha256(data).hexdigest()},
    }))
    verify_snapshot(source)
    (upstream / 'data.json').write_bytes(b'edited')
    with pytest.raises(ValueError, match='upstream file changed'):
        verify_snapshot(source)


def test_full_pinned_audit_is_repeatable_and_keeps_raw_population(tmp_path):
    source = ROOT / 'data/sources/cachewise'
    first = audit_dataset(source, tmp_path / 'first')
    second = audit_dataset(source, tmp_path / 'second')
    assert first == second
    assert first['counts']['sessions'] == 94
    assert first['counts']['tool_event_rows'] == 11617
    assert first['counts']['candidate_tool_span_rows'] == 9393
    assert first['exact_kv_labels_supported'] is False
    features = [json.loads(s) for s in (tmp_path / 'first/features.jsonl').read_text().splitlines()]
    labels = [json.loads(s) for s in (tmp_path / 'first/labels.jsonl').read_text().splitlines()]
    assert len(features) == len(labels) == first['counts']['tool_event_rows']
    assert [r['sample_id'] for r in features] == [r['sample_id'] for r in labels]
    assert len({r['sample_id'] for r in features}) == len(features)
    assert all(set(r['features']) == {'tool_name', 'elapsed_s'} for r in features)
    for filename, digest in first['output_sha256'].items():
        assert hashlib.sha256((tmp_path / 'first' / filename).read_bytes()).hexdigest() == digest
