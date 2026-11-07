"""Synthetic audit mutations; never counted as real backend acceptance."""
import hashlib
import json
import time
from pathlib import Path
import pytest
from leasepath.real_observer import Observer
from leasepath.qwen_data_audit import audit

class TestTokenizer:
    def encode(self,text,**kwargs):
        return list(range(32)) + ([32] if text.endswith('NEXT') else [])

def build(root,*,miss=False,retire=True,abort=False,missing_result=False,wrong_cell=False):
    (root/'raw').mkdir(parents=True)
    a=Observer(root/'raw/controller.jsonl',run_id='synthetic-audit-fixture',source='controller')
    b=Observer(root/'raw/backend.jsonl',run_id='synthetic-audit-fixture',source='engine')
    (root/'identity.json').write_text(json.dumps({
        'run_id':'synthetic-audit-fixture','compatibility_id':'test-compatible',
        'expected_stream_ids':[a.identity['stream_id'],b.identity['stream_id']]}))
    owner='owner'
    a.emit('workflow_submit',owner=owner)
    a.emit('context_open',owner=owner,context_version='v1',compatibility_id='test-compatible')
    for index in range(2):
        prompt='PROMPT' if index==0 else ('RESULT NEXT' if not missing_result else 'NEXT')
        tokens=TestTokenizer().encode(prompt)
        rid=str(index)
        ph=hashlib.sha256(prompt.encode()).hexdigest()
        a.emit('request_input',owner=owner,context_version='v1',request_id=rid,token_ids=tokens,prompt=prompt,prompt_sha256=ph)
        n=time.monotonic_ns()
        release=[[x,x] for x in tokens]
        if wrong_cell and index==0:release[0][1]=9999
        reason='cancelled' if abort and index==0 else 'eog'
        b.emit('engine_request',owner=owner,context_version='v1',request_id=rid,backend_id=index,
               tokens=tokens,prompt_sha256=ph,enqueue_ns=n,dispatch_ns=n,lookup_ns=n,
               release_ns=n+1,hit=0 if miss or index==0 else 32,
               prefill=len(tokens)-(0 if miss or index==0 else 32),
               lookup_cells=[] if index==0 or miss else [[i,i] for i in range(32)],
               release_cells=[] if reason=='cancelled' else release,reason=reason,
               device_kv_bytes={'CUDA0':1000},output_tokens=[1],text='x')
        if index==0:
            a.emit('tool_start',owner=owner,tool_id='t',tool_name='compute')
            a.emit('tool_end',owner=owner,tool_id='t',tool_name='compute',result_json='RESULT')
    if retire:a.emit('context_retire',owner=owner,context_version='v1',reason='explicit_discard')
    a.emit('observation_end',complete=True)
    b.finish();a.finish()

@pytest.fixture(autouse=True)
def tokenizer(monkeypatch):
    monkeypatch.setattr('leasepath.qwen_data_audit.client_tokenizer',lambda _:TestTokenizer())

@pytest.mark.parametrize('miss',[False,True])
def test_hit_miss_are_both_demand(tmp_path,miss):
    build(tmp_path,miss=miss)
    result=audit(tmp_path,export=True)
    assert result['labels']=={'legal_demand':1,'retire':1}
    assert result['unmapped']==result['unexplained']==0
    assert result['tool_results_consumed']==1

def test_no_retire_is_censoring(tmp_path):
    build(tmp_path,retire=False)
    assert audit(tmp_path)['labels']=={'legal_demand':1,'right_censored':1}

def test_abort_does_not_create_idle(tmp_path):
    build(tmp_path,abort=True)
    assert audit(tmp_path)['labels']=={'retire':1}

@pytest.mark.parametrize('kwargs,match',[
    ({'missing_result':True},'not consumed'),({'wrong_cell':True},'exact physical input prefix')])
def test_rejects_semantic_corruption(tmp_path,kwargs,match):
    build(tmp_path,**kwargs)
    with pytest.raises(ValueError,match=match):audit(tmp_path)

def test_rejects_truncated_producer(tmp_path):
    build(tmp_path)
    path=tmp_path/'raw/backend.jsonl'
    data=path.read_text().splitlines()
    path.write_text('\n'.join(data[:-1])+'\n')
    with pytest.raises(ValueError,match='no complete boundary'):audit(tmp_path)

def test_rejects_unmapped_backend(tmp_path):
    build(tmp_path)
    # A new sealed producer has an extra native request absent from the client.
    old=[json.loads(x) for x in (tmp_path/'raw/backend.jsonl').read_text().splitlines()]
    (tmp_path/'raw/backend.jsonl').unlink()
    b=Observer(tmp_path/'raw/backend.jsonl',run_id='synthetic-audit-fixture',source='engine')
    for row in old[1:-1]:b.emit(row['kind'],**row['data'])
    b.emit('engine_request',**old[1]['data'])
    b.finish()
    identity=json.loads((tmp_path/'identity.json').read_text())
    identity['expected_stream_ids'][1]=b.identity['stream_id']
    (tmp_path/'identity.json').write_text(json.dumps(identity))
    with pytest.raises(ValueError,match='unmapped'):audit(tmp_path)
