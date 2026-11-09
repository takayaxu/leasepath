"""Versioned real batch loop; retain the stage-2 runner byte for byte."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import time
from leasepath.qwen_data_agent import ObservedQwen
from leasepath.qwen_data_tasks import equal
from leasepath.real_observer import Observer
from leasepath.restricted_data_tools import Resource, schemas_for
from leasepath.restricted_data_single_thread_tools import SingleThreadSessionTools
from leasepath.controlled_window_tools import WindowSessionTools
from probe_qwen_cache_native import write
from run_qwen_data_pilot import SYSTEM

SYSTEM += '\nFinal JSON type contract: answer is an OBJECT. Preserve every root key in the complete compute result. '
SYSTEM += 'For example, compute result {"rows":[["A",1]]} requires "answer":{"rows":[["A",1]]}. '
SYSTEM += 'For result {"value":1}, use "answer":{"value":1}. Copy the complete result object, including its root keys.\n'

def features(engine, owner, tool=None, phase=None, started=None):
    initial, idle_at = engine.cache.idle[owner]
    now = time.monotonic()
    return {**initial, 'elapsed_idle_s': initial['elapsed_idle_s']+now-idle_at,
            'tool_name': tool, 'phase': phase,
            'tool_elapsed_s': now-started if started is not None else None}


def run_batch(engine, tokenizer, budget, tasks, references, output, policy, check_sources, tool_controls, tool_threads, response_windows=None):
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
            if response_windows is None:
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
            tool_class = WindowSessionTools if response_windows is not None else SingleThreadSessionTools
            tool_options = {'response_windows': response_windows[owner], 'release_guard': check_sources} if response_windows is not None else {}
            tools = tool_class(output/'sessions'/owner/'artifacts',
                {task['dataset_id']: Resource(Path(task['file']), task['input_sha256'], task['kind'], task['metadata'])},
                task['operations'], timeout=240, threads=tool_threads, memory_gib=12,
                controls=tool_controls[owner], session_key=owner, **tool_options)
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
            'scope': 'shared-server paired experiment; known development parents; controlled response and natural phases reported separately',
            'tool_response_mode': 'fixed_window_real_computation' if response_windows is not None else 'natural_real_computation',
            'policy_overhead_included_in_jct': True, 'model_load_excluded_and_separately_reported': True}
        write(output/'summary.json', summary)
        return summary
    finally:
        pool.shutdown(wait=True)
        controller.abort(); backend.abort()
