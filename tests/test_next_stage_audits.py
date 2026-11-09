from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from audit_independent_agent_loop import parse_native_call


def test_native_call_link_checks_raw_arguments():
    assert parse_native_call('✿FUNCTION✿: evaluate_candidate\n✿ARGS✿: {"candidate":[1,0]}')==('evaluate_candidate',{'candidate':[1,0]})
    with pytest.raises(ValueError):parse_native_call('Host says evaluate_candidate([1,0])')
    with pytest.raises(ValueError):parse_native_call('✿FUNCTION✿: a\n✿ARGS✿: {}\n✿FUNCTION✿: b\n✿ARGS✿: {}')
    with pytest.raises(ValueError):parse_native_call('✿FUNCTION✿: a\n✿ARGS✿: {} and then run b')


def test_new_sources_compile_without_importing_gpu():
    files=['scripts/run_qwen_distribution_pressure.py','scripts/check_qwen_distribution_pressure.py',
           'scripts/audit_pressure_live_state.py','scripts/qwen_cache_independent_agent_batch.py',
           'scripts/audit_independent_agent_loop.py','src/leasepath/independent_agent_worker.py']
    for name in files:compile((ROOT/name).read_text(),name,'exec')
