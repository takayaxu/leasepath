import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from stage_pressure_model_v1 import digest, identity
from pressure_disk_model_v1 import verify_disk_model, admit_disk_model


def launch(tmp_path, wrong_hash=False):
    model = tmp_path / 'model'; model.write_bytes(b'bounded-model\x00' * 10001)
    prep = {'model_path': str(model), 'model_identity': identity(model), 'bytes': model.stat().st_size,
            'sha256': '0' * 64 if wrong_hash else digest(model), 'source_sha256': {},
            'chunk_bytes': 32768, 'hash_buffer_bytes': 4096, 'minimum_total_bytes': 0,
            'preparation_wall_budget_s': 10, 'maximum_hold_wall_s': 30,
            'cpus': [min(os.sched_getaffinity(0))], 'address_space_bytes': 2 * 1024**3}
    plan = tmp_path / 'plan.json'; plan.write_text(json.dumps(prep))
    progress = tmp_path / 'progress.json'; ready = tmp_path / 'ready.json'
    proc = subprocess.Popen([sys.executable, str(ROOT / 'scripts/pressure_disk_keeper_v1.py'),
                             '--plan', str(plan), '--progress', str(progress), '--ready', str(ready)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc, plan, progress, ready, prep


def await_ready(proc, ready):
    deadline = time.monotonic() + 10
    while not ready.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(.02)
    assert ready.exists()


def registration(plan, ready, prep):
    return {'disk_model': {'plan': str(plan), 'plan_sha256': digest(plan),
                           'ready': str(ready), 'ready_sha256': digest(ready)},
            'model_sha256': prep['sha256'],
            'memory_budget': {'model_bytes': prep['bytes'], 'minimum_MemAvailable_gib': 0}}


def test_live_lock_two_hashes_and_dead_owner_rejected(tmp_path):
    proc, plan, progress, ready, prep = launch(tmp_path)
    try:
        await_ready(proc, ready)
        native = registration(plan, ready, prep)
        verified = verify_disk_model(native)
        assert admit_disk_model(native, verified)['owned_model_bytes'] == prep['bytes']
        proof = json.loads(ready.read_text()); proof['passes'].pop()
        ready.write_text(json.dumps(proof)); native['disk_model']['ready_sha256'] = digest(ready)
        with pytest.raises(ValueError, match='two complete'):
            verify_disk_model(native)
        proof['passes'].append({'pass': 2, 'bytes': prep['bytes'], 'sha256': prep['sha256']})
        ready.write_text(json.dumps(proof)); native['disk_model']['ready_sha256'] = digest(ready)
        proc.terminate(); proc.wait(timeout=5)
        with pytest.raises((ValueError, FileNotFoundError)):
            verify_disk_model(native)
        assert json.loads(progress.read_text())['status'] == 'released'
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()


def test_hash_failure_never_publishes_ready(tmp_path):
    proc, plan, progress, ready, prep = launch(tmp_path, wrong_hash=True)
    try:
        assert proc.wait(timeout=10) != 0
        assert not ready.exists()
        assert json.loads(progress.read_text())['status'] == 'failed'
    finally:
        if proc.poll() is None:
            proc.kill(); proc.wait()


def test_modified_model_invalidates_admission(tmp_path):
    proc, plan, progress, ready, prep = launch(tmp_path)
    try:
        await_ready(proc, ready)
        native = registration(plan, ready, prep)
        with Path(prep['model_path']).open('r+b') as stream:
            stream.write(b'changed')
        with pytest.raises(ValueError, match='identity changed'):
            verify_disk_model(native)
    finally:
        if proc.poll() is None:
            proc.terminate(); proc.wait(timeout=5)
