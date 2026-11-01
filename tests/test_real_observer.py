"""Transport integrity tests are synthetic and do not establish backend U/D/C."""
from concurrent.futures import ThreadPoolExecutor
import pytest

from leasepath.real_observer import Observer, ObservationError, validate_observation, validate_stream


def test_concurrent_records_are_sequenced_and_complete(tmp_path):
    path = tmp_path / 'stream.jsonl'
    observer = Observer(path, run_id='test', source='controlled_test', rank=0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: observer.emit('test_event', n=n), range(40)))
    observer.finish()
    result = validate_observation([path], expected_stream_ids=[observer.identity['stream_id']])
    assert result['complete'] and result['streams'][0]['records'] == 42
    assert not result['semantic_completeness_verified']
    with pytest.raises(ObservationError):
        observer.emit('late')


def test_event_gap_and_payload_corruption_rejected(tmp_path):
    path = tmp_path / 'stream.jsonl'
    observer = Observer(path, run_id='test', source='controlled_test')
    observer.emit('test_event', value='original')
    observer.finish()
    original = path.read_bytes()
    lines = original.splitlines(keepends=True)
    path.write_bytes(lines[0] + lines[2])
    with pytest.raises(ObservationError, match='sequence gap'):
        validate_stream(path)
    path.write_bytes(original.replace(b'original', b'modified'))
    with pytest.raises(ObservationError, match='corrupt terminal'):
        validate_stream(path)


def test_crash_or_truncated_last_line_cannot_support_complete_boundary(tmp_path):
    path = tmp_path / 'stream.jsonl'
    observer = Observer(path, run_id='test', source='controlled_test')
    observer.abort()
    with pytest.raises(ObservationError, match='no complete boundary'):
        validate_stream(path)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ObservationError, match='incomplete line'):
        validate_stream(path)


def test_missing_stream_and_mixed_run_rejected(tmp_path):
    paths, ids = [], []
    for run in ['first', 'second']:
        path = tmp_path / (run + '.jsonl')
        observer = Observer(path, run_id=run, source='controlled_test')
        ids.append(observer.identity['stream_id'])
        observer.finish()
        paths.append(path)
    with pytest.raises(ObservationError, match='missing'):
        validate_observation(paths[:1], expected_stream_ids=ids)
    with pytest.raises(ObservationError, match='mixed observation'):
        validate_observation(paths, expected_stream_ids=ids)


def test_reserved_boundary_and_non_json_event_fail_closed(tmp_path):
    observer = Observer(tmp_path / 'stream.jsonl', run_id='test', source='controlled_test')
    with pytest.raises(ObservationError, match='reserved'):
        observer.emit('observer_end', complete=True)
    with pytest.raises(ValueError):
        observer.emit('invalid', n=float('nan'))
    with pytest.raises(ObservationError, match='failed'):
        observer.finish()
    observer.abort()
