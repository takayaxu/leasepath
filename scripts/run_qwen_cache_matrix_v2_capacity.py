"""One physical capacity of the fixed real protocol pressure matrix."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.cache_policy_models import TransferredPredictor
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import read_json, sha256
from leasepath.cache_matrix import AuditedEngine as MultiOwnerEngine
from leasepath.cache_matrix_continuation import ContinuedMatrixBudget as MatrixBudget
from qwen_cache_stage3b_batch import run_batch
from leasepath.restricted_data_tools import file_hash
from leasepath.reuse_predictor import EmpiricalRiskPredictor
from probe_qwen_cache_matrix import run_gate, write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, default=ROOT/'configs/prediction/qwen_cache_stage3b_2026-11-08.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--capacity', type=int, required=True)
    args = parser.parse_args()
    plan = read_json(args.plan)
    if args.capacity not in plan['capacities_blocks']: raise ValueError('unplanned capacity')
    capacity = args.capacity
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'frozen-plan.json', plan)
    copy = read_json(ROOT/'.artifacts/qwen-cache-stage3/model-copy.json')
    if copy['sha256'] != plan['model_sha256']: raise ValueError('unverified model copy')
    write(args.output/'model-copy.json', copy)
    library = ROOT/'.artifacts/qwen-cache-stage2/native/backend.so'
    identity = read_json(ROOT/'.artifacts/qwen-cache-stage3/native-identity.json')
    if sha256(library) != identity['adapter_sha256']:
        raise ValueError('verified adapter changed')
    def check_runtime():
        for name, digest in identity['linked_runtime_libraries'].items():
            if sha256(name) != digest: raise ValueError('runtime library changed: '+name)
    check_runtime()
    from transformers import AutoTokenizer
    tokenizer_dir = Path('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf')
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True)
    compatibility = hashlib.sha256(json.dumps({'model_sha256': plan['model_sha256'], 'library': sha256(library),
        'tokenizer': sha256(tokenizer_dir/'tokenizer.json'), 'context': plan['physical_context'],
        'kv_dtype': 'float16', 'geometry': [capacity, plan['block_tokens']]}, sort_keys=True).encode()).hexdigest()
    budget = MatrixBudget(ROOT/'.artifacts/qwen-cache-stage3/attempt-b-ledger.jsonl', plan, scope={'capacity': capacity})
    taskdirs = [ROOT/'.artifacts/qwen-data-tasks-v5', ROOT/'.artifacts/qwen-data-tasks-extension38-v2']
    tasks, refs = [], {}
    protected = [args.plan, library, Path(__file__).resolve(), ROOT/'scripts/probe_qwen_cache_matrix.py', ROOT/'scripts/qwen_cache_stage3b_batch.py', ROOT/'src/leasepath/cache_matrix_continuation.py', ROOT/'scripts/run_qwen_cache_matrix.py', ROOT/'scripts/run_qwen_cache_matrix_v2.py', ROOT/'src/leasepath/cache_matrix.py',
                 tokenizer_dir/'tokenizer.json', tokenizer_dir/'tokenizer_config.json',
                 ROOT/'native/qwen_cache_cuda.cpp', ROOT/'native/qwen_data_cuda.cpp',
                 ROOT/'scripts/build_qwen_cache_cuda.sh']
    protected += list((ROOT/'src/leasepath').glob('qwen_*.py')) + list((ROOT/'src/leasepath').glob('restricted_data_*.py'))
    protected += [ROOT/'src/leasepath'/s for s in ['predictive_cache.py', 'cache_policy_models.py', 'reuse_predictor.py', 'gbdt_predictor.py']]
    protected += [Path(plan[k]) for k in ['prior_failed_ledger', 'prior_failed_audit']]
    protected.append(Path(plan['prior_failed_ledger']).with_suffix('.sealed.json'))
    for directory in taskdirs:
        tasks.extend(t for t in read_json(directory/'tasks.private.json') if t['id'] in plan['live_tasks'])
        refs.update(read_json(directory/'references.private.json'))
        protected += [directory/s for s in ['tasks.private.json', 'references.private.json', 'tool-schemas.json'] if (directory/s).exists()]
    tasks.sort(key=lambda t: plan['live_tasks'].index(t['id']))
    if {t['id'] for t in tasks} != set(plan['live_tasks']): raise ValueError('missing live tasks')
    for t in tasks:
        if file_hash(t['file']) != t['input_sha256']: raise ValueError('frozen input differs')
    hashes = {str(p.resolve()): file_hash(p) for p in protected}
    for p in protected:
        p = p.resolve()
        if p.is_relative_to(ROOT) and p.suffix in {'.py', '.cpp', '.sh'}:
            dest = args.output/'frozen-source'/p.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    def check_sources():
        for name, digest in hashes.items():
            if file_hash(name) != digest: raise ValueError('protected source changed: '+name)
    prediction = ROOT/plan['prediction_result']
    for name, digest in read_json(prediction/'SHA256SUMS.json').items():
        if sha256(prediction/name) != digest: raise ValueError('frozen prediction changed')
    hashes.update({str((prediction/name).resolve()): sha256(prediction/name)
                   for name in ['conditional-model.json', 'gbdt-model.json']})
    training_compatibility = read_json(prediction/'conditional-model.json')['compatibilities'][0]
    manifest = {'status': 'started', 'argv': sys.argv, 'pid': os.getpid(), 'python': platform.python_version(),
        'capacity': capacity, 'plan_sha256': sha256(args.plan), 'source_sha256': hashes, 'runtime_compatibility': compatibility,
        'runtime_libraries_sha256': identity['linked_runtime_libraries'],
        'training_compatibility': training_compatibility,
        'statistical_transfer': 'explicit mapping after new native gate; same model, tokenizer, F16 KV, context and coarse features; tensor identities remain separate',
        'budget_at_start': budget.summary(), 'started_wall_ns': time.time_ns()}
    write(args.output/'run-manifest.json', manifest)
    engine = None
    try:
        start = time.monotonic()
        engine = MultiOwnerEngine(library, copy['dst'], tokenizer, capacity=capacity,
            context=plan['physical_context'], block_size=plan['block_tokens'], headroom=plan['transient_headroom_tokens'],
            compatibility=compatibility, lock_path=ROOT/'.artifacts/qwen-cache-stage3/engine.lock')
        manifest['model_load_s'] = time.monotonic()-start
        manifest['device_preflight'] = engine.resource_preflight
        write(args.output/'run-manifest.json', manifest)
        gate = run_gate(engine, tokenizer, budget, args.output/'native-gate')
        results = []
        raw_models = {'conditional': EmpiricalRiskPredictor.load(prediction/'conditional-model.json'),
                      'gbdt': GBDTRiskPredictor.load(prediction/'gbdt-model.json')}
        models = {name: TransferredPredictor(model, runtime_compatibility=compatibility,
                                            training_compatibility=training_compatibility) for name, model in raw_models.items()}
        for concurrency in plan['concurrencies']:
            selected = tasks[:concurrency]
            for repeat in range(plan['repeats']):
                policies = plan['policies'][repeat:] + plan['policies'][:repeat]
                for name in policies:
                    check_sources()
                    budget.scope = {'capacity': capacity, 'concurrency': concurrency, 'repeat': repeat, 'policy': name}
                    engine.clear()
                    engine.policy = 'predicted_reuse' if name in models else name
                    engine.predictor = models.get(name)
                    engine.clear()
                    result = run_batch(engine, tokenizer, budget, selected, refs,
                        args.output/f'concurrency-{concurrency}-repeat-{repeat}-{name}', name, check_sources)
                    results.append({'capacity': capacity, 'concurrency': concurrency, 'repeat': repeat, **result})
                    write(args.output/'progress.json', {'completed_batches': len(results), 'results': results, 'budget': budget.summary()})
                    if result['status'] != 'passed':
                        raise RuntimeError('real protocol batch failed; matrix stopped, all attempts retained')
        check_sources()
        check_runtime()
        summary = {'status': 'passed' if all(r['status'] == 'passed' for r in results) else 'quality_failed',
            'scope': 'fixed pressure matrix; known development parents',
            'capacity': capacity, 'gate': gate, 'batches': results, 'budget': budget.summary(),
            'model_load_s': manifest['model_load_s'], 'new_model_generations_only': True,
            'no_final_independent_performance_claim': True}
        write(args.output/'summary.json', summary)
        manifest['status'] = summary['status']
    except BaseException as exc:
        manifest.update(status='failed', error=f'{type(exc).__name__}: {exc}', budget=budget.summary())
        write(args.output/'failure.json', manifest)
        raise
    finally:
        if engine: engine.close()
        manifest['finished_wall_ns'] = time.time_ns()
        manifest['budget_at_end'] = budget.summary()
        write(args.output/'run-manifest.json', manifest)


if __name__ == '__main__': main()
