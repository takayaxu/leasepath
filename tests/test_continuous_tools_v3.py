import json
import os
from leasepath.continuous_projects_v2 import projects,initialize
from leasepath.continuous_tools_v3 import ProjectTools


def fixture(tmp_path,index):
    project=projects()[index];workspace=initialize(project,tmp_path/'project')
    return ProjectTools(workspace,tmp_path/'evidence',project,[min(os.sched_getaffinity(0))])


def test_invalid_json_is_rejected_without_damaging_existing_file(tmp_path):
    tools=fixture(tmp_path,3);before=(tools.root/'integration.json').read_bytes()
    result=tools.call('write_file',dict(path='integration.json',content='{"customer_names":{1:"Aster"}}'))
    assert result['file_unchanged'] and 'error' in result
    assert (tools.root/'integration.json').read_bytes()==before
    assert tools.calls[-1]['status']=='rejected'
    result=tools.call('write_file',dict(path='integration.json',content=json.dumps({'customer_names':{'1':'Aster'}})))
    assert result['written']=='integration.json'


def test_public_checks_detect_real_crossfield_error_without_reference_answer(tmp_path):
    tools=fixture(tmp_path,1);tools.round=6
    (tools.root/'report.json').write_text(json.dumps(dict(paid_by_region_cents={'east':0},by_region_cents={'east':100},paid_cents=100,gross_cents=100)))
    result=tools.call('run_checks',{})
    assert result['returncode']!=0 and 'must sum' in result['stderr']
    assert 'expected' not in result
    (tools.root/'report.json').write_text(json.dumps(dict(paid_by_region_cents={'east':100},by_region_cents={'east':100},paid_cents=100,gross_cents=100)))
    assert tools.call('run_checks',{})['returncode']==0


def test_public_checks_respect_current_artifact_destination(tmp_path):
    tools=fixture(tmp_path,2);tools.round=3
    (tools.root/'config.json').write_text(json.dumps({'slow_request_ids':[]}))
    result=tools.call('run_checks',{})
    assert result['returncode']!=0
    assert 'target artifact' in result['stderr']
