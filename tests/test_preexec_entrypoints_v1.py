from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from preflight_pressure_preexec_v1 import verify_inheritance,resource_violation


def sample():
    control={'cpus':[14,42],'numa_node':1}
    evidence={'cpus':[14,42],'memory_policy':2,'node_mask':2,'thp_disabled':True,'memory_limit_bytes':12*1024**3,'cpu_limit_s':240,'pid':17}
    call={'status':'ok','child_pid':17,'preexec_controls':{'launcher':dict(evidence),'python_entry':dict(evidence),'verified_before_legacy_worker_import':True},'thread_diagnostics':{'pools_after':[{'num_threads':1}]},'worker_controls':{'after':{'cpus':[14,42],'node_mask':2,'thp_enabled':'0'}},'child_lifetime':{'major_faults':0,'block_input_ops':0},'file_diagnostics':{'io_since_exec':{'read_bytes':0}},'worker_timing':{'overall':{'major_faults':0,'read_bytes':0}}}
    return call,control


def test_inheritance_rejects_mismatched_entry_not_just_environment_flag():
    call,control=sample();verify_inheritance(call,control)
    call['preexec_controls']['python_entry']['node_mask']=1
    with pytest.raises(ValueError):verify_inheritance(call,control)


def test_zero_disk_does_not_hide_major_fault():
    call,control=sample();assert not resource_violation(call)
    call['child_lifetime']['major_faults']=1;assert resource_violation(call)
    call,control=sample();del call['child_lifetime'];assert resource_violation(call)


def test_native_entrypoints_compile():
    for f in ['scripts/run_qwen_preexec_pressure_v1.py','scripts/check_qwen_preexec_pressure_v1.py','scripts/audit_pressure_preexec_v1.py']:
        compile((ROOT/f).read_text(),f,'exec')
