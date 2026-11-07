"""Real CUDA checks; all generation attempts are reserved before engine entry."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import threading
import time

from leasepath.qwen_data_backend import NativeEngine, verify_request
from leasepath.qwen_data_ledger import GenerationBudget
from leasepath.qwen_data_audit import audit
from leasepath.real_observer import Observer
from leasepath.restricted_data_tools import file_hash

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--tokenizer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--gpus', type=int, default=3)
    parser.add_argument('--infrastructure-only', action='store_true')
    parser.add_argument('--model-identity',type=Path)
    args = parser.parse_args()
    if not args.infrastructure_only:
        if args.model_identity is None:raise ValueError('verified 32B conversion identity required')
        identity=json.loads(args.model_identity.read_text())
        if (identity['repository']!='Qwen/Qwen2.5-Coder-32B-Instruct' or
                identity['revision']!='381fc969f78efac66bc87ff7ddeadb7e73c218a7' or
                identity['gguf_sha256']!=file_hash(args.model) or identity['dtype']!='float16' or
                identity['quantization'] is not None):raise ValueError('unexpected 32B model identity')
    args.output.mkdir(parents=True, exist_ok=False)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(args.tokenizer), local_files_only=True)
    engine = NativeEngine(args.library, args.model, gpus=args.gpus, context=16384)
    run_id=args.output.name+'-controlled'
    controller=Observer(args.output/'raw/controller.jsonl',run_id=run_id,source='boundary_controller')
    backend=Observer(args.output/'raw/backend.jsonl',run_id=run_id,source='native_cuda_engine')
    compatibility=hashlib.sha256(json.dumps({
        'model':file_hash(args.model_identity or args.model),'library':file_hash(args.library),
        'tokenizer':file_hash(args.tokenizer/'tokenizer.json'),'context':16384,'kv_dtype':'float16'},
        sort_keys=True).encode()).hexdigest()
    (args.output/'identity.json').write_text(json.dumps({'run_id':run_id,'compatibility_id':compatibility,
        'tokenizer':str(args.tokenizer.resolve()),'device_preflight':engine.resource_preflight,
        'expected_stream_ids':[controller.identity['stream_id'],backend.identity['stream_id']]},indent=2)+'\n')
    results = []
    ledger = args.output/'ledger.jsonl'
    budget=GenerationBudget(args.ledger)
    def open_context(owner):
        controller.emit('workflow_submit',owner=owner)
        controller.emit('context_open',owner=owner,context_version='v1',compatibility_id=compatibility)

    def generate(label, prompt, *, owner, cancel_after=None, **kwargs):
        # Same frozen count applies to errors and cancellation.
        reservation=budget.reserve('probe',label=label,scope='backend_boundaries')
        with ledger.open('a') as fd:
            fd.write(json.dumps({'kind': 'generation_reserved', 'label': label,
                                'ts_ns': time.monotonic_ns(), 'lane': 'probe',
                                'reservation':reservation['id']})+'\n')
            fd.flush()
            os.fsync(fd.fileno())
        expected=tokenizer.encode(prompt,add_special_tokens=False)
        prompt_sha=hashlib.sha256(prompt.encode()).hexdigest()
        context={'request_id':reservation['id'],'owner':owner,'context_version':'v1','prompt_sha256':prompt_sha}
        controller.emit('request_input',prompt=prompt,token_ids=expected,**context)
        cancelled_at=[]
        def cancel():
            cancelled_at.append(time.monotonic_ns())
            engine.cancel()
        timer=threading.Timer(cancel_after,cancel) if cancel_after is not None else None
        if timer:timer.start()
        try:
            result = engine.generate(prompt, **kwargs)
        finally:
            if timer:timer.cancel();timer.join()
        if cancel_after is not None:
            assert len(cancelled_at)==1 and result['lookup_ns']<=cancelled_at[0]<=result['release_ns']
            result['cancel_sent_ns']=cancelled_at[0]
        verify_request(result,expected)
        result.update(context,label=label)
        backend.emit('engine_request',**result)
        results.append(result)
        (args.output/f'{label}.json').write_text(json.dumps(result)+'\n')
        print(label, result['reason'], 'hit', result['hit'], 'tokens', len(result['tokens']), flush=True)
        return result
    prompt = tokenizer.apply_chat_template([
        {'role': 'system', 'content': 'Follow the user instruction exactly.'},
        {'role': 'user', 'content': 'Respond with exactly the single word: READY'}],
        tokenize=False, add_generation_prompt=True)
    try:
        open_context('reuse')
        a = generate('first', prompt, owner='reuse', limit=64)
        b = generate('hit', prompt, owner='reuse', limit=64)
        engine.clear()
        backend.emit('cache_clear',owner='reuse',reason='controlled physical miss')
        c = generate('controlled_miss', prompt, owner='reuse', limit=64)
        assert b['hit'] >= (len(b['tokens'])-1)//16*16 and b['hit']>0 and c['hit'] == 0
        assert a['text']==b['text']==c['text']
        assert a['output_tokens']==b['output_tokens']==c['output_tokens']
        assert a['reason'] == 'eog'
        controller.emit('context_retire',owner='reuse',context_version='v1',reason='explicit_controlled_discard')
        word = a['text'].strip()
        assert word
        open_context('censor')
        d = generate('protocol_stop', prompt, owner='censor', limit=64, stops=[word])
        assert d['reason'] == 'protocol_stop' and d['matched_stop'] == word
        open_context('termination')
        e = generate('length', prompt, owner='termination', limit=1)
        assert e['reason'] == 'length'
        long_prompt=tokenizer.apply_chat_template([
            {'role':'user','content':'List the first 1000 positive integers, one per line.'}],
            tokenize=False,add_generation_prompt=True)
        f = generate('timeout', long_prompt, owner='termination', limit=1024, timeout=0.02)
        assert f['reason'] == 'timeout'
        g = generate('cancel', long_prompt, owner='termination', limit=1024,cancel_after=0.02)
        assert g['reason'] == 'cancelled'
        short = generate('short_prefix', 'Hello', owner='termination', limit=1)
        assert len(short['tokens'])<16
        engine.close()
        controller.emit('observation_end',complete=True)
        backend.finish();controller.finish()
        audit_result=audit(args.output,export=True)
        assert audit_result['labels']=={'legal_demand':2,'retire':1,'right_censored':1}
        summary = {
            'status': 'passed', 'evidence_kind': 'measured',
            'scope': 'infrastructure_only' if args.infrastructure_only else '32b_backend_boundaries',
            'generations': len(results), 'reasons': dict(Counter(r['reason'] for r in results)),
            'exact_prompt_ids': True, 'physical_kv_cells_checked': True,
            'protocol_stop_is_normal_release': True,
            'cache_hit_and_miss_preserve_demand_identity': True,
            'main_pilot_passed': False}
        summary['budget']=budget.summary()
        summary['controlled_audit']=audit_result
        summary['model_identity_sha256']=file_hash(args.model_identity) if args.model_identity else None
        (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    finally:
        engine.close()
        backend.abort();controller.abort()

if __name__ == '__main__':
    main()
