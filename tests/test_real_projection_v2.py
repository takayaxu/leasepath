from copy import deepcopy

import pytest

from leasepath.real_projection_v2 import project


def row(kind, t, **data):
    return {'kind': kind, 'ts_ns': t, 'seq': t, 'stream_id': 'test',
            'source': 'controlled_v2', 'data': data}


def ready(rid, t, *, version='v1', owner='a', retry=None, logical=None, purpose='demand'):
    return row('request_ready', t, request_id=rid, owner=owner, context_version=version,
               attempt_id=rid, logical_request_id=logical or rid, retry_of=retry,
               purpose=purpose, wire_api='chat')


def backend(rid, t, *, abort=False, repeats=1, preempt=False):
    bid = 'chatcmpl-' + rid + '-engine'
    result = [row('request_enqueue', t, request_id=bid, prompt_token_ids=[1,2,3],
                  compatibility_id='config', cache_salt=None)]
    for offset in range(repeats):
        result.append(row('cache_lookup', t+1+offset, request_id=bid, lookup_started_ns=t+1+offset,
                          cached_tokens=0, blocks=[[]]))
    if preempt:
        result.append(row('preemption_release', t+4, request_id=bid))
    result.append(row('request_release', t+5, request_id=bid, prefix_token_ids=[1,2], block_size=2,
                      status='FINISHED_ABORTED' if abort else 'FINISHED_STOPPED',
                      blocks_before_release=[[{'slot':1,'generation':1,'hash':'h','ref_count':1,'is_null':False}]]))
    return result


def test_multiple_lookup_preemption_abort_retry_close_only_first_legal_demand():
    clients = [row('context_open',0,owner='a',context_version='v1'),ready('one',1),
               ready('two',10),ready('retry',20,retry='two',logical='two'),
               row('observation_end',40,owner='a')]
    raw = backend('one',2) + backend('two',11,abort=True,repeats=3,preempt=True) + backend('retry',21,repeats=3)
    result=project(raw,clients,'config')
    assert [r['outcome'] for r in result['labels']]==['U','C']
    assert result['labels'][0]['end_ns']==12
    assert result['summary']['all_lookup_events_retained']==7
    assert len(result['protection'])==3
    assert len(result['protection'][1]['preemptions'])==1
    assert all(r['status']=='projected' for r in result['coverage'])


def test_version_retire_is_scoped_and_auxiliary_actual_lookup_is_still_demand():
    clients=[row('context_open',0,owner='a',context_version='v1'),ready('one',1),
             row('context_open',8,owner='a',context_version='v2'),ready('two',10,version='v2'),
             row('context_retire',18,owner='a',context_version='v1'),
             ready('aux',20,version='v2',purpose='auxiliary_no_demand'),row('observation_end',40,owner='a')]
    result=project(backend('one',2)+backend('two',11)+backend('aux',21),clients,'config')
    assert [r['outcome'] for r in result['labels']]==['D','U','C']
    assert result['labels'][0]['context_version']=='v1'


def test_incomplete_scope_is_reported_instead_of_guessed():
    raw=backend('unmapped',2)
    r=project(raw,[],'config')
    assert r['coverage'][0]['status']=='unmapped'
    assert not r['labels']
    clients=[row('context_open',0,owner='a',context_version='v1'),ready('unmapped',1)]
    r=project(raw,clients,'config')
    assert r['summary']['unresolved_relations']==1
    assert not r['labels']


def test_retired_restore_and_cross_owner_retry_rejected():
    clients=[row('context_open',0,owner='a',context_version='v1'),ready('one',1),
             row('context_retire',8,owner='a',context_version='v1'),
             row('context_restore',9,owner='a',context_version='v1')]
    with pytest.raises(ValueError,match='restored'):
        project(backend('one',2),clients,'config')
    clients=[ready('one',1),ready('two',10,owner='b',retry='one',logical='one')]
    with pytest.raises(ValueError,match='retry chain'):
        project([],clients,'config')


def test_duplicate_lookup_does_not_create_future_evidence_for_own_release():
    clients=[row('context_open',0,owner='a',context_version='v1'),ready('one',1),
             row('observation_end',20,owner='a')]
    raw=backend('one',2,repeats=3)
    result=project(raw,clients,'config')
    assert [r['outcome'] for r in result['labels']]==['C']
    bad=deepcopy(raw);bad[1]['data']['lookup_started_ns']=1
    with pytest.raises(ValueError,match='timestamp|pending'):
        project(bad,clients,'config')


def test_unmapped_compatible_lookup_cannot_manufacture_censoring():
    clients=[row('context_open',0,owner='a',context_version='v1'),ready('one',1),
             row('observation_end',30,owner='a')]
    result=project(backend('one',2)+backend('unknown-owner',12),clients,'config')
    assert not result['labels']
    assert result['unresolved'][0]['reason']=='unprojected_compatible_lookup_before_endpoint'


def test_explicit_retire_with_pending_abort_does_not_resurrect_idle():
    clients=[row('context_open',0,owner='a',context_version='v1'),ready('one',1),
             ready('two',10),row('context_retire',13,owner='a',context_version='v1'),
             row('observation_end',30,owner='a')]
    result=project(backend('one',2)+backend('two',11,abort=True),clients,'config')
    assert [r['outcome'] for r in result['labels']]==['U']
    assert len(result['protection'])==2
