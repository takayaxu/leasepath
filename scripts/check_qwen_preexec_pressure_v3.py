"""Existing independent protocol audit plus staged model provenance/accounting."""
import argparse
import json
from pathlib import Path

from check_qwen_preexec_pressure_v2 import audit as protocol_audit, require
from pressure_staged_model_v1 import verify_staged_model


def audit(directory, trace_prefix=None):
    plan = json.loads((directory / 'frozen-plan.json').read_text())
    manifest = json.loads((directory / 'run-manifest.json').read_text())
    verification = verify_staged_model(plan)
    require(manifest['staged_model_verification'] == verification, 'staging evidence differs')
    require(plan['memory_budget']['minimum_MemAvailable_gib'] == 220, 'original total admission changed')
    require(plan['memory_budget']['max_tool_address_space_gib'] == 120, 'tool reserve changed')
    for key in ('memory_admission_before', 'memory_admission_after_model_lock'):
        row = manifest[key]
        require(row['owned_model_bytes'] == verification['model_bytes'], 'model credited more than once')
        require(row['mincore_all_pages_resident'], 'nonresident model credited')
        require(row['available_bytes'] + row['owned_model_bytes'] == row['effective_available_bytes'], 'incorrect memory sum')
        require(row['required_total_bytes'] == 220 * 1024**3 and row['effective_available_bytes'] >= row['required_total_bytes'], 'memory admission not met')
    result = protocol_audit(directory, trace_prefix)
    result['staged_model_verified'] = verification
    result['memory_accounting_verified'] = True
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--trace-prefix', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.run, args.trace_prefix)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
