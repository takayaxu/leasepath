"""Prove prelaunch schema rejections without excusing actual worker failures."""
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator, ValidationError
from leasepath.restricted_data_tools import schemas_for


def check_rejection(call, task):
    if call.get('name') != 'compute' or call.get('status') != 'error':
        raise ValueError('only an actual schema-validation error can omit worker evidence')
    allowed = {'call_id','name','arguments','started_ns','status','ended_ns',
               'call_thread_cpu_ns','result','call_evidence_write_ns'}
    if set(call) - allowed:
        raise ValueError('prelaunch rejection contains input/worker/unknown execution evidence')
    if not 0 <= call['call_thread_cpu_ns'] or not call['started_ns'] <= call['ended_ns']:
        raise ValueError('invalid rejection timing')
    json.dumps(call['arguments'], allow_nan=False)
    schema = next(x['parameters'] for x in schemas_for(task['operations']) if x['name'] == 'compute')
    try:
        Draft202012Validator(schema).validate(call['arguments'])
    except ValidationError as exc:
        expected = {'error': 'ValidationError', 'message': str(exc)[:800]}
        if call['result'] != expected:
            raise ValueError('recorded rejection does not match the frozen schema')
    else:
        raise ValueError('schema-valid call cannot be accepted as a prelaunch rejection')
    return {'task': task['id'], 'call_id': call['call_id'], 'arguments': call['arguments'],
            'kind': 'reproduced_prelaunch_schema_rejection', 'launched_worker': False}


def check_raw_workers(traces, calls):
    """Every observed numerical Python exec must correspond to exactly one call.

    This excludes an unrecorded failed worker hiding behind a schema-rejection
    label. The frozen worker implementation rejects the schema before opening
    an input or constructing UsagePopen. Full trace covers all child execs.
    """
    expected = {call['child_pid']: call for call in calls}
    if len(expected) != len(calls):
        raise ValueError('worker PID reused or duplicate call')
    observed = {}
    for file in traces:
        pid = int(file.name.rsplit('.', 1)[1])
        found = []
        with file.open(errors='replace') as stream:
            for line in stream:
                match = re.search(r'\bexecve\("([^"\n]+)"', line)
                if match and match[1].endswith('/bin/python3.13') and '"leasepath.preexec_worker_v2"' in line and re.search(r'= 0\s*$', line):
                    found.append(line)
        if found:
            if len(found) != 1 or pid in observed:
                raise ValueError('multiple numerical execs per PID')
            observed[pid] = str(file)
    if set(observed) != set(expected):
        raise ValueError('raw numerical exec PIDs differ from successful worker records')
    return {'raw_worker_execs': len(observed), 'matched_worker_pids': sorted(observed),
            'unmapped_numerical_execs': 0}


def check_rejection_native_output(call, owner, requests, tokenizer):
    matched = 0
    for request in requests:
        if request['owner'] != owner:
            continue
        # Read actual native output token IDs, not an auxiliary transcript.
        text = tokenizer.decode(request['output_tokens'])
        name = re.search(r'✿FUNCTION✿\s*:\s*([^\s]+)', text)
        marker = re.search(r'✿ARGS✿\s*:\s*', text)
        if not name or not marker or name[1] != call['name']:
            continue
        try:
            arguments, _ = json.JSONDecoder().raw_decode(text[marker.end():])
        except ValueError:
            continue
        if arguments == call['arguments']:
            matched += 1
    if not matched:
        raise ValueError('rejection arguments absent from actual native output tokens')
    return matched
