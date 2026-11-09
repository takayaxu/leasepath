"""The gate must reject confounded comparisons even when every answer is correct."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from probe_qwen_controlled_tools import qualify
from leasepath.prediction_training import read_json


def quiet_groups():
    plan=read_json(ROOT/'configs/prediction/qwen_cache_controlled_2026-11-09.json')
    groups=[]
    for _ in range(6):
        def sample(end):
            return {'wall_ns':10**10 if end else 0,
                    'psi':{name:{kind:{'total':0} for kind in ('some','full')} for name in ('cpu','io','memory')},
                    'cpu_stat':('cpu 400 0 0 600 0 0 0 0\n' if end else 'cpu 0 0 0 0 0 0 0 0\n')+
                               ''.join(f'cpu{i} 0\n' for i in range(56)),
                    'global_settings':{'thp':'always'},'meminfo_kib':{'MemAvailable':100*1024**2}}
        rows=[]
        for task in plan['live_tasks']:
            control=plan['tool_controls'][task]
            state={'cpus':control['cpus'],'node_mask':2,'memory_policy':2,'thp_enabled':'0',
                   'runtime_modules':{'numpy':'/dev/shm/leasepath-controlled-runtime-2026-11-09/numpy/__init__.py'}}
            rows.append({'task':task,'correct':True,'call':{'started_ns':0,'ended_ns':10**10,'status':'ok',
                         'worker_timing':{'overall':{'wall_ns':10**10,'all_thread_runqueue_wait_ns':10**7,
                                                    'read_bytes':0,'major_faults':0}},
                         'worker_controls':{'before':deepcopy(state),'after':deepcopy(state)}}})
        groups.append({'before':sample(False),'after':sample(True),'rows':rows})
    return groups,plan


def test_gate_accepts_stable_correct_tools_and_rejects_ridge_tail():
    groups,plan=quiet_groups()
    assert qualify(groups,plan)['status']=='passed'
    groups[2]['rows'][1]['call']['ended_ns']*=4
    result=qualify(groups,plan)
    assert result['status']=='noise_gate_failed'
    assert any('ridge' in reason for reason in result['reasons'])


def test_gate_rejects_host_drift_even_with_constant_correct_tool_times():
    groups,plan=quiet_groups()
    groups[1]['after']['meminfo_kib']['MemAvailable']-=20*1024**2
    assert qualify(groups,plan)['status']=='noise_gate_failed'
    groups,plan=quiet_groups()
    groups[0]['rows'][1]['call']['worker_timing']['overall']['read_bytes']=4096
    assert qualify(groups,plan)['status']=='noise_gate_failed'
    groups,plan=quiet_groups()
    groups[0]['after']['cpu_stat']=groups[0]['after']['cpu_stat'].replace('400 0 0 600','800 0 0 200')
    assert qualify(groups,plan)['status']=='noise_gate_failed'
