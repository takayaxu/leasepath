"""The resource gate must fail closed before experimental model startup."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'preflight', Path(__file__).resolve().parents[1] / 'scripts/preflight_real_development.py')
preflight = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def test_busy_devices_rejected_even_when_reserved():
    text = '''| 0     910B2               | OK |
| 0 | bus | 0 | 0 / 0 | 46116/ 65536         |
| 1     910B2               | OK |
| 0 | bus | 0 | 0 / 0 | 45876/ 65536         |'''
    devices = preflight.parse_npu(text)
    assert len(devices) == 2
    assert not preflight.resource_gate(devices, [0, 1])['ready']


def test_empty_or_unreserved_or_duplicate_devices_rejected():
    idle = [dict(device=n, used_mib=3410, total_mib=65536, no_running_processes=True) for n in range(2)]
    for devices, reserved in [(idle, []), ([], [0, 1]), (idle, [0, 0]), (idle, [0, 2])]:
        assert not preflight.resource_gate(devices, reserved)['ready']
    assert preflight.resource_gate(idle, [0, 1])['ready']
    idle[0]['no_running_processes'] = False
    assert not preflight.resource_gate(idle, [0, 1])['ready']
