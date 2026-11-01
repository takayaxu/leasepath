import json
import pytest

from leasepath.codex_protocol import ChatStream, ProtocolError, response_events, strict_json, to_chat


TOOLS = [{'type': 'function', 'function': {'name': 'exec_command', 'parameters': {
    'type': 'object', 'properties': {'cmd': {'type': 'string'}, 'login': {'type': 'boolean'}},
    'required': ['cmd'], 'additionalProperties': False}}}]


def request(items):
    return {'stream': True, 'tools': [{'type': 'function', **TOOLS[0]['function']}], 'input': items}


def test_tool_history_preserves_result_and_developer_instructions():
    inputs = [{'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': 'keep me'}]},
              {'type': 'function_call', 'call_id': 'c1', 'name': 'exec_command',
               'arguments': '{"cmd":"false","login":false}'},
              {'type': 'function_call_output', 'call_id': 'c1', 'output': 'exit code 1'}]
    result = to_chat(request(inputs), model='test', request_id='r', max_tokens=20)
    assert result['messages'][0] == {'role': 'system', 'content': 'keep me'}
    assert result['messages'][1]['tool_calls'][0]['id'] == 'c1'
    assert result['messages'][2] == {'role': 'tool', 'tool_call_id': 'c1', 'content': 'exit code 1'}
    assert result['tools'] == TOOLS


