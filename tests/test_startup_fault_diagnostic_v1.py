from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from audit_pressure_fault_windows_v1 import classify


def sample(first=0,after=0,last=0,life=0):
    return {'startup_fault_diagnostic':{'boundaries':[{'label':'first_after_resource_import','major_faults':first},{'label':'after_numerical','major_faults':after},{'label':'after_serialization','major_faults':last}],'imports':[]},'child_lifetime':{'major_faults':life},'worker_timing':{'overall':{'major_faults':0}}}


def test_fault_at_start_is_not_mistaken_for_serialization():
    r=classify(sample(1,1,1,1));assert r['first_cumulative_major_faults']==1 and not r['observed_intervals']


def test_post_compute_fault_is_not_claimed_as_startup():
    r=classify(sample(0,0,1,1));assert r['observed_intervals']==[{'from':'after_numerical','to':'after_serialization','major_faults':1}]


def test_final_gap_is_explicit_and_counters_must_be_monotonic():
    assert classify(sample(0,0,0,1))['post_last_snapshot_or_descendant_major_faults']==1
    with pytest.raises(ValueError):classify(sample(1,0,0,1))
    with pytest.raises(ValueError):classify(sample(0,0,1,0))
