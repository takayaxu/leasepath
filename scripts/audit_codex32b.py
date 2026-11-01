"""Independent audit of saved Codex/Chat boundaries and actual shell calls."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from leasepath.real_observer import validate_stream


def audit(service, sessions):
    raw = [json.loads(line) for p in (service / 'raw').glob('*.jsonl') for line in p.read_text().splitlines()]
    enqueue = {r['data']['request_id']: r for r in raw if r['kind'] == 'request_enqueue'}
    accounted, requests, executions, opaque = set(), [], [], []
    all_calls, returned, backend_returned = {}, {}, set()
    for session in sessions:
        validate_stream(session / 'gateway.jsonl')
        rows = [json.loads(line) for line in (session / 'gateway.jsonl').read_text().splitlines()]
        ready = {r['data']['request_id']: r for r in rows if r['kind'] == 'request_ready'}
        for target in sorted(session.glob('request-*')):
            rid = target.name.removeprefix('request-')
            matches = [bid for bid in enqueue if bid.startswith('chatcmpl-' + rid + '-')]
            original = json.loads((target / 'original.json').read_text())
            for item in original.get('input', []):
                if item.get('type') == 'function_call_output':
                    returned.setdefault(item['call_id'], []).append({'request_id': rid, 'output': item['output']})
            if not matches:
                assert (target / 'error.json').exists(), ('unexplained missing backend', rid)
                opaque.append({'request_id': rid, 'status': 'frontend_rejected_no_backend',
                               'error': str(target / 'error.json')})
                continue
            assert len(matches) == 1 and rid in ready
            bid = matches[0]
            assert bid not in accounted
            accounted.add(bid)
            ids = json.loads((target / 'tokenize.json').read_text())['tokens']
            assert ids == enqueue[bid]['data']['prompt_token_ids'], ('actual input mismatch', rid)
            assert ready[rid]['ts_ns'] <= enqueue[bid]['ts_ns']
            forwarded = json.loads((target / 'forwarded.json').read_text())
            backend_returned.update(item['call_id'] for item in original.get('input', [])
                                    if item.get('type') == 'function_call_output')
            strict = 'tools' not in forwarded
            if strict:
                for item in original.get('input', []):
                    if item.get('type') == 'function_call_output':
                        candidates = []
                        for message in forwarded['messages']:
                            try:
                                envelope = json.loads(message['content'])
                            except (ValueError, TypeError):
                                continue
                            if isinstance(envelope, dict) and envelope.get('tool_result', {}).get('call_id') == item['call_id']:
                                candidates.append(envelope['tool_result']['output'])
                        assert candidates == [item['output']], ('tool result lost/rewritten', rid, item['call_id'])
            normalized = target / 'normalized.json'
            if normalized.exists():
                events = json.loads(normalized.read_text())
                assert [e['sequence_number'] for e in events] == list(range(len(events)))
                completed = events[-1]
                assert completed['type'] == 'response.completed'
                usage = completed['response']['usage']
                assert usage['input_tokens'] == len(ids)
                calls = [x for x in completed['response']['output'] if x['type'] == 'function_call']
                if strict:
                    chunks = [json.loads(line[6:]) for line in (target / 'backend.sse').read_text().splitlines()
                              if line.startswith('data: {')]
                    content = ''.join(c.get('delta', {}).get('content') or '' for chunk in chunks for c in chunk.get('choices', []))
                    declared = json.loads(content)
                    if calls:
                        assert len(calls) == 1
                        assert declared == {'tool_calls': [{'name': calls[0]['name'], 'arguments': json.loads(calls[0]['arguments'])}]}
                    else:
                        assert declared == {'final': completed['response']['output'][0]['content'][0]['text']}
                for call in calls:
                    assert call['call_id'] not in all_calls
                    all_calls[call['call_id']] = call
            requests.append({'owner': session.name, 'request_id': rid, 'backend_request_id': bid,
                             'prompt_tokens': len(ids), 'actual_prompt_ids_exact': True,
                             'schema_transport': 'explicit strict JSON' if strict else 'native Chat tools'})
        for path in sorted((session / 'tool-execution').glob('*.jsonl')):
            validate_stream(path)
            execution = [json.loads(line) for line in path.read_text().splitlines()]
            start = next(r for r in execution if r['kind'] == 'tool_start')
            end = next(r for r in execution if r['kind'] in {'tool_end', 'tool_cancelled'})
            cid = start['data']['tool_call_id']
            assert cid in all_calls and all_calls[cid]['name'] == 'exec_command'
            assert json.loads(all_calls[cid]['arguments'])['cmd'] == start['data']['command']
            assert start['ts_ns'] <= end['ts_ns']
            assert cid in returned, ('tool result not returned to model', cid)
            executions.append({'owner': session.name, 'tool_call_id': cid,
                               'command': start['data']['command'], 'start_ns': start['ts_ns'],
                               'end_ns': end['ts_ns'], 'returncode': end['data'].get('returncode'),
                               'result_return_request_ids': [r['request_id'] for r in returned[cid]]})
    missing = set(enqueue) - accounted
    assert not missing, ('unaccounted backend requests', sorted(missing))
    expected_shell = {cid for cid, call in all_calls.items() if call['name'] == 'exec_command'}
    actual_shell = {r['tool_call_id'] for r in executions}
    assert actual_shell == expected_shell, ('missing actual shell execution', expected_shell - actual_shell)
    return {'status': 'passed', 'backend_requests': len(enqueue), 'actual_prompt_ids_exact': len(requests),
            'unique_model_tool_calls': len(all_calls), 'actual_shell_executions': len(executions),
            'unique_results_attached_by_CLI': len(returned),
            'unique_results_forwarded_into_backend': len(backend_returned),
            'results_not_forwarded_after_budget_rejection': sorted(set(returned) - backend_returned),
            'frontend_rejected': opaque,
            'requests': requests, 'executions': executions,
            'scope': 'new explicit full-history serial Codex sessions; not a universal Codex protocol acceptance'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', type=Path, required=True)
    parser.add_argument('--sessions', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.service, args.sessions)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in {'requests', 'executions'}}))


if __name__ == '__main__':
    main()
