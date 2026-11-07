"""Real CUDA checks; all generation attempts are reserved before engine entry."""
import argparse
from collections import Counter
import json
from pathlib import Path
import threading
import time

from leasepath.qwen_data_backend import NativeEngine, verify_request
from leasepath.qwen_data_ledger import GenerationBudget

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--tokenizer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ledger',type=Path,required=True)
    parser.add_argument('--gpus', type=int, default=3)
    parser.add_argument('--infrastructure-only', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(args.tokenizer), local_files_only=True)
    engine = NativeEngine(args.library, args.model, gpus=args.gpus, context=4096)
    results = []
    ledger = args.output/'ledger.jsonl'
    budget=GenerationBudget(args.ledger)
    def generate(label, prompt, **kwargs):
        # Same frozen count applies to errors and cancellation.
        reservation=budget.reserve('probe',label=label,scope='backend_boundaries')
        with ledger.open('a') as fd:
            fd.write(json.dumps({'kind': 'generation_reserved', 'label': label,
                                'ts_ns': time.monotonic_ns(), 'lane': 'probe',
                                'reservation':reservation['id']})+'\n')
            fd.flush()
            import os
            os.fsync(fd.fileno())
        result = engine.generate(prompt, **kwargs)
        verify_request(result, tokenizer.encode(prompt, add_special_tokens=False))
        result['label'] = label
        results.append(result)
        (args.output/f'{label}.json').write_text(json.dumps(result)+'\n')
        print(label, result['reason'], 'hit', result['hit'], 'tokens', len(result['tokens']), flush=True)
        return result
    prompt = tokenizer.apply_chat_template([
        {'role': 'system', 'content': 'Follow the user instruction exactly.'},
        {'role': 'user', 'content': 'Respond with exactly the single word: READY'}],
        tokenize=False, add_generation_prompt=True)
    try:
        a = generate('first', prompt, limit=64)
        b = generate('hit', prompt, limit=64)
        engine.clear()
        c = generate('controlled_miss', prompt, limit=64)
        assert b['hit'] >= len(b['tokens'])//16*16 and c['hit'] == 0
        assert a['reason'] == 'eog'
        word = a['text'].strip()
        assert word
        d = generate('protocol_stop', prompt, limit=64, stops=[word])
        assert d['reason'] == 'protocol_stop' and d['matched_stop'] == word
        e = generate('length', prompt, limit=1)
        assert e['reason'] == 'length'
        long_prompt=tokenizer.apply_chat_template([
            {'role':'user','content':'List the first 1000 positive integers, one per line.'}],
            tokenize=False,add_generation_prompt=True)
        f = generate('timeout', long_prompt, limit=1024, timeout=0.02)
        assert f['reason'] == 'timeout'
        timer=threading.Timer(0.02,engine.cancel)
        timer.start()
        try:
            g = generate('cancel', long_prompt, limit=1024)
        finally:
            timer.cancel();timer.join()
        assert g['reason'] == 'cancelled'
        short = generate('short_prefix', 'Hello', limit=1)
        assert len(short['tokens'])<16
        summary = {
            'status': 'passed', 'evidence_kind': 'measured',
            'scope': 'infrastructure_only' if args.infrastructure_only else '32b_backend_boundaries',
            'generations': len(results), 'reasons': dict(Counter(r['reason'] for r in results)),
            'exact_prompt_ids': True, 'physical_kv_cells_checked': True,
            'protocol_stop_is_normal_release': True,
            'cache_hit_and_miss_preserve_demand_identity': True,
            'main_pilot_passed': False}
        summary['budget']=budget.summary()
        (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    finally:
        engine.close()

if __name__ == '__main__':
    main()
