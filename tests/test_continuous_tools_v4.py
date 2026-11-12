import csv
import json
import math
import os
from leasepath.continuous_projects_v2 import projects,initialize
from leasepath.continuous_tools_v4 import ProjectTools


def test_public_ols_identity_catches_uncentered_variance_without_private_answers(tmp_path):
    project=next(p for p in projects() if p['id']=='continuous_experiment')
    workspace=initialize(project,tmp_path/'project')
    tools=ProjectTools(workspace,tmp_path/'evidence',project,[min(os.sched_getaffinity(0))])
    tools.round=4
    rows=list(csv.DictReader((workspace/'measurements.csv').open()))
    xs=[float(r['x']) for r in rows];ys=[float(r['y']) for r in rows]
    mx=math.fsum(xs)/len(xs);my=math.fsum(ys)/len(ys)
    cov=math.fsum((x-mx)*(y-my) for x,y in zip(xs,ys))
    bad=cov/math.fsum((x+mx)**2 for x in xs)
    path=workspace/'metrics.json';path.write_text(json.dumps({'slope':bad}))
    result=tools.call('run_checks',{})
    assert result['returncode']!=0 and 'OLS normal equation failed' in result['stderr']
    assert 'expected' not in result
    slope=cov/math.fsum((x-mx)**2 for x in xs)
    path.write_text(json.dumps({'slope':slope}))
    assert tools.call('run_checks',{})['returncode']==0
    intercept=my-slope*mx
    path.write_text(json.dumps({'slope':slope,'intercept':intercept,'mse':1.}))
    assert tools.call('run_checks',{})['returncode']!=0
    mse=math.fsum((y-slope*x-intercept)**2 for x,y in zip(xs,ys))/len(xs)
    path.write_text(json.dumps({'slope':slope,'intercept':intercept,'mse':mse}))
    assert tools.call('run_checks',{})['returncode']==0
