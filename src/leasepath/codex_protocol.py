"""Restricted, explicit Responses <-> Chat adapter; never executes tools.

Backend SSE is buffered until its terminal marker, finish reason, function IDs,
names and argument schemas have been validated. Responses deltas are then
emitted in order. This adds buffering latency and is not transparent streaming.
"""
import json
import time
from jsonschema import Draft202012Validator


class ProtocolError(ValueError):
    pass


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProtocolError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ProtocolError(value)))
    except (ValueError, TypeError) as exc:
        raise ProtocolError('invalid JSON') from exc


def text_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ProtocolError('unsupported content')
    parts = []
    for part in content:
        if part.get('type') not in {'input_text', 'output_text'} or not isinstance(part.get('text'), str):
            raise ProtocolError('only text content is supported')
        parts.append(part['text'])
    return '\n'.join(parts)


def to_chat(request, *, model, request_id, max_tokens):
    if not request.get('stream') or request.get('previous_response_id'):
        raise ProtocolError('requires streaming full explicit history; no previous_response_id')
    if request.get('background') or request.get('truncation', 'disabled') != 'disabled':
        raise ProtocolError('background/truncation unsupported')
    tools, names = [], set()
    for tool in request.get('tools', []):
        if tool.get('type') != 'function':
            raise ProtocolError('unsupported tool type: ' + str(tool.get('type')))
        name = tool.get('name')
        if not isinstance(name, str) or name in names:
            raise ProtocolError('missing or duplicate tool name')
        names.add(name)
        function = {k: tool[k] for k in ['name', 'description', 'parameters'] if k in tool}
        Draft202012Validator.check_schema(function.get('parameters', {}))
        tools.append({'type': 'function', 'function': function})
    messages = []
    if request.get('instructions'):
        messages.append({'role': 'system', 'content': request['instructions']})
    calls, answered = {}, set()
    inputs = request.get('input')
    if isinstance(inputs, str):
        inputs = [{'type': 'message', 'role': 'user', 'content': inputs}]
    if not isinstance(inputs, list):
        raise ProtocolError('missing explicit history')
    for item in inputs:
        kind = item.get('type', 'message')
        if kind == 'message':
            role = item.get('role')
            if role not in {'user', 'assistant', 'developer', 'system'}:
                raise ProtocolError('unsupported role')
            messages.append({'role': 'system' if role == 'developer' else role,
                             'content': text_content(item.get('content'))})
        elif kind == 'function_call':
            cid, name = item.get('call_id'), item.get('name')
            if not isinstance(cid, str) or cid in calls or name not in names:
                raise ProtocolError('duplicate/unknown function call in history')
            args = strict_json(item.get('arguments'))
            if not isinstance(args, dict):
                raise ProtocolError('function arguments must be object')
            calls[cid] = name
            call = {'id': cid, 'type': 'function', 'function': {'name': name, 'arguments': args}}
            # The pinned Qwen template expects argument objects and serializes
            # them itself. Keep IDs on the wire, not just textual snippets.
            if messages and messages[-1]['role'] == 'assistant' and 'tool_calls' in messages[-1]:
                messages[-1]['tool_calls'].append(call)
            else:
                messages.append({'role': 'assistant', 'content': '', 'tool_calls': [call]})
        elif kind == 'function_call_output':
            cid = item.get('call_id')
            if cid not in calls or cid in answered:
                raise ProtocolError('orphan or duplicate function output')
            answered.add(cid)
            messages.append({'role': 'tool', 'tool_call_id': cid,
                             'content': text_content(item.get('output'))})
        else:
            raise ProtocolError('unsupported input item: ' + str(kind))
    if set(calls) != answered:
        raise ProtocolError('history contains function call without result')
    # First system message is placed ahead of tools by the pinned template.
    if not messages or messages[0]['role'] != 'system':
        messages.insert(0, {'role': 'system', 'content': 'You are a coding assistant.'})
    return {'model': model, 'messages': messages, 'tools': tools,
            'tool_choice': request.get('tool_choice', 'auto'), 'parallel_tool_calls': False,
            'stream': True, 'stream_options': {'include_usage': True},
            'temperature': 0, 'top_p': 1, 'seed': 23, 'max_tokens': max_tokens,
            'request_id': request_id, 'skip_special_tokens': False}


