import json
from pathlib import Path
import subprocess

import pytest

from leasepath.real_environment import preflight_environment


@pytest.mark.parametrize('failure', ['host_entry', 'wrong_source', 'host_python'])
def test_actual_shell_preflight_rejects_fallback(monkeypatch, tmp_path, failure):
    work=tmp_path/'workspace'; wrappers=tmp_path/'tool-bin';env={"PATH":str(wrappers),"PYTHONPATH":str(work/'src')}
    data={'source':str(work/'src/flask/__init__.py'),'prefix':str(tmp_path/'environment'),
          'executable':str(tmp_path/'environment/bin/python')}
    entries=[str(wrappers/c) for c in ['python','pytest','flask']]
    if failure=='host_entry':entries[-1]='/host/conda/bin/flask'
    if failure=='wrong_source':data['source']='/host/site-packages/flask/__init__.py'
    if failure=='host_python':data['prefix']='/host/conda'
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a,0,
        stdout='\n'.join(entries+[json.dumps(data)]),stderr=''))
    with pytest.raises(ValueError,match='entry|source|interpreter'):
        preflight_environment(work,env,wrappers,'pallets/flask',cpus=[0])
