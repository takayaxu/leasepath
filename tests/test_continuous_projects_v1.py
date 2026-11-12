import json
import sqlite3
from pathlib import Path
import pytest
from leasepath.continuous_projects_v1 import projects,initialize
from leasepath.continuous_tools_v1 import ProjectTools
from leasepath.continuous_verify_v1 import verify


def test_six_eight_turn_projects_are_development_only():
    suite=projects()
    assert len(suite)==len({p['source_group'] for p in suite})==6
    assert all(p['split']=='development' and len(p['turns'])==8 and len(p['tools'])>=3 for p in suite)
    assert all('write_file' in p['tools'] for p in suite)


@pytest.mark.parametrize('project',projects(),ids=lambda p:p['id'])
def test_missing_or_wrong_delivery_is_rejected(project,tmp_path):
    root=initialize(project,tmp_path/'project')
    assert not verify(project,0,root)['success']
    spec=project['turns'][0]
    if not spec['artifact'].endswith('.py'):
        (root/spec['artifact']).write_text(json.dumps({spec['key']:'wrong'}))
        assert not verify(project,0,root)['success']


def test_inventory_behavior_and_cumulative_regression(tmp_path):
    project=projects()[0];root=initialize(project,tmp_path/'project')
    (root/'inventory.py').write_text("def available(stock,sku):return stock.get(sku,0)\ndef reserve(stock,sku,qty):\n if type(qty)!=int or qty<0 or stock.get(sku,0)<qty:raise ValueError()\n return {**stock,sku:stock.get(sku,0)-qty}\n")
    assert verify(project,1,root)['success']
    (root/'inventory.py').write_text("def available(stock,sku):return 0\ndef reserve(stock,sku,qty):return {}\n")
    assert not verify(project,1,root)['success']


def test_billing_independent_numeric_check_and_prior_field(tmp_path):
    project=projects()[1];root=initialize(project,tmp_path/'project')
    (root/'report.json').write_text(json.dumps(dict(invoice_count=36,gross_cents=84582)))
    with sqlite3.connect(root/'billing.sqlite') as db:
        total=db.execute('SELECT SUM(amount_cents) FROM invoices').fetchone()[0]
    (root/'report.json').write_text(json.dumps(dict(invoice_count=36,gross_cents=total)))
    assert verify(project,1,root)['success']
    (root/'report.json').write_text(json.dumps(dict(gross_cents=total)))
    assert not verify(project,1,root)['success']


def test_tools_preserve_real_errors_and_workspace_boundary(tmp_path):
    import os
    project=projects()[1];root=initialize(project,tmp_path/'project')
    tools=ProjectTools(root,tmp_path/'evidence',project,[min(os.sched_getaffinity(0))])
    assert 'error' in tools.call('read_file',{'path':'../outside'})
    assert 'error' in tools.call('write_file',{'path':'invoices.csv','content':'fake'})
    result=tools.call('query_sql',{'sql':'SELECT COUNT(*) AS n FROM invoices'})
    assert result['rows']==[(36,)]
    assert 'error' in tools.call('query_sql',{'sql':'DELETE FROM invoices'})
    result=tools.call('run_python',{'code':"print(2+3)"})
    assert result['returncode']==0 and result['stdout'].strip()=='5'
    result=tools.call('run_python',{'code':"raise ValueError('real_error')"})
    assert result['returncode']!=0 and 'real_error' in result['stderr']
    assert len((tmp_path/'evidence/calls.jsonl').read_text().splitlines())==6
