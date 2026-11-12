"""Independent current/past deliverable checks; no oracle in tool feedback."""
import csv
import json
import math
from pathlib import Path
import subprocess
import sys


INVENTORY_PROBES=[
    "assert m.available({'a':3},'a')==3;assert m.available({},'absent')==0",
    "s={'a':3};assert m.reserve(s,'a',2)=={'a':1};assert s=={'a':3};expect_error(lambda:m.reserve(s,'a',4));expect_error(lambda:m.reserve(s,'a',-1));expect_error(lambda:m.reserve(s,'a',1.5))",
    "s={'a':3};assert m.release(s,'b',2)=={'a':3,'b':2};assert s=={'a':3};expect_error(lambda:m.release(s,'a',-2))",
    "assert m.low_stock({'z':0,'b':2,'a':1},2)==['a','z'];assert m.low_stock({},2)==[]",
    "assert m.stock_value({'a':3,'b':2,'c':1},{'a':1.5,'b':4})==12.5",
    "s={'a':1};d=[{'sku':'a','qty':2},{'sku':'b','qty':3}];assert m.merge_deliveries(s,d)=={'a':3,'b':3};assert s=={'a':1};expect_error(lambda:m.merge_deliveries(s,[{'sku':'a','qty':-1}]))",
    "s={'a':5,'b':2};assert m.fulfill_order(s,{'a':2,'b':1})=={'a':3,'b':1};expect_error(lambda:m.fulfill_order(s,{'a':2,'b':3}));assert s=={'a':5,'b':2};expect_error(lambda:m.fulfill_order(s,{'a':-1}))",
    "assert m.summarize({'a':3,'b':0},{'a':2},1)=={'units':3,'value':6,'low':['b']}"
]


def equal(actual,expected):
    if isinstance(expected,float):return type(actual) in (int,float) and math.isfinite(actual) and math.isclose(actual,expected,rel_tol=1e-7,abs_tol=1e-7)
    if isinstance(expected,dict):return isinstance(actual,dict) and set(actual)==set(expected) and all(equal(actual[k],v) for k,v in expected.items())
    if isinstance(expected,list):return isinstance(actual,list) and len(actual)==len(expected) and all(equal(a,b) for a,b in zip(actual,expected))
    return type(actual)==type(expected) and actual==expected


def expected_fields(project,root):
    name=project['id']
    if name=='continuous_billing':
        with (root/'invoices.csv').open() as stream:rows=list(csv.DictReader(stream))
        total=lambda status=None:sum(int(r['amount_cents']) for r in rows if status is None or r['status']==status)
        regions=sorted({r['region'] for r in rows})
        return {'report.json':dict(invoice_count=len(rows),gross_cents=total(),paid_cents=total('paid'),open_cents=total('open'),
            by_region_cents={g:sum(int(r['amount_cents']) for r in rows if r['region']==g) for g in regions},
            overdue_ids=sorted(int(r['id']) for r in rows if r['status']=='open' and int(r['due_day'])<20),
            paid_by_region_cents={g:sum(int(r['amount_cents']) for r in rows if r['region']==g and r['status']=='paid') for g in regions},
            reconciliation=dict(accounted_cents=total('paid')+total('open')+total('void'),balanced=True))}
    if name=='continuous_logs':
        rows=[json.loads(line) for line in (root/'service.log').read_text().splitlines()];errors=[r for r in rows if r['level']=='ERROR'];maximum=max(r['latency_ms'] for r in rows)
        return {'config.json':dict(port=8087,timeout_ms=math.ceil(maximum/100)*100,retry_limit=3,log_level='WARNING'),
                'incident.json':dict(error_counts={k:sum(r['code']==k for r in errors) for k in {r['code'] for r in errors}},slow_request_ids=sorted(r['request_id'] for r in rows if r['latency_ms']>500),first_error_request=errors[0]['request_id'],summary=dict(requests=len(rows),errors=len(errors),max_latency_ms=maximum))}
    if name=='continuous_api':
        # Separately derived constants; does not import the service fixture.
        return {'integration.json':dict(healthy=True,product_ids=[11,12,14],active_product_ids=[11,14],catalog_value_cents=6750,order_count=3,paid_order_ids=[101,103],customer_names={'1':'Aster','2':'Birch'},paid_revenue_cents=1575)}
    if name=='continuous_experiment':
        with (root/'measurements.csv').open() as stream:rows=list(csv.DictReader(stream))
        xs=[float(r['x']) for r in rows];ys=[float(r['y']) for r in rows];n=len(rows);mx=math.fsum(xs)/n;my=math.fsum(ys)/n
        slope=math.fsum((x-mx)*(y-my) for x,y in zip(xs,ys))/math.fsum((x-mx)**2 for x in xs);intercept=my-slope*mx
        sensors=sorted({r['sensor'] for r in rows})
        return {'metrics.json':dict(count=n,mean_y=my,variance_y=math.fsum((y-my)**2 for y in ys)/n,
            mean_by_sensor={s:math.fsum(float(r['y']) for r in rows if r['sensor']==s)/sum(r['sensor']==s for r in rows) for s in sensors},
            slope=slope,intercept=intercept,mse=math.fsum((y-slope*x-intercept)**2 for x,y in zip(xs,ys))/n,valid_count=sum(r['quality']=='good' for r in rows))}
    if name=='continuous_documents':
        return {'handoff.json':dict(release='warehouse-2026.11',owners={'api':'Chen','worker':'Noor','reports':'Mira'},minimum_python='3.13',required_checks=['integration','lint','schema','unit'],open_issue_ids=['I02','I03'],blocking_issue_ids=['I03'],ready=False,audit=dict(issue_count=4,open_count=2,policy_date='2026-11-01'))}
    raise ValueError('unknown project')


def verify(project,round_index,workspace):
    root=Path(workspace)
    try:
        if project['id']=='continuous_inventory':
            header="import importlib.util\nspec=importlib.util.spec_from_file_location('tested','inventory.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)\ndef expect_error(fn):\n try:fn()\n except ValueError:return\n raise AssertionError('ValueError required')\n"
            result=subprocess.run([sys.executable,'-c',header+'\n'.join(INVENTORY_PROBES[:round_index+1])],cwd=root,capture_output=True,text=True,timeout=30)
            if result.returncode:return dict(success=False,error='independent behavior assertion failed',detail=result.stderr[-1500:])
        else:
            expected=expected_fields(project,root)
            for spec in project['turns'][:round_index+1]:
                actual=json.loads((root/spec['artifact']).read_text())
                if spec['key'] not in actual or not equal(actual[spec['key']],expected[spec['artifact']][spec['key']]):
                    return dict(success=False,error='independent artifact assertion failed',artifact=spec['artifact'],field=spec['key'])
        return dict(success=True,error=None,checked_rounds=round_index+1)
    except Exception as exc:
        return dict(success=False,error=type(exc).__name__+': '+str(exc))
