import hashlib
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import source_io_guard_v1 as guard


def test_empty_file_and_ancestors_retained_and_replacement_rejected(tmp_path):
    source = tmp_path / 'empty'; source.touch()
    keep = guard.SourcePathHandles([source])
    fds = list(keep.handles)
    try:
        assert keep.verify()['empty_files'] == 1
        assert any(r['directory'] and r['path'] == str(tmp_path) for r in keep.records)
        source.unlink(); source.touch()
        with pytest.raises(ValueError, match='path changed'):
            keep.verify()
    finally:
        keep.close()
    for fd in fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_content_change_rejected_but_new_sibling_allowed(tmp_path):
    source = tmp_path / 'source'; source.write_bytes(b'original')
    keep = guard.SourcePathHandles([source])
    try:
        (tmp_path / 'new-evidence').write_text('ok')
        keep.verify()
        source.write_bytes(b'changed-longer')
        with pytest.raises(ValueError, match='path changed'):
            keep.verify()
    finally:
        keep.close()


def test_digest_preserved_and_attributed_io_not_swallowed(tmp_path, monkeypatch):
    source = tmp_path / 'source'; source.write_bytes(b'contents')
    values = iter([{'read_bytes': 0, 'major_faults': 0, 'block_input_ops': 0},
                   {'read_bytes': 4096, 'major_faults': 0, 'block_input_ops': 8}])
    monkeypatch.setattr(guard, 'thread_counters', lambda: next(values))
    audit = guard.SourceIOAudit()
    assert audit.digest(source) == hashlib.sha256(b'contents').hexdigest()
    event = audit.snapshot()['events'][0]
    assert event['path'] == str(source) and event['delta']['read_bytes'] == 4096
    assert event['delta']['block_input_ops'] == 8


def test_exception_and_counter_survive_failed_operation(monkeypatch):
    monkeypatch.setattr(guard, 'thread_counters', lambda: dict(read_bytes=0, major_faults=0, block_input_ops=0))
    audit = guard.SourceIOAudit()
    def failing():
        raise RuntimeError('native failure')
    with pytest.raises(RuntimeError, match='native failure'):
        audit.measure('native_generate', failing)
    assert audit.snapshot()['stats']['native_generate']['calls'] == 1
    audit.reset('next')
    assert audit.snapshot()['stats'] == {}