@pytest.mark.parametrize('items', [
    [{'type': 'function_call_output', 'call_id': 'orphan', 'output': 'success'}],
    [{'type': 'reasoning', 'summary': []}],
    [{'type': 'message', 'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'x'}]}],
    [{'type': 'function_call', 'call_id': 'c', 'name': 'exec_command', 'arguments': '{}'}],
])
def test_unsupported_or_missing_history_fails(items):
    with pytest.raises(ProtocolError):
        to_chat(request(items), model='test', request_id='r', max_tokens=20)


def make_stream(*, finish='tool_calls', done=True, name='exec_command', arguments=None, duplicate=False):
    stream = ChatStream(TOOLS)
    args = arguments if arguments is not None else '{"cmd":"printf test","login":false}'
    for delta in [
        {'tool_calls': [{'index': 0, 'id': 'c1', 'function': {'name': name, 'arguments': args[:9]}}]},
        {'tool_calls': [{'index': 0, 'function': {'arguments': args[9:]}}]},
    ]:
        stream.feed(json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}))
    if duplicate:
        stream.feed(json.dumps({'choices': [{'index': 0, 'delta': {'tool_calls': [
            {'index': 1, 'id': 'c1', 'function': {'name': name, 'arguments': args}}]}}]}))
    stream.feed(json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}))
    stream.feed(json.dumps({'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}))
    if done:
        stream.feed('[DONE]')
    return stream


def test_fragmented_arguments_roundtrip_and_completed_events():
    stream = make_stream()
    events = response_events(stream, 'r1', 'm1')
    assert [x['sequence_number'] for x in events] == list(range(len(events)))
    added = next(e['item'] for e in events if e['type'] == 'response.output_item.added')
    completed = events[-1]['response']['output'][0]
    deltas = ''.join(e['delta'] for e in events if e['type'] == 'response.function_call_arguments.delta')
    assert added['call_id'] == completed['call_id'] == 'c1'
    assert completed['arguments'] == deltas == stream.calls[0]['arguments']
    assert events[-1]['type'] == 'response.completed'


@pytest.mark.parametrize('kwargs', [
    {'finish': 'length'}, {'done': False}, {'name': 'unknown'}, {'duplicate': True},
    {'arguments': '{"cmd":"x","login":true}'}, {'arguments': '{"cmd":12,"login":false}'},
    {'arguments': '{"cmd":"x","login":false'},
])
def test_no_executable_event_for_invalid_or_incomplete_calls(kwargs):
    with pytest.raises(ProtocolError):
        response_events(make_stream(**kwargs), 'r', 'm')


def test_duplicate_json_keys_and_terminal_followups_rejected():
    with pytest.raises(ProtocolError):
        strict_json('{"cmd":"a","cmd":"b"}')
    with pytest.raises(ProtocolError):
        make_stream().feed('[DONE]')


def test_nonfunction_tools_are_rejected_not_silently_removed():
    data = request([])
    data['tools'].append({'type': 'custom', 'name': 'apply_patch'})
    with pytest.raises(ProtocolError):
        to_chat(data, model='m', request_id='r', max_tokens=20)


def test_backend_error_does_not_become_completed_response():
    stream = ChatStream(TOOLS)
    with pytest.raises(ProtocolError, match='backend error'):
        stream.feed('{"error":{"message":"cancelled"}}')
    with pytest.raises(ProtocolError):
        response_events(stream, 'r', 'm')


@pytest.mark.parametrize('content,valid', [
    ('{"tool_calls":[{"name":"exec_command","arguments":{"cmd":"false","login":false}}]}', True),
    ('{"final":"verified"}', True),
    ('Here is JSON: {"final":"verified"}', False),
    ('{"final":"one"}\n{"final":"two"}', False),
    ('{"tool_calls":[{"name":"unknown","arguments":{}}]}', False),
    ('{"tool_calls":[{"name":"exec_command","arguments":{"cmd":"x"}}]}', False),
])
def test_strict_text_accepts_only_declared_whole_response(content, valid):
    from leasepath.codex_protocol import parse_strict_text
    stream = ChatStream([])
    stream.content = content
    stream.finish, stream.done = 'stop', True
    stream.usage = {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}
    if valid:
        result = parse_strict_text(stream, TOOLS, 'req')
        events = response_events(result, 'r', 'm')
        assert events[-1]['type'] == 'response.completed'
    else:
        with pytest.raises(ProtocolError):
            parse_strict_text(stream, TOOLS, 'req')


def test_strict_transport_moves_all_schemas_and_results_without_empty_native_tools():
    from leasepath.codex_protocol import strict_text_request
    data = to_chat(request([
        {'type': 'message', 'role': 'developer', 'content': 'instruction'},
        {'type': 'function_call', 'call_id': 'c1', 'name': 'exec_command',
         'arguments': '{"cmd":"false","login":false}'},
        {'type': 'function_call_output', 'call_id': 'c1', 'output': 'literal error\nline 2'}
    ]), model='m', request_id='r', max_tokens=20)
    converted = strict_text_request(data)
    assert 'tools' not in converted  # backend distinguishes absent from []
    assert converted['tool_choice'] == 'none'
    assert json.dumps(TOOLS, ensure_ascii=False) in converted['messages'][0]['content']
    assert json.loads(converted['messages'][-1]['content']) == {
        'tool_result': {'call_id': 'c1', 'output': 'literal error\nline 2'}}


def test_shell_observation_preserves_failed_exit_and_call_identity(tmp_path):
    import hashlib
    import os
    from pathlib import Path
    import subprocess
    import sys
    from leasepath.real_observer import validate_stream

    root = Path(__file__).resolve().parents[1]
    (tmp_path / 'tool-intents').mkdir()
    (tmp_path / 'tool-execution').mkdir()
    command = 'printf actual-output; exit 7'
    intent = {'id': 'test-call', 'name': 'exec_command', 'owner': 'test-owner', 'run_id': 'test',
              'arguments': json.dumps({'cmd': command, 'login': False})}
    (tmp_path / 'tool-intents/one.json').write_text(json.dumps(intent))
    bootstrap = tmp_path / 'bootstrap.sh'
    bootstrap.write_text('exec ' + sys.executable + ' ' + str(root / 'scripts/codex32b_shell.py') +
                         ' -c "$BASH_EXECUTION_STRING"\n')
    run = subprocess.run(['/bin/bash', '-c', command], cwd=tmp_path, capture_output=True,
                         text=True, timeout=10, env={**os.environ, 'BASH_ENV': str(bootstrap),
                         'LEASEPATH_CODEX_EXECUTION_ROOT': str(tmp_path)})
    assert (run.returncode, run.stdout) == (7, 'actual-output')
    log = next((tmp_path / 'tool-execution').glob('*.jsonl'))
    validate_stream(log)
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    end = next(r for r in rows if r['kind'] == 'tool_end')
    assert end['data']['returncode'] == 7
    assert end['data']['tool_call_id'] == 'test-call'