class ChatStream:
    """Validate fragmented Chat SSE before releasing any executable output."""
    def __init__(self, tools):
        self.schemas = {t['function']['name']: t['function'].get('parameters', {}) for t in tools}
        self.content = ''
        self.calls = {}
        self.finish = None
        self.usage = None
        self.done = False

    def feed(self, data):
        if self.done:
            raise ProtocolError('data after DONE')
        if data == '[DONE]':
            self.done = True
            return
        chunk = strict_json(data)
        if 'error' in chunk:
            raise ProtocolError('backend error: ' + str(chunk['error']))
        if chunk.get('usage'):
            self.usage = chunk['usage']
        for choice in chunk.get('choices', []):
            if choice.get('index') != 0 or self.finish is not None:
                raise ProtocolError('unexpected choice or content after finish')
            delta = choice.get('delta', {})
            if delta.get('reasoning') or delta.get('reasoning_content'):
                raise ProtocolError('reasoning channel unsupported')
            if delta.get('content'):
                self.content += delta['content']
            for part in delta.get('tool_calls') or []:
                index = part.get('index')
                if type(index) is not int or index < 0:
                    raise ProtocolError('missing call index')
                call = self.calls.setdefault(index, {'id': '', 'name': '', 'arguments': ''})
                if part.get('id'):
                    if call['id']:
                        raise ProtocolError('call ID repeated or changed')
                    call['id'] = part['id']
                function = part.get('function', {})
                call['name'] += function.get('name') or ''
                call['arguments'] += function.get('arguments') or ''
            if choice.get('finish_reason'):
                self.finish = choice['finish_reason']

    def validate(self):
        if not self.done or self.finish not in {'stop', 'tool_calls'} or self.usage is None:
            raise ProtocolError('missing terminal/usage or truncated backend response')
        if sorted(self.calls) != list(range(len(self.calls))):
            raise ProtocolError('noncontiguous call indexes')
        ids = set()
        for call in self.calls.values():
            if not call['id'] or call['id'] in ids or call['name'] not in self.schemas:
                raise ProtocolError('missing/duplicate call ID or unknown tool')
            ids.add(call['id'])
            args = strict_json(call['arguments'])
            if not isinstance(args, dict):
                raise ProtocolError('nonobject function arguments')
            errors = list(Draft202012Validator(self.schemas[call['name']]).iter_errors(args))
            if errors:
                raise ProtocolError('tool arguments failed schema: ' + errors[0].message)
            # A login shell would reload host profiles and break isolation.
            if call['name'] == 'exec_command' and args.get('login') is not False:
                raise ProtocolError('isolated execution requires explicit login=false')
        if bool(self.calls) != (self.finish == 'tool_calls'):
            raise ProtocolError('finish reason disagrees with calls')
        return self


def response_events(stream, response_id, model):
    stream.validate()
    output = []
    created = int(time.time())
    response = {'id': response_id, 'object': 'response', 'created_at': created,
                'status': 'in_progress', 'model': model, 'output': [],
                'parallel_tool_calls': False, 'error': None}
    events = [('response.created', {'response': dict(response)}),
              ('response.in_progress', {'response': dict(response)})]
    if stream.content:
        item = {'id': 'msg_' + response_id, 'type': 'message', 'role': 'assistant',
                'status': 'in_progress', 'content': []}
        events.append(('response.output_item.added', {'output_index': 0, 'item': dict(item)}))
        part = {'type': 'output_text', 'text': '', 'annotations': []}
        events.append(('response.content_part.added', {'item_id': item['id'], 'output_index': 0,
                                                      'content_index': 0, 'part': dict(part)}))
        for start in range(0, len(stream.content), 64):
            events.append(('response.output_text.delta', {'item_id': item['id'], 'output_index': 0,
                'content_index': 0, 'delta': stream.content[start:start + 64]}))
        part['text'] = stream.content
        events.append(('response.output_text.done', {'item_id': item['id'], 'output_index': 0,
                       'content_index': 0, 'text': stream.content}))
        events.append(('response.content_part.done', {'item_id': item['id'], 'output_index': 0,
                       'content_index': 0, 'part': dict(part)}))
        item = {**item, 'status': 'completed', 'content': [part]}
        events.append(('response.output_item.done', {'output_index': 0, 'item': item}))
        output.append(item)
    for call in stream.calls.values():
        index = len(output)
        item = {'id': 'fc_' + call['id'], 'type': 'function_call', 'status': 'in_progress',
                'call_id': call['id'], 'name': call['name'], 'arguments': ''}
        events.append(('response.output_item.added', {'output_index': index, 'item': dict(item)}))
        for start in range(0, len(call['arguments']), 64):
            events.append(('response.function_call_arguments.delta', {'item_id': item['id'],
                'output_index': index, 'delta': call['arguments'][start:start + 64]}))
        events.append(('response.function_call_arguments.done', {'item_id': item['id'],
                       'output_index': index, 'arguments': call['arguments']}))
        item = {**item, 'arguments': call['arguments'], 'status': 'completed'}
        events.append(('response.output_item.done', {'output_index': index, 'item': item}))
        output.append(item)
    usage = stream.usage
    response = {**response, 'status': 'completed', 'output': output,
                'usage': {'input_tokens': usage['prompt_tokens'], 'output_tokens': usage['completion_tokens'],
                          'total_tokens': usage['total_tokens'],
                          'input_tokens_details': {'cached_tokens': usage.get('prompt_tokens_details', {}).get('cached_tokens', 0)},
                          'output_tokens_details': {'reasoning_tokens': 0}}}
    events.append(('response.completed', {'response': response}))
    return [{'type': kind, 'sequence_number': seq, **data} for seq, (kind, data) in enumerate(events)]


