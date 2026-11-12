import json
import os
from leasepath.continuous_projects_v1 import projects,initialize
from leasepath.continuous_tools_v2 import ProjectTools


def test_pid_and_resources_are_audit_only_and_semantic_output_stays_real(tmp_path):
    project=projects()[1];workspace=initialize(project,tmp_path/'project')
    tools=ProjectTools(workspace,tmp_path/'evidence',project,[min(os.sched_getaffinity(0))])
    first=tools.call('run_python',{'code':"print('stable result')"})
    second=tools.call('run_python',{'code':"print('stable result')"})
    assert first==second
    assert first['returncode']==0 and first['stdout'].strip()=='stable result'
    assert 'child_pid' not in first and 'lifetime' not in first
    evidence=[json.loads(x) for x in (tools.evidence/'process-resources.jsonl').read_text().splitlines()]
    assert len(evidence)==2 and evidence[0]['child_pid']!=evidence[1]['child_pid']
    assert all('user_s' in row['lifetime'] for row in evidence)
