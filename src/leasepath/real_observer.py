"""Versioned stage-0 observation streams; no inferred lookup or outcome events.

Each process writes its own stream. A complete boundary requires an explicit
expected-stream list from the controller, gap-free records and terminal hashes.
Backend/agent hooks and their semantic completeness require separate live tests.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

VERSION = 'real-development-observer-v1'


class ObservationError(ValueError):
    pass


class Observer:
    def __init__(self, path, *, run_id, source, rank=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.identity = {'observer_version': VERSION, 'run_id': run_id,
                         'stream_id': uuid.uuid4().hex, 'pid': os.getpid(),
                         'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                         'source': source, 'rank': rank}
        self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self.lock = threading.Lock()
        self.sequence = 0
        self.digest = hashlib.sha256()
        self.closed = False
        self.failed = False
        self.emit('observer_start', clock='CLOCK_MONOTONIC')

    def _append(self, kind, data):
        if self.closed or self.failed or os.getpid() != self.identity['pid']:
            raise ObservationError('closed, failed, or inherited observer')
        record = {**self.identity, 'seq': self.sequence,
                  'ts_ns': time.monotonic_ns(), 'kind': kind, 'data': data}
        try:
            encoded = (json.dumps(record, separators=(',', ':'), allow_nan=False) + '\n').encode()
            view = memoryview(encoded)
            while view:
                written = os.write(self.fd, view)
                if written <= 0:
                    raise OSError('observer write made no progress')
                view = view[written:]
        except Exception:
            self.failed = True
            raise
        self.digest.update(encoded)
        self.sequence += 1

    def emit(self, kind, **data):
        if kind == 'observer_end' or (kind == 'observer_start' and self.sequence):
            raise ObservationError('reserved observer boundary')
        with self.lock:
            self._append(kind, data)

    def finish(self):
        """Controller calls only after this source has drained all actual hooks."""
        with self.lock:
            self._append('observer_end', {'complete': True, 'prior_records': self.sequence,
                                          'prior_sha256': self.digest.hexdigest()})
            try:
                os.fsync(self.fd)
            finally:
                os.close(self.fd)
                self.closed = True

    def abort(self):
        """Close without a complete boundary; validation must reject this stream."""
        with self.lock:
            if not self.closed:
                os.close(self.fd)
                self.closed = True


def validate_stream(path):
    identity = None
    digest = hashlib.sha256()
    previous_ns = -1
    count = 0
    ended = False
    with Path(path).open('rb') as stream:
        for seq, line in enumerate(stream):
            if ended or not line.endswith(b'\n'):
                raise ObservationError('trailing records or incomplete line')
            try:
                record = json.loads(line)
                current = {k: record[k] for k in ('observer_version', 'run_id', 'stream_id', 'pid',
                                                 'boot_id', 'source', 'rank')}
                timestamp = record['ts_ns']
                kind = record['kind']
                data = record['data']
                valid = (record['seq'] == seq and current['observer_version'] == VERSION and
                         type(timestamp) is int and timestamp >= previous_ns and isinstance(data, dict))
            except (ValueError, TypeError, KeyError) as exc:
                raise ObservationError('malformed record') from exc
            if not valid or (identity is not None and current != identity):
                raise ObservationError('sequence gap, identity change, or invalid clock')
            if seq == 0:
                if kind != 'observer_start' or data.get('clock') != 'CLOCK_MONOTONIC':
                    raise ObservationError('missing clock/start evidence')
                identity = current
            elif kind == 'observer_start':
                raise ObservationError('duplicate start')
            if kind == 'observer_end':
                if (data.get('complete') is not True or data.get('prior_records') != seq or
                        data.get('prior_sha256') != digest.hexdigest()):
                    raise ObservationError('incomplete or corrupt terminal boundary')
                ended = True
            digest.update(line)
            previous_ns = timestamp
            count += 1
    if not ended:
        raise ObservationError('no complete boundary; cannot support C')
    return {**identity, 'records': count, 'sha256': digest.hexdigest(), 'complete': True}


def validate_observation(paths, *, expected_stream_ids):
    """Missing processes and mixed boots are rejected, even if files look valid."""
    streams = [validate_stream(p) for p in paths]
    ids = [s['stream_id'] for s in streams]
    expected = list(expected_stream_ids)
    if (not expected or len(set(expected)) != len(expected) or len(set(ids)) != len(ids) or
            set(ids) != set(expected)):
        raise ObservationError('missing, duplicate, or unexpected stream')
    if len({s['boot_id'] for s in streams}) != 1 or len({s['run_id'] for s in streams}) != 1:
        raise ObservationError('mixed observation run or boot; clocks cannot be combined')
    return {'observer_version': VERSION, 'streams': streams, 'complete': True,
            'semantic_completeness_verified': False,
            'scope': 'transport_integrity_only_not_backend_or_label_acceptance'}