def strict_text_request(payload):
    """Declare a complete JSON envelope protocol, without native tool tags.

All schemas remain model-visible in the system message. Tool histories and
results receive explicit JSON envelopes; no result bytes are discarded.
"""
    schemas = payload['tools']
    instructions = (
        '\n\nLOCAL TOOL TRANSPORT PROTOCOL strict-json-envelope-v1. '
        'Respond with exactly ONE valid JSON object, without markdown, XML tags or any surrounding text. '
        'To run a tool, output {"tool_calls":[{"name":"exec_command","arguments":{"cmd":"your command","login":false}}]}. '
        'Use exactly one tool call per response. Do not output separate JSON objects. '
        'The client assigns a unique call ID and Codex executes the tool, then returns its actual result. '
        'Do not predict tool outputs. Wait for the returned tool_result before deciding the next action. '
        'When all requested work is verified, output {"final":"your concise result"}. '
        'The following are the exact available tool definitions; follow their parameter schemas. '
        'For exec_command always set login=false; use default bash.\n' +
        json.dumps(schemas, ensure_ascii=False))
    messages = []
    for message in payload['messages']:
        message = dict(message)
        if message.get('tool_calls'):
            if message.get('content'):
                raise ProtocolError('strict text history cannot combine text and calls')
            calls = [{'name': c['function']['name'], 'arguments': c['function']['arguments']}
                     for c in message.pop('tool_calls')]
            message['content'] = json.dumps({'tool_calls': calls}, ensure_ascii=False)
        if message['role'] == 'tool':
            message = {'role': 'user', 'content': json.dumps({'tool_result': {
                'call_id': message['tool_call_id'], 'output': message['content']}}, ensure_ascii=False)}
        messages.append(message)
    messages[0] = {**messages[0], 'content': messages[0]['content'] + instructions}
    result = {**payload, 'messages': messages, 'tool_choice': 'none'}
    result.pop('tools')  # vLLM rejects [], while omitted means no native tools
    return result


def parse_strict_text(stream, tools, request_id):
    stream.validate()
    if stream.calls:
        raise ProtocolError('native calls mixed into strict text response')
    data = strict_json(stream.content)
    if not isinstance(data, dict):
        raise ProtocolError('strict text response must be one object')
    if set(data) == {'final'} and isinstance(data['final'], str):
        stream.content = data['final']
        return stream
    if set(data) != {'tool_calls'} or not isinstance(data['tool_calls'], list) or len(data['tool_calls']) != 1:
        raise ProtocolError('expected exactly one declared tool call or final')
    call = data['tool_calls'][0]
    if not isinstance(call, dict) or set(call) != {'name', 'arguments'} or not isinstance(call['arguments'], dict):
        raise ProtocolError('invalid strict call envelope')
    stream.schemas = {t['function']['name']: t['function'].get('parameters', {}) for t in tools}
    stream.calls = {0: {'id': 'call_' + request_id + '_0', 'name': call['name'],
                       'arguments': json.dumps(call['arguments'], ensure_ascii=False)}}
    stream.content = ''
    stream.finish = 'tool_calls'
    return stream.validate()
