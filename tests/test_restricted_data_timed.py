import os
import json
import subprocess
import sys
import numpy as np
import pytest

from leasepath.restricted_data_tools import Resource, SessionTools, file_hash
from leasepath.restricted_data_timed_tools import TimedSessionTools


@pytest.mark.parametrize('operation,parameters', [
    ('ridge_cv', {'alpha': 1., 'folds': 5}),
    ('impute', {'strategy': 'median'}),
    ('pca', {'components': 2}),
    ('window_rank', {'group_by': 'o_orderpriority', 'value': 'o_totalprice'}),
])
def test_actual_worker_equivalence_and_timing_stays_out_of_model_result(tmp_path, operation, parameters):
    if operation == 'window_rank':
        import duckdb
        source = tmp_path/'test.duckdb'
        c = duckdb.connect(str(source))
        c.execute("create table orders as select * from (values ('A',1,4.0),('A',2,4.0),('B',3,5.0)) t(o_orderpriority,o_orderkey,o_totalprice)")
        c.close()
        kind = 'tpch'
    else:
        source = tmp_path/'test.npz'
        rng = np.random.default_rng(1)
        X = rng.normal(size=(100, 4)); y = X[:, 0]*3+2
        if operation == 'impute': X[0, 0] = np.nan
        np.savez(source, X=X, y=y)
        kind = 'numpy'
    resource = {'dtest': Resource(source, file_hash(source), kind, {})}
    args = {'dataset_id': 'dtest', 'operation': operation, **parameters}
    original = SessionTools(tmp_path/'original', resource, [operation])
    timed = TimedSessionTools(tmp_path/'timed', resource, [operation])
    old, new = original.call('compute', args), timed.call('compute', args)
    assert set(old) == set(new) == {'artifact_id', 'result'}, (old, new)
    assert old['result'] == new['result']
    call = timed.calls[0]
    diag = call['worker_timing']
    assert call['child_wall_ns'] >= call['worker_execute_ns'] > 0
    assert call['artifact_write_fsync_ns'] > 0
    assert all(v >= 0 for phase in diag['phases'].values() for v in phase.values())
    assert sum(p['wall_ns'] for p in diag['phases'].values()) <= diag['overall']['wall_ns']
    phase = 'sql_connect_query_close' if kind == 'tpch' else 'npz_materialize'
    assert diag['phases'][phase]['wall_ns'] > 0
    assert 'worker_timing' not in new
    # Same protected source and untrusted-argument restrictions.
    assert 'error' in timed.call('compute', {**args, 'code': 'print(1)'})
    source.write_bytes(b'changed')
    assert 'error' in timed.call('inspect_input', {'dataset_id': 'dtest'})


def test_timed_timeout_reaps_child(tmp_path):
    source = tmp_path/'input'; source.write_bytes(b'not an npz')
    tools = TimedSessionTools(tmp_path/'session',
        {'dtest': Resource(source, file_hash(source), 'numpy', {})}, ['pca'], timeout=.001)
    answer = tools.call('compute', {'dataset_id': 'dtest', 'operation': 'pca', 'components': 1})
    assert answer['error'] == 'TimeoutError'
    with pytest.raises(ProcessLookupError):
        os.killpg(tools.calls[-1]['child_pid'], 0)


def test_cpu_diagnostic_disables_thp_only_in_own_child_and_preserves_answer(tmp_path):
    source = tmp_path/'small.npz'
    rng = np.random.default_rng(3)
    X = rng.normal(size=(100,4))
    np.savez(source, X=X, y=X[:,0]*2)
    parent_setting = open('/proc/self/status').read().split('THP_enabled:')[1].splitlines()[0]
    with source.open('rb') as stream:
        request = {'arguments':{'dataset_id':'dtest','operation':'ridge_cv','folds':5,'alpha':1.},
                   'kind':'numpy','fd':stream.fileno(),'threads':2,'memory_gib':12}
        env = {**os.environ,'OMP_NUM_THREADS':'2','OPENBLAS_NUM_THREADS':'2'}
        results = []
        for module in ['leasepath.restricted_data_timed_worker','leasepath.restricted_data_no_thp_worker']:
            stream.seek(0)
            done = subprocess.run([sys.executable,'-m',module],input=json.dumps(request),
                capture_output=True,text=True,check=True,pass_fds=(stream.fileno(),),env=env,timeout=30)
            results.append(json.loads(done.stdout)['result'])
    assert results[0] == results[1]
    assert open('/proc/self/status').read().split('THP_enabled:')[1].splitlines()[0] == parent_setting
