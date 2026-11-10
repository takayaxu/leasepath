from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from audit_pressure_rejection_v1 import check_rejection, check_raw_workers, check_rejection_native_output
from leasepath.restricted_data_tools import schemas_for
from jsonschema import Draft202012Validator, ValidationError


def rejected():
    task = {'id':'missing_median', 'operations':['impute','quantile']}
    args = {'dataset_id':'dmissing_median','operation':'impute','strategy':'median','column':2}
    schema = next(s['parameters'] for s in schemas_for(task['operations']) if s['name']=='compute')
    try:
        Draft202012Validator(schema).validate(args)
    except ValidationError as exc:
        result = {'error':'ValidationError','message':str(exc)[:800]}
    return task, {'call_id':'t1','name':'compute','arguments':args,'status':'error',
                  'started_ns':1,'ended_ns':3,'call_thread_cpu_ns':1,'result':result,'call_evidence_write_ns':1}


def test_exact_prelaunch_rejection_is_reproduced():
    task, call = rejected()
    assert check_rejection(call,task)['launched_worker'] is False


@pytest.mark.parametrize('key,value', [('child_pid',123),('input_check_ns',1),('preexec_controls',{}),('child_returncode',1)])
def test_worker_or_input_activity_cannot_hide_as_rejection(key,value):
    task, call = rejected(); call[key]=value
    with pytest.raises(ValueError,match='execution evidence'):
        check_rejection(call,task)


def test_valid_args_or_different_error_cannot_bypass_worker_audit():
    task, call = rejected(); del call['arguments']['column']
    with pytest.raises(ValueError,match='schema-valid'):
        check_rejection(call,task)
    task, call = rejected();call['result']['message']='invented rejection'
    with pytest.raises(ValueError,match='does not match'):
        check_rejection(call,task)
    task, call = rejected();call['status']='ok'
    with pytest.raises(ValueError,match='only an actual'):
        check_rejection(call,task)


def test_raw_trace_rejects_unmapped_or_missing_worker(tmp_path):
    one=tmp_path/'trace.123'
    one.write_text('1.0 execve("/launcher", ["/launcher", "/private/bin/python3.13", "-m", "leasepath.preexec_worker_v2"], 0x0) = 0\n'
                   '1.1 execve("/private/bin/python3.13", ["/private/bin/python3.13", "-m", "leasepath.preexec_worker_v2"], 0x0) = 0\n')
    assert check_raw_workers([one],[{'child_pid':123}])['raw_worker_execs']==1
    with pytest.raises(ValueError,match='differ'):
        check_raw_workers([one],[])
    with pytest.raises(ValueError,match='differ'):
        check_raw_workers([], [{'child_pid':123}])
    one.write_text(one.read_text()+one.read_text())
    with pytest.raises(ValueError,match='multiple'):
        check_raw_workers([one],[{'child_pid':123}])


def test_rejection_must_come_from_native_tokens():
    task, call = rejected()
    class Tokenizer:
        def decode(self, tokens):
            return ''.join(chr(t) for t in tokens)
    text=': ✿FUNCTION✿: compute\n✿ARGS✿: '+json.dumps(call['arguments'])
    requests=[{'owner':task['id'],'output_tokens':list(map(ord,text))}]
    assert check_rejection_native_output(call,task['id'],requests,Tokenizer())==1
    with pytest.raises(ValueError,match='absent'):
        check_rejection_native_output(call,'different-owner',requests,Tokenizer())
