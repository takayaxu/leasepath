"""Prepare test-first repair tasks at two predeclared real-compute scales."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from prepare_d2_expansion import TASKS, module

ROOT = Path(__file__).resolve().parents[1]
BANDS = {'medium': (3.2, 5.8), 'large': (6.8, 9.5)}
SIZES = {
    'csv-ledger': {'medium': [750000, 900000, 1050000], 'large': [1500000, 1650000, 1800000, 1950000]},
    'jsonl-status': {'medium': [300000, 360000, 420000], 'large': [600000, 720000, 840000]},
    'graph-distance': {'medium': [750000, 900000, 1050000], 'large': [1500000, 1800000, 2100000]},
}
DEFECTS = {
    'csv-ledger': '''import csv
def summarize_csv(path):
    totals = {}
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            totals[row['tenant']] = int(row['cents'])
    return totals
''',
    'jsonl-status': '''import json
def count_statuses(path):
    counts = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                counts[json.loads(line)['status']] = 1
    return counts
''',
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_tests(folder, files, log_path):
    start = time.monotonic()
    result = subprocess.run([str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q', *files],
                            cwd=folder, env={**os.environ, 'PYTHONPATH': str(folder),
                                             'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'},
                            capture_output=True, text=True, timeout=60)
    elapsed = time.monotonic() - start
    log_path.write_text(result.stdout + result.stderr)
    return result.returncode, elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/d2-repair-v2')
    args = parser.parse_args()
    home = args.output.resolve()
    home.mkdir(parents=True, exist_ok=False)
    config = ROOT / 'configs/prediction/d2_repair_v2.json'
    records, calibration = [], []
    graph_run = ROOT / '.artifacts/d2-expansion-v1/runs/greedy-guided/designed-graph-distance'
    graph_source = graph_run / 'solution.py'
    previous = json.loads((ROOT / 'results/prediction/d2_expansion_v1/execution-quality.json').read_text())
    graph_evaluation = next(r for r in previous if r['task_id'] == 'designed-graph-distance')
    if graph_evaluation['tests_passed'] or sha(graph_source) != graph_evaluation['candidate_sha256']:
        raise ValueError('prior real failed graph candidate provenance mismatch')
    for spec in [s for s in TASKS if s['name'] in SIZES]:
        parent = 'designed-' + spec['name']
        visible_body = spec['visible']
        if spec['name'] == 'graph-distance':
            visible_body += '''    assert shortest_distance(p, -1, 3) is None
    assert shortest_distance(p, -1, -1) == 0
    p.write_text('0 1\\n\\n1 2\\n')
    assert shortest_distance(p, 0, 2) == 2
'''
        defect = graph_source.read_text() if spec['name'] == 'graph-distance' else DEFECTS[spec['name']]
        defect_origin = ({'kind': 'previous_agent_failure', 'path': str(graph_source.relative_to(ROOT)),
                          'workflow_id': graph_evaluation['workflow_id'], 'sha256': sha(graph_source)}
                         if spec['name'] == 'graph-distance' else
                         {'kind': 'explicitly_seeded_overwrite_instead_of_accumulate',
                          'sha256': hashlib.sha256(defect.encode()).hexdigest()})
        for band, (lower, upper) in BANDS.items():
            name = f'repair-{spec["name"]}-{band}'
            evaluation = home / 'evaluation' / name
            evaluation.mkdir(parents=True)
            visible = module(visible_body, 'visible')
            independent = module(spec['independent'], 'independent')
            (evaluation / 'test_visible.py').write_text(visible)
            (evaluation / 'test_independent.py').write_text(independent)
            attempts = []
            selected = None
            for size in SIZES[spec['name']][band]:
                workload = f'N = {size}\n' + module(spec['workload'], 'workload')
                (evaluation / 'test_workload.py').write_text(workload)
                (evaluation / 'solution.py').write_text(spec['reference'])
                rc, elapsed = run_tests(evaluation, ['test_visible.py', 'test_workload.py', 'test_independent.py'],
                                        evaluation / f'calibration-{size}.log')
                attempt = {'units': size, 'reference_elapsed_s': elapsed, 'returncode': rc}
                attempts.append(attempt)
                print(json.dumps({'task_id': name, **attempt}), flush=True)
                if rc:
                    raise ValueError(f'reference validation failed: {name}')
                if lower <= elapsed <= upper:
                    selected = size
                    break
                if elapsed > upper:
                    raise ValueError(f'predeclared calibration overshot band for {name}; preserve diagnostic')
            if selected is None:
                raise ValueError(f'predeclared sizes did not reach band for {name}')
            (evaluation / 'reference.py').write_text(spec['reference'])
            (evaluation / 'defective.py').write_text(defect)
            (evaluation / 'solution.py').write_text(defect)
            rc, broken_elapsed = run_tests(evaluation, ['test_visible.py', 'test_workload.py'],
                                           evaluation / 'defect-reproduction.log')
            if rc != 1:
                raise ValueError(f'defect must produce a real pytest failure: {name}')
            # Restore reference outside the agent workspace; preserve defect and logs.
            (evaluation / 'solution.py').write_text(spec['reference'])
            statement = spec['statement'] + (
                '\nRepair the existing defective implementation; preserve the API. '
                'This is a controlled test-first repair collection, not a production incident. '
                'First inspect ALL four files using exactly `cat README.md solution.py test_visible.py test_workload.py`. '
                'Before changing any source, run exactly `python -m pytest -q test_visible.py test_workload.py` '
                'to reproduce the defect. Use the actual failure to fix solution.py, then rerun the same full suite. '
                f'The integration test processes {selected} {spec["unit"]}; it must run in full, with no sleeps or skipped work. '
                'Only solution.py may be changed. Do not access other directories, reference solutions or independent tests. '
                'Submit with the standalone command `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` after success.')
            work = home / 'workspaces/greedy-guided' / name
            work.mkdir(parents=True)
            files = {'solution.py': defect, 'README.md': statement + '\n\nImplement ' + spec['signature'] + '\n',
                     'test_visible.py': visible, 'test_workload.py': workload,
                     '.gitignore': '__pycache__/\n.pytest_cache/\n'}
            for filename, content in files.items():
                (work / filename).write_text(content)
            subprocess.run(['git', 'init', '-q', str(work)], check=True)
            subprocess.run(['git', '-C', str(work), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(work), '-c', 'user.name=LeasePath Pilot',
                            '-c', 'user.email=leasepath@localhost', 'commit', '-qm', 'Frozen reproducible defect'], check=True)
            records.append({'instance_id': name, 'parent_task_id': parent,
                            'group_id': 'leasepath/designed-file-tasks-v1:' + parent,
                            'source_group': 'leasepath/designed-file-tasks-v1',
                            'problem_statement': statement, 'function_signature': spec['signature'],
                            'evidence_scope': 'controlled_test_first_repair_real_compute',
                            'split': 'pilot_development_only', 'scale_band': band,
                            'target_reference_wall_s': [lower, upper],
                            'workload_family': spec['family'], 'work_units': selected, 'work_unit': spec['unit'],
                            'workspaces': {'greedy-guided': str(work)},
                            'visible_tests': 2, 'independent_tests': 1,
                            'visible_test_sha256': sha(work / 'test_visible.py'),
                            'independent_test_sha256': sha(evaluation / 'test_independent.py'),
                            'protected_sha256': {f: sha(work / f) for f in files if f != 'solution.py'},
                            'reference_sha256': sha(evaluation / 'reference.py'), 'reference_validated': True,
                            'initial_source_sha256': sha(work / 'solution.py'), 'defect_origin': defect_origin})
            calibration.append({'task_id': name, 'group_id': records[-1]['group_id'], 'band': band,
                                'target_s': [lower, upper], 'attempts': attempts, 'selected_units': selected,
                                'defect_pytest_returncode': rc, 'defect_pytest_elapsed_s': broken_elapsed})
            print(json.dumps(calibration[-1]), flush=True)
    manifest = {'dataset': 'controlled-repair-v2', 'unique_parents': 3, 'executions': 6,
                'new_parent_tasks': 0, 'selection': 'three v1 parents at two predeclared scales; no agent outcome selection',
                'calibration_rule': 'first predeclared size in band; fail if band not reached or overshot',
                'source_sha256': sha(Path(__file__)), 'configuration_sha256': sha(config),
                'dependency_sha256': {'scripts/prepare_d2_expansion.py': sha(ROOT / 'scripts/prepare_d2_expansion.py')},
                'tasks': records}
    (home / 'tasks.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    (home / 'calibration.json').write_text(json.dumps(calibration, indent=2) + '\n')


if __name__ == '__main__':
    main()
