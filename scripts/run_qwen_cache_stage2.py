"""Bounded CUDA gate and real asynchronous tool / serial-model JCT smoke."""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.cache_policy_models import FixedTTLPriority, TransferredPredictor
from leasepath.gbdt_predictor import GBDTRiskPredictor
from leasepath.prediction_training import read_json, sha256
from leasepath.qwen_cache_backend import MultiOwnerEngine
from leasepath.qwen_data_agent import ObservedQwen
from leasepath.qwen_data_ledger import GenerationBudget
from leasepath.qwen_data_tasks import equal
from leasepath.real_observer import Observer
from leasepath.restricted_data_tools import Resource, SessionTools, file_hash, schemas_for
from leasepath.reuse_predictor import EmpiricalRiskPredictor
from probe_qwen_cache_native import run_gate, run_prefill_probe, write
from run_qwen_data_pilot import SYSTEM


def features(engine, owner, tool=None, phase=None, started=None):
    initial, idle_at = engine.cache.idle[owner]
    now = time.monotonic()
    return {**initial, 'elapsed_idle_s': initial['elapsed_idle_s']+now-idle_at,
            'tool_name': tool, 'phase': phase,
            'tool_elapsed_s': now-started if started is not None else None}


def run_batch(engine, tokenizer, budget, tasks, references, output, policy, check_sources):
    output.mkdir(parents=True, exist_ok=False)
    controller = Observer(output/'raw/controller.jsonl', run_id=output.name, source='live_agent_controller')
    backend = Observer(output/'raw/backend.jsonl', run_id=output.name, source='multi_owner_native_cuda')
    engine.clear()
    engine.history = []
    begin = time.monotonic()
    submissions, states, ready, records = {}, {}, deque(), []
    notifications = queue.Queue()
    pool = ThreadPoolExecutor(max_workers=len(tasks))
    def notice(kind, owner, **data):
        notifications.put((time.monotonic(), kind, owner, data))
    def tool_call(owner, tools, call):
        check_sources()
        notice('start', owner, tool_name=call['name'])
        try:
            try: arguments = json.loads(call['arguments'])
            except (ValueError, TypeError): arguments = {'invalid_json': str(call['arguments'])[:200]}
            result = tools.call(call['name'], arguments)
            check_sources()
            notice('end', owner, tool_name=call['name'], result=result)
        except Exception as exc:
            notice('failed', owner, error=f'{type(exc).__name__}: {exc}')
    def finish(owner, final=None, error=None):
        state = states[owner]
        if state['done']: return
        check_start = time.monotonic()
        try:
            check_sources()
            if error is None:
                if not isinstance(final, dict) or set(final) != {'artifact_id', 'answer'}:
                    raise ValueError('final answer schema')
                if final['artifact_id'] not in state['tools'].artifacts:
                    raise ValueError('unknown final artifact')
                expected = references[state['task']['id']]['reference']
                if not equal(state['tools'].artifacts[final['artifact_id']], expected):
                    raise ValueError('computed artifact differs from independent reference')
                if not equal(final['answer'], expected):
                    raise ValueError('final answer differs from independent reference')
            if state['llm'].pending_tool_results:
                raise ValueError('unconsumed tool result at task completion')
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        validated = time.monotonic()
        engine.retire(owner)
        controller.emit('context_retire', owner=owner, context_version='v1', reason='explicit_task_completed_or_failed')
        state['done'] = True
        record = {'task': state['task']['id'], 'owner': owner, 'policy': policy,
            'success': error is None, 'error': error, 'generations': state['llm'].generation_attempts,
            'submit_monotonic_s': submissions[owner], 'validated_monotonic_s': validated,
            'jct_to_validated_answer_s': validated-submissions[owner],
            'validation_s': validated-check_start, 'final': final,
            'tools': state['tools'].calls,
            'model_requests': [r for r in engine.history if r['owner'] == owner]}
        write(output/f'{owner}-messages.json', state['messages'])
        write(output/f'{owner}-quality.json', record)
        records.append(record)
        print('live_task', output.name, record['task'], record['success'], round(record['jct_to_validated_answer_s'], 3), flush=True)
    try:
        for task in tasks:
            owner = task['id']
            submissions[owner] = begin  # Same externally fixed simultaneous arrivals, before per-task preparation.
            controller.emit('workflow_submit', owner=owner, source_group=task['source_group'])
            controller.emit('context_open', owner=owner, context_version='v1', compatibility_id=engine.compatibility)
            tools = SessionTools(output/'sessions'/owner/'artifacts',
                {task['dataset_id']: Resource(Path(task['file']), task['input_sha256'], task['kind'], task['metadata'])},
                task['operations'], timeout=240, threads=2, memory_gib=12)
            llm = ObservedQwen(engine, tokenizer, budget, controller, backend, lane='pilot')
            llm.owner = owner
            llm.check_sources = check_sources
            llm.task_deadline = begin+900
            messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content':
                f"Session dataset_id: {task['dataset_id']}\nTask: {task['intent']}\nUse only this session dataset. "
                "The dataset schema is available from inspect_input. "
                "Use the ✿FUNCTION✿ / ✿ARGS✿ format for each function call; "
                "use bare JSON only for the final artifact_id/answer object."}]
            states[owner] = {'task': task, 'tools': tools, 'llm': llm, 'messages': messages,
                             'done': False, 'tool_running': False}
            ready.append((begin, owner))
        while any(not s['done'] for s in states.values()):
            while True:
                try: observed, kind, owner, data = notifications.get_nowait()
                except queue.Empty: break
                state = states[owner]
                if state['done']: continue
                controller.emit('tool_'+kind, owner=owner, actual_observed_monotonic_s=observed, **data)
                if kind == 'start':
                    state['tool_running'] = True
                    if owner in engine.cache.idle:
                        engine.observe(owner, features(engine, owner, data['tool_name'], 'waiting_tool', observed))
                elif kind == 'end':
                    state['tool_running'] = False
                    result_json = json.dumps(data['result'], allow_nan=False, separators=(',', ':'))
                    state['messages'].append({'role': 'function', 'name': data['tool_name'], 'content': result_json})
                    state['llm'].pending_tool_results.append(result_json)
                    if owner in engine.cache.idle:
                        engine.observe(owner, features(engine, owner, data['tool_name']))
                    ready.append((observed, owner))
                elif kind == 'failed':
                    state['tool_running'] = False
                    finish(owner, error=data['error'])
            if not ready:
                # Wait only when no runnable model work exists; genuine tools run concurrently.
                time.sleep(.002)
                if time.monotonic()-begin > 900:
                    raise TimeoutError('live batch wall budget')
                continue
            ready = deque(sorted(ready))
            _, owner = ready.popleft()
            state = states[owner]
            if state['done']: continue
            try:
                if state['llm'].generation_attempts >= 6:
                    raise RuntimeError('six-generation task budget')
                if policy == 'no_reuse': engine.clear()
                engine.owner = owner
                replies = state['llm'].chat(state['messages'], functions=schemas_for(state['task']['operations']), stream=False)
                state['messages'].extend(replies)
                calls = [m for m in replies if m.get('function_call')]
                if not calls:
                    final = json.loads(''.join(m.get('content', '') for m in replies).strip())
                    finish(owner, final=final)
                else:
                    if len(calls) != 1: raise ValueError('sequential function protocol required')
                    pool.submit(tool_call, owner, state['tools'], calls[0]['function_call'])
            except Exception as exc:
                finish(owner, error=f'{type(exc).__name__}: {exc}')
        pool.shutdown(wait=True)
        ending = time.monotonic()
        controller.emit('observation_end', complete=True)
        controller.finish(); backend.finish()
        summary = {'status': 'passed' if all(r['success'] for r in records) else 'quality_failed',
            'policy': policy, 'complete_success': sum(r['success'] for r in records), 'tasks': len(tasks),
            'makespan_s': ending-begin,
            'mean_successful_jct_s': sum(r['jct_to_validated_answer_s'] for r in records if r['success']) / max(1, sum(r['success'] for r in records)),
            'cached_tokens': sum(r['hit'] for x in records for r in x['model_requests']),
            'executed_prefill_tokens': sum(r['prefill'] for x in records for r in x['model_requests']),
            'prefill_s': sum(r['prefill_ns']/1e9 for x in records for r in x['model_requests']),
            'decode_s': sum(r['decode_ns']/1e9 for x in records for r in x['model_requests']),
            'prediction_s': engine.cache.prediction_ns/1e9, 'prediction_calls': engine.cache.prediction_calls,
            'evictions': engine.cache.evictions, 'generations': sum(r['generations'] for r in records),
            'final_physical': engine.inspect(), 'quality_records': [r['task']+'-quality.json' for r in records],
            'evidence_kind': 'measured_real_32b_and_real_parameter_tools',
            'scope': 'three-parent system smoke; descriptive JCT, not independent final evaluation',
            'policy_overhead_included_in_jct': True, 'model_load_excluded_and_separately_reported': True}
        write(output/'summary.json', summary)
        return summary
    finally:
        pool.shutdown(wait=True)
        controller.abort(); backend.abort()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan', type=Path, default=ROOT/'configs/prediction/qwen_cache_stage2_2026-11-08.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--gate-only', action='store_true')
    args = parser.parse_args()
    plan = read_json(args.plan)
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'frozen-plan.json', plan)
    copy = read_json(ROOT/'.artifacts/qwen-cache-stage2/model-copy.json')
    if copy['sha256'] != plan['model_sha256']: raise ValueError('unverified model copy')
    write(args.output/'model-copy.json', copy)
    library = ROOT/'.artifacts/qwen-cache-stage2/native/backend.so'
    from transformers import AutoTokenizer
    tokenizer_dir = Path('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf')
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True)
    compatibility = hashlib.sha256(json.dumps({'model_sha256': plan['model_sha256'], 'library': sha256(library),
        'tokenizer': sha256(tokenizer_dir/'tokenizer.json'), 'context': plan['physical_context'],
        'kv_dtype': 'float16', 'geometry': [plan['capacity_blocks'], plan['block_tokens']]}, sort_keys=True).encode()).hexdigest()
    budget = GenerationBudget(ROOT/'.artifacts/qwen-cache-stage2/ledger.jsonl', limits=plan['budget_limits'])
    taskdirs = [ROOT/'.artifacts/qwen-data-tasks-v5', ROOT/'.artifacts/qwen-data-tasks-extension38-v2']
    tasks, refs = [], {}
    protected = [args.plan, library, Path(__file__).resolve(), ROOT/'scripts/probe_qwen_cache_native.py',
                 tokenizer_dir/'tokenizer.json', tokenizer_dir/'tokenizer_config.json',
                 ROOT/'native/qwen_cache_cuda.cpp', ROOT/'native/qwen_data_cuda.cpp',
                 ROOT/'scripts/build_qwen_cache_cuda.sh']
    protected += list((ROOT/'src/leasepath').glob('qwen_*.py')) + list((ROOT/'src/leasepath').glob('restricted_data_*.py'))
    protected += [ROOT/'src/leasepath'/s for s in ['predictive_cache.py', 'cache_policy_models.py', 'reuse_predictor.py', 'gbdt_predictor.py']]
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
        'plan_sha256': sha256(args.plan), 'source_sha256': hashes, 'runtime_compatibility': compatibility,
        'training_compatibility': training_compatibility,
        'statistical_transfer': 'explicit mapping after new native gate; same model, tokenizer, F16 KV, context and coarse features; tensor identities remain separate',
        'budget_at_start': budget.summary(), 'started_wall_ns': time.time_ns()}
    write(args.output/'run-manifest.json', manifest)
    engine = None
    try:
        start = time.monotonic()
        engine = MultiOwnerEngine(library, copy['dst'], tokenizer, capacity=plan['capacity_blocks'],
            context=plan['physical_context'], block_size=plan['block_tokens'], headroom=plan['transient_headroom_tokens'],
            compatibility=compatibility, lock_path=ROOT/'.artifacts/qwen-cache-stage2/engine.lock')
        manifest['model_load_s'] = time.monotonic()-start
        manifest['device_preflight'] = engine.resource_preflight
        write(args.output/'run-manifest.json', manifest)
        gate = run_gate(engine, tokenizer, budget, args.output/'native-gate')
        timings = run_prefill_probe(engine, tokenizer, budget, args.output/'prefill-probe')
        results = []
        if not args.gate_only:
            raw_models = {'conditional': EmpiricalRiskPredictor.load(prediction/'conditional-model.json'),
                          'gbdt': GBDTRiskPredictor.load(prediction/'gbdt-model.json')}
            models = {name: TransferredPredictor(model, runtime_compatibility=compatibility,
                                                training_compatibility=training_compatibility) for name, model in raw_models.items()}
            for repeat in range(plan['repeats']):
                policies = plan['policies'][repeat*2:] + plan['policies'][:repeat*2]
                for name in policies:
                    engine.clear()
                    engine.policy = 'predicted_reuse' if name in {'conditional', 'gbdt', 'fixed_ttl'} else 'lru' if name == 'no_reuse' else name
                    engine.predictor = FixedTTLPriority(plan['fixed_ttl_s']) if name == 'fixed_ttl' else models.get(name)
                    engine.clear()
                    result = run_batch(engine, tokenizer, budget, tasks, refs,
                        args.output/f'repeat-{repeat}-{name}', name, check_sources)
                    results.append({'repeat': repeat, **result})
                    write(args.output/'progress.json', {'completed_batches': len(results), 'results': results, 'budget': budget.summary()})
                    if len(results) == 1 and result['status'] != 'passed':
                        raise RuntimeError('first real protocol batch failed; later comparisons stopped')
        check_sources()
        summary = {'status': 'passed' if all(r['status'] == 'passed' for r in results) else 'quality_failed',
            'scope': 'native multi-owner gate and bounded three-parent end-to-end development smoke',
            'gate': gate, 'prefill_probe': timings, 'batches': results, 'budget': budget.summary(),
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
