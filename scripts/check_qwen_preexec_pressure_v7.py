"""V6 protocol/resource audit plus prospective parent I/O evidence coverage."""
import argparse
import json
from pathlib import Path

from check_qwen_preexec_pressure_v6 import audit as previous_audit
from audit_pressure_protocol_v3 import require


def audit(directory, trace_prefix=None):
    manifest = json.loads((directory / 'run-manifest.json').read_text())
    plan = json.loads((directory / 'frozen-plan.json').read_text())
    recipe = 'retained-source-paths-thread-attribution-v1'
    require(plan['parent_io_recipe'] == manifest['parent_io_recipe'] == recipe, 'parent I/O recipe differs')
    rows = json.loads((directory / 'source-path-handles.json').read_text())
    files = {r['path'] for r in rows if not r['directory']}
    require(set(manifest['source_sha256']).issubset(files), 'source path handle missing')
    require(manifest['source_path_handles']['all_identities_match'], 'source path identity failure')
    result = previous_audit(directory, trace_prefix)
    checks = 0
    for host in directory.glob('cycle-*/host.json'):
        evidence = json.loads((host.parent / 'parent-source-io.json').read_text())
        require(evidence['batch'] == host.parent.name and evidence['zero_io_gate_unchanged'], 'I/O evidence scope differs')
        require(not evidence['events'], 'attributed I/O/fault during accepted batch')
        require(evidence['stats']['source_sha256']['calls'] > 0, 'source check evidence missing')
        summary = json.loads((host.parent / 'batch/summary.json').read_text())
        require(evidence['stats']['native_generate']['calls'] == summary['generations'], 'native I/O coverage differs')
        checks += 1
    require(checks == (1 if result['diagnostic'] else 18), 'incomplete I/O attribution coverage')
    result.update(parent_io_recipe=recipe, parent_io_attribution_batches=checks,
                  source_path_handles_verified=True)
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
