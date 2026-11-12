"""V2 bounded development collection with causal queue features; no timing acceptance.

This is a new behavioral observation, not a retry of the old pressure matrix.
Model/source identity, generation limits, physical KV and protocol remain hard
checks. Disk I/O and faults are recorded rather than used to select samples.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline, file_digest
from leasepath.preexec_pressure_v2 import PressureEngine, check_topology
from leasepath.controlled_resources import host_sample
from leasepath.prediction_training import read_json
from leasepath.predictive_cache import PredictiveCache
from qwen_cache_independent_agent_batch_v2 import run_batch
from run_qwen_cache_controlled import ScopedBudget
from probe_qwen_cache_matrix import run_gate
from check_qwen_cache_stage2 import check_native
from check_qwen_cache_matrix import check_actions
from audit_pressure_live_state import audit_live_state
from audit_independent_agent_loop_v4 import audit as audit_loop


def write(path, value):
    path = Path(path)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temp.replace(path)


def file_identity(path):
    st = Path(path).stat()
    return [getattr(st, k) for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')]


def process_counters():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    io = dict(line.split(':') for line in Path('/proc/self/io').read_text().splitlines())
    return {'read_bytes': int(io['read_bytes']), 'major_faults': usage.ru_majflt,
            'block_input_ops': usage.ru_inblock}


def require_development(tasks, batches):
    selected = [name for batch in batches for name in batch]
    by_id = {t['id']: t for t in tasks}
    if len(batches) != 1 or len(selected) not in (3, 6) or len(set(selected)) != len(selected):
        raise ValueError('one disjoint three- or six-task development batch required')
    if any(by_id[name]['split'] != 'development' for name in selected):
        raise ValueError('holdout execution prohibited')
    return by_id


def audit_decisions(batch, baseline, compatibility, capacity):
    controller = [json.loads(x) for x in (batch / 'raw/controller.jsonl').read_text().splitlines()]
    backend = [json.loads(x) for x in (batch / 'raw/backend.jsonl').read_text().splitlines()]
    events = [r for r in controller if r['kind'] == 'context_retire']
    events += [r for r in backend if r['kind'] == 'engine_request']
    replica = PredictiveCache(capacity, block_size=128, block_bytes=33554432)
    predictor = baseline.predictor(compatibility)
    metrics = []
    for event in sorted(events, key=lambda r: r['ts_ns']):
        row, now = event['data'], event['ts_ns'] / 1e9
        if event['kind'] == 'context_retire':
            replica.retire(row['owner'], now)
            continue
        check_native(row, capacity, 128, 2048)
        metrics.append(audit_live_state(replica, row, compatibility, predictor, None, now))
        check_actions(replica, row, compatibility, 128, now)
    return {'status': 'passed', 'requests_checked': len(metrics), 'metrics': metrics,
            'natural_JCT_acceptance': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    plan = read_json(args.plan)
    capacity = plan['capacity_blocks']
    if capacity not in (24, 32, 64):
        raise ValueError('unregistered geometry')
    work, evidence = Path(plan['work']), Path(plan['evidence'])
    if (evidence / 'state.json').exists() or work.exists():
        raise ValueError('attempt already exists; no replacement')
    work.mkdir(parents=True)
    write(work / 'frozen-plan.json', plan)
    state = {'status': 'preparing', 'pid': os.getpid(), 'started_wall_ns': time.time_ns(),
             'plan_sha256': file_digest(args.plan), 'new_model_generations': 0,
             'holdout_model_tasks': 0, 'natural_JCT_acceptance': False, 'batches': []}
    state_lock = threading.Lock()
    def save():
        with state_lock:
            state['updated_utc'] = datetime.now(timezone.utc).isoformat()
            state['new_model_generations'] = sum(len(p.read_text().splitlines()) for p in work.rglob('*ledger.jsonl'))
            write(evidence / 'state.json', state)
    def stop(signum, frame):
        raise InterruptedError('bounded pilot stopped by signal ' + str(signum))
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(sig, stop)
    signal.alarm(plan['overall_wall_budget_s'])
    engine = None
    budgets = []
    save()
    try:
        tracer = int(next(x.split(':')[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('TracerPid:')))
        if not tracer:
            raise ValueError('full diagnostic file trace required')
        state['tracer_pid'] = tracer
        if sys.prefix != plan['runtime_prefix']:
            raise ValueError('wrong private Python runtime')
        hashes = plan['source_sha256']
        model = Path(plan['model']['path'])
        def check_sources():
            for path, expected in hashes.items():
                if file_digest(path) != expected:
                    raise ValueError('frozen source changed: ' + path)
            if file_identity(model) != plan['model']['identity']:
                raise ValueError('model identity changed')
        check_sources()
        suite = Path(plan['suite'])
        suite_manifest = read_json(suite / 'manifest.json')
        for name, expected in suite_manifest['source_sha256'].items():
            if file_digest(ROOT / name) != expected:
                raise ValueError('suite source changed: ' + name)
        for name, expected in suite_manifest['artifact_sha256'].items():
            if file_digest(suite / name) != expected:
                raise ValueError('suite artifact changed: ' + name)
        tasks = require_development(read_json(suite / 'tasks.json'), plan['development_batches'])
        references = read_json(suite / 'references.private.json')
        # Parent-only independent checking. References never enter prompts/tools.
        baseline = FrozenEqualCostBaseline(ROOT, ROOT / plan['baseline_manifest'])
        if baseline.evidence() != plan['baseline_evidence']:
            raise ValueError('frozen policy identity changed')
        state['topology'] = check_topology(plan)
        host = host_sample()
        write(work / 'host-before.json', host)
        if host['meminfo_kib']['MemAvailable'] < plan['minimum_available_gib'] * 1024**2:
            raise ValueError('memory admission failed')
        os.sched_setaffinity(0, plan['model_cpus'])
        native = read_json(plan['native_identity'])
        if file_digest(plan['library']) != native['adapter_sha256']:
            raise ValueError('native adapter changed')
        for path, expected in native['linked_runtime_libraries'].items():
            if file_digest(path) != expected:
                raise ValueError('native dependency changed: ' + path)
        runtime = read_json(plan['runtime_manifest'])
        for row in runtime['files']:
            if file_digest(Path(runtime['dst']) / row['relative']) != row['sha256']:
                raise ValueError('private runtime differs: ' + row['relative'])
        receipt_path = Path(plan['model_verification_receipt'])
        if file_digest(receipt_path) != plan['model_verification_receipt_sha256']:
            raise ValueError('prior model verification receipt changed')
        receipt = read_json(receipt_path)
        if (receipt['sha256'] != plan['model']['sha256'] or receipt['identity'] != file_identity(model)
                or receipt['fresh_full_hash_passes'] != 1 or receipt['bytes'] != plan['model']['bytes']):
            raise ValueError('previous full verification no longer matches inode identity')
        state['model_verification_s'] = 0
        state['model_bytes_verified'] = 0
        write(work / 'model-verification.json', {**receipt, 'fresh_full_hash_passes': 0,
              'method': 'reuse prior full SHA256 receipt with unchanged dev/inode/size/mtime/ctime',
              'source_receipt': str(receipt_path), 'residency_claim': False})
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(plan['tokenizer'], local_files_only=True)
        compatibility = hashlib.sha256(json.dumps({'model': receipt['sha256'], 'adapter': native['adapter_sha256'],
            'tokenizer': file_digest(Path(plan['tokenizer']) / 'tokenizer.json'), 'geometry': [capacity, 128],
            'context': 16384, 'kv': 'float16', 'stage': 'independent-loop-observation-v2'}, sort_keys=True).encode()).hexdigest()
        state.update(status='loading_native_model', runtime_compatibility=compatibility); save()
        started = time.monotonic()
        engine = PressureEngine(plan['library'], str(model), tokenizer, capacity=capacity, context=16384,
                   headroom=2048, baseline=baseline, compatibility=compatibility,
                   lock_path=Path(plan['engine_lock']))
        state.update(model_load_s=time.monotonic() - started, device_preflight=engine.resource_preflight)
        selected = {r['uuid'] for r in engine.resource_preflight}
        def check_gpu():
            text = subprocess.check_output(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid', '--format=csv,noheader'], text=True)
            for line in text.splitlines():
                uuid, pid = [s.strip() for s in line.split(',')]
                if uuid in selected and pid != str(os.getpid()):
                    raise RuntimeError('another selected-GPU tenant appeared')
        state['status'] = 'native_protocol_gate'; save()
        probe = ScopedBudget(work / 'probe-ledger.jsonl', limits={'probe': 13, 'pilot': 1, 'total': 13})
        budgets.append(probe)
        gate = run_gate(engine, tokenizer, probe, work / 'native-gate')
        for file in (work / 'native-gate').glob('*.json'):
            if file.name != 'summary.json':
                row = read_json(file)
                if tokenizer.encode(row['prompt'], add_special_tokens=False) != row['tokens']:
                    raise ValueError('probe token IDs differ')
                check_native(row, capacity, 128, 2048)
        state['native_gate'] = gate['status']; save()
        for index, names in enumerate(plan['development_batches']):
            check_sources(); check_gpu(); engine.select_arm('gbdt_distribution')
            directory = work / f'batch-{index}'; directory.mkdir()
            budget = ScopedBudget(directory / 'ledger.jsonl', limits={'probe': 1, 'pilot': 12*len(names), 'total': 12*len(names)})
            budget.scope = {'batch': index, 'capacity': capacity, 'concurrency': len(names),
                            'policy': 'gbdt_distribution', 'split': 'development'}
            budgets.append(budget)
            state.update(status='running_development_batch', current_batch=index); save()
            before = process_counters(); host_before = host_sample()
            result = run_batch(engine, tokenizer, budget, [tasks[n] for n in names], references,
                               directory / 'batch', 'gbdt_distribution', check_sources, plan['tool_controls'], 1)
            after = process_counters()
            write(directory / 'resources.json', {'before': host_before, 'after': host_sample(),
                  'parent_delta': {k: after[k] - before[k] for k in before},
                  'timing_is_observational': True, 'natural_JCT_acceptance': False})
            write(budget.path.with_suffix('.sealed.json'), {'sealed': True, 'sha256': file_digest(budget.path), 'budget': budget.summary()})
            check_sources(); check_gpu()
            loop = audit_loop(directory / 'batch', suite, budget.path, capacity, tokenizer)
            write(directory / 'loop-audit.json', loop)
            decisions = audit_decisions(directory / 'batch', baseline, compatibility, capacity)
            write(directory / 'decision-audit.json', decisions)
            state['batches'].append({'index': index, 'names': names, 'status': result['status'],
                'successes': result['complete_success'], 'generations': result['generations'],
                'certified_loops': loop['certified_loops'], 'path': str(directory), 'natural_JCT_acceptance': False})
            save()
            # Quality failures are retained; structural/native audit failures remain fatal.
        state['status'] = 'completed_behavioral_pilot'
    except BaseException as exc:
        state.update(status='stopped', error=f'{type(exc).__name__}: {exc}')
    finally:
        signal.alarm(0)
        if engine is not None:
            engine.close()
        for budget in budgets:
            if budget.path.exists():
                write(budget.path.with_suffix('.sealed.json'), {'sealed': True, 'sha256': file_digest(budget.path),
                      'budget': budget.summary(), 'reason': state['status']})
        state['finished_wall_ns'] = time.time_ns(); save()
        write(work / 'run-manifest.json', state)
        write(work / 'host-after.json', host_sample())
    if state['status'] != 'completed_behavioral_pilot':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
