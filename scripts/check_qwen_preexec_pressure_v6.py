"""Rejection-aware protocol audit plus continuously locked original model."""
import argparse
import json
from pathlib import Path

from audit_pressure_protocol_v3 import audit as protocol_audit, require
from pressure_disk_model_v1 import verify_disk_model


def audit(directory, trace_prefix=None):
    plan = json.loads((directory / 'frozen-plan.json').read_text())
    manifest = json.loads((directory / 'run-manifest.json').read_text())
    verification = verify_disk_model(plan)
    require(manifest['disk_model_verification'] == verification, 'model verification differs')
    require(json.loads((directory / 'model-verification-disk.json').read_text()) == verification,
            'model evidence file differs')
    require(plan['memory_budget']['minimum_MemAvailable_gib'] == 220, 'total admission changed')
    require(plan['memory_budget']['max_tool_address_space_gib'] == 120, 'tool reserve changed')
    for key in ('memory_admission_before', 'memory_admission_after_model_lock'):
        row = manifest[key]
        require(row['owned_model_bytes'] == verification['model_bytes'], 'incorrect model credit')
        require(row['mincore_all_pages_resident'], 'nonresident credit')
        require(row['available_bytes'] + row['owned_model_bytes'] == row['effective_available_bytes'], 'incorrect memory sum')
        require(row['required_total_bytes'] == 220*1024**3 and row['effective_available_bytes'] >= row['required_total_bytes'], 'memory admission unmet')
    result = protocol_audit(directory, trace_prefix)
    result.update(disk_model_verified=verification, memory_accounting_verified=True,
                  model_evidence_scope='live continuously locked original model; both full hashes verified')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--trace-prefix', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run, args.trace_prefix)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('status', 'new_generations', 'tasks_checked', 'diagnostic')}))
