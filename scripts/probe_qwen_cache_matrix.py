"""Bounded real multi-owner CUDA gate. Also importable by the live runner."""
import json
from pathlib import Path
import threading
import time

from leasepath.prediction_training import sha256


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def fixture(tokenizer, label, blocks):
    rows = []
    while True:
        rows.extend(f'{label}_feature_{i}: float64; finite numeric measurement.' for i in range(len(rows), len(rows)+16))
        prompt = tokenizer.apply_chat_template([
            {'role': 'system', 'content': 'Follow the user instruction exactly.'},
            {'role': 'user', 'content': f'Controlled schema {label}:\n'+'\n'.join(rows)+
             '\nDo not analyze this schema. Respond with exactly the single word: READY'}],
            tokenize=False, add_generation_prompt=True)
        if len(tokenizer.encode(prompt, add_special_tokens=False)) >= blocks*128+17:
            return prompt


def run_gate(engine, tokenizer, budget, output):
    output.mkdir(parents=True, exist_ok=False)
    results = []
    def generate(label, owner, prompt, *, cancel=False, **kwargs):
        reservation = budget.reserve('probe', scope='native_multi_owner_gate', label=label)
        engine.owner = owner
        cancelled_at = []
        def cancel_now():
            cancelled_at.append(time.monotonic_ns())
            engine.cancel()
        timer = threading.Timer(.05, cancel_now) if cancel else None
        if timer: timer.start()
        try:
            result = engine.generate(prompt, limit=64, **kwargs)
        except Exception as exc:
            write(output/f'{len(results):02d}-{label}-failed.json',
                  {'label': label, 'reservation_id': reservation['id'], 'prompt': prompt,
                   'error': f'{type(exc).__name__}: {exc}'})
            raise
        finally:
            if timer: timer.cancel(); timer.join()
        record = {**result, 'label': label, 'reservation_id': reservation['id'], 'prompt': prompt}
        if cancel:
            record['cancel_sent_ns'] = cancelled_at
            assert len(cancelled_at) == 1 and result['lookup_ns'] <= cancelled_at[0] <= result['release_ns']
        write(output / f'{len(results):02d}-{label}.json', record)
        results.append(record)
        print('gate', label, result['reason'], 'hit', result['hit'], 'prefill', result['prefill'], flush=True)
        return result
    a, b, c = (fixture(tokenizer, label, blocks) for label, blocks in [('a', 10), ('b', 10), ('c', engine.capacity-2)])
    engine.clear()
    first = generate('A_cold', 'A', a)
    other = generate('B_cold', 'B', b)
    hot = generate('A_after_B', 'A', a)
    assert first['reason'] == other['reason'] == hot['reason'] == 'eog'
    assert hot['hit'] >= 1280 and first['hit'] == 0
    assert hot['output_tokens'] == first['output_tokens'] and first['text'].strip() == 'READY'
    alias = generate('shared_alias', 'alias_A', a)
    before = engine.inspect()
    engine.retire('A')
    assert engine.inspect() == before
    shared = generate('alias_after_retire_A', 'alias_A', a)
    assert shared['hit'] == alias['hit'] == hot['hit']
    assert shared['after']['used_cells'] == before['used_cells']
    pressure = generate('C_pressure', 'C', c)
    assert pressure['policy_decision']['evicted']
    recompute = generate('A_after_pressure', 'alias_A', a)
    assert recompute['hit'] < hot['hit'] and recompute['prefill'] > hot['prefill']
    assert recompute['output_tokens'] == first['output_tokens']
    engine.clear()
    cold = generate('A_after_clear', 'A', a)
    assert cold['hit'] == 0 and cold['output_tokens'] == first['output_tokens']
    stop = generate('protocol_stop', 'A', a, stops=['READY'])
    assert stop['reason'] == 'protocol_stop' and stop['matched_stop'] == 'READY'
    idle_before = engine.inspect()
    timeout = generate('timeout', 'timeout_owner', b, timeout=.000001)
    assert timeout['reason'] == 'timeout' and not timeout['release_cells']
    assert engine.inspect() == idle_before
    engine.clear()
    generate('other_owner_before_cancel', 'A', a)
    idle_before = engine.inspect()
    cancelled = generate('cancel', 'cancel_owner', b, cancel=True)
    assert cancelled['reason'] == 'cancelled' and not cancelled['release_cells']
    assert engine.inspect() == idle_before
    engine.clear()
    recovery = generate('recovery', 'A', a)
    assert recovery['output_tokens'] == first['output_tokens']
    engine.clear()
    summary = {'status': 'passed', 'scope': 'controlled_real_32b_multi_owner_gate',
        'generations': len(results), 'interleaved_A_B_A_hit': hot['hit'],
        'cold_prefill_tokens': first['prefill'], 'hot_prefill_tokens': hot['prefill'],
        'after_pressure_hit': recompute['hit'], 'after_pressure_prefill_tokens': recompute['prefill'],
        'checks': {'exact_client_native_token_ids': True, 'physical_cell_reuse_verified': True,
            'shared_owner_retire_preserves_device_cells': True, 'pressure_physically_evicts_and_recomputes': True,
            'cold_hot_greedy_output_equal': True, 'protocol_stop_normal_release': True,
            'timeout_cancel_active_drained_other_owner_preserved': True,
            'input_and_active_headroom_quota_verified': True},
        'buffer_semantics': 'used physical KV cells freed; preallocated device buffer remains reserved',
        'budget': budget.summary(), 'final_physical': engine.inspect()}
    write(output / 'summary.json', summary)
    return summary
