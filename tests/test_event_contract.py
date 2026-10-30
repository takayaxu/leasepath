"""Handwritten event-contract examples, including file/CLI label export."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from leasepath.trace import derive_idle_labels, read_events
from leasepath.contract_fixtures import expand_cases, check_case, write_jsonl

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'data/fixtures/event_contract.json').read_text())
CASES = list(expand_cases(CONFIG))


@pytest.mark.parametrize('case,events', CASES, ids=[case['id'] for case, _ in CASES])
def test_event_golden_case(case, events):
    check_case(case, events)


def test_physical_hit_miss_leave_full_label_identical():
    traces = {case['id']: events for case, events in CASES}
    assert derive_idle_labels(traces['legal_demand_physical_miss']) == derive_idle_labels(
        traces['legal_demand_physical_hit'])


def test_validate_trace_cli_exports_miss_as_legal_demand(tmp_path):
    case, events = next((case, events) for case, events in CASES
                        if case['id'] == 'legal_demand_physical_miss')
    trace = tmp_path / 'miss.events.jsonl'
    labels = tmp_path / 'labels' / 'miss.labels.jsonl'
    write_jsonl(trace, events)
    completed = subprocess.run(
        [sys.executable, str(ROOT / 'scripts/validate_trace.py'), str(trace), '--labels', str(labels)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(completed.stdout)['idle_episodes'] == 1
    assert read_events(labels) == [{**case['label_defaults'], **case['expected_labels'][0]}]
