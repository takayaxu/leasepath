"""Independently evaluate, freeze, audit and summarize designed real execution."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_dataset import export
from leasepath.d2_expansion import EXTRA_FEATURES, project_extra

SCHEDULE = [0, .01, .03, .1, .3, 1, 2, 3, 5, 10, 30, 120]


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0}
    return {'n': len(values), 'min': values[0], 'median': values[len(values)//2],
            'p95': values[min(len(values)-1, int(len(values)*.95))], 'max': values[-1],
            'ge_1s': sum(v >= 1 for v in values), 'ge_3s': sum(v >= 3 for v in values),
            'ge_5s': sum(v >= 5 for v in values)}


def evaluate(home, task, run):
    name = task['instance_id']
    candidate = home / 'runs/greedy-guided' / name / 'solution.py'
    target = home / 'evaluation-runs' / name
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(candidate, target / 'solution.py')
    work = Path(task['workspaces']['greedy-guided'])
    for filename in ['test_visible.py', 'test_workload.py']:
        original = subprocess.check_output(['git', '-C', str(work), 'show', 'HEAD:' + filename])
        if hashlib.sha256(original).hexdigest() != task['protected_sha256'][filename]:
            raise ValueError('original task test provenance mismatch')
        (target / filename).write_bytes(original)
    independent = home / 'evaluation' / name / 'test_independent.py'
    if digest(independent) != task['independent_test_sha256']:
        raise ValueError('independent oracle provenance mismatch')
    shutil.copy2(independent, target / independent.name)
    command = [str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q', 'test_visible.py',
               'test_workload.py', 'test_independent.py', '--junitxml', str(target / 'junit.xml')]
    try:
        process = subprocess.run(command, cwd=target, text=True, capture_output=True, timeout=60,
                                 env={**os.environ, 'PYTHONPATH': str(target), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'})
        rc, output = process.returncode, process.stdout + process.stderr
    except subprocess.TimeoutExpired as exc:
        rc, output = -1, str(exc)
    (target / 'pytest.log').write_text(output)
    cases = list(ET.parse(target / 'junit.xml').iter('testcase')) if (target / 'junit.xml').exists() else []
    passed = (rc == 0 and len(cases) == 3 and all(
        c.find('failure') is None and c.find('error') is None and c.find('skipped') is None for c in cases))
    tools = run['tool_stats']
    criteria = {
        'independent_visible_workload_tests_passed': passed,
        'submitted': run['exit_status'] == 'Submitted',
        'source_edited': run['source_edited'],
        'protected_files_unchanged': run['protected_files_unchanged'] and all(
            digest(work / f) == h for f, h in task['protected_sha256'].items()),
        'real_inspection': any(t['category'] == 'inspect' for t in tools),
        'real_edit': any(t['source_changed'] for t in tools),
        'successful_full_visible_suite': any(t['command'].strip() == 'python -m pytest -q test_visible.py test_workload.py'
                                            and t['returncode'] == 0 for t in tools),
        'no_excess_identical_loop': run['loop_feedback_count'] <= 1,
    }
    return {'task_id': name, 'workflow_id': run['workflow_id'],
            'group_id': task['source_group'] + ':' + name,
            'candidate_sha256': digest(candidate), 'tests_passed': passed, 'tests_observed': len(cases),
            'returncode': rc, 'criteria': criteria, 'quality_passed': all(criteria.values())}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=ROOT / '.artifacts/d2-expansion-v1')
    p.add_argument('--output', type=Path, default=ROOT / 'results/prediction/d2_expansion_v1')
    args = p.parse_args()
    home, out = args.root.resolve(), args.output.resolve()
    tasks_manifest = read(home / 'tasks.json')
    tasks = tasks_manifest['tasks']
    runs = read(home / 'runs/greedy-guided/results.json')
    rm = read(home / 'runs/greedy-guided/run-manifest.json')
    cfg = read(ROOT / 'configs/prediction/d2_expansion_v1.json')
    if (rm['configuration'] != cfg or rm['runner_sha256'] != digest(ROOT / 'scripts/run_d2_expansion.py')
            or rm['tasks_sha256'] != digest(home / 'tasks.json')
            or tasks_manifest['source_sha256'] != digest(ROOT / 'scripts/prepare_d2_expansion.py')):
        raise ValueError('collection source/configuration changed after freeze')
    if {t['instance_id'] for t in tasks} != {r['task_id'] for r in runs} or len(runs) != len(tasks):
        raise ValueError('collection must include each predeclared task exactly once')
    out.mkdir(parents=True, exist_ok=False)
    frozen = home / 'frozen-round'
    frozen.mkdir(exist_ok=False)
    with (ROOT / cfg['raw_log']).open('rb') as source:
        fcntl.flock(source.fileno(), fcntl.LOCK_SH)
        try:
            with (frozen / 'raw.jsonl').open('wb') as target:
                shutil.copyfileobj(source, target)
        finally:
            fcntl.flock(source.fileno(), fcntl.LOCK_UN)
    sources = [ROOT / cfg['backend_manifest'], ROOT / cfg['upstream_prompt'],
               *Path(ROOT / 'src/leasepath').glob('*.py'), *Path(ROOT / 'scripts').glob('*d2*.py'),
               ROOT / 'configs/prediction/d2_expansion_v1.json', ROOT / 'schemas/trace.schema.json',
               ROOT / 'pyproject.toml', ROOT / 'tests/test_d2_expansion.py', ROOT / 'tests/test_d2_dataset.py']
    hashes = {}
    for source in sources:
        rel = source.relative_to(ROOT)
        target = frozen / 'source' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        hashes[str(rel)] = digest(source)
    shutil.copy2(home / 'tasks.json', frozen / 'tasks.json')
    shutil.copy2(ROOT / '.artifacts/d2/server.log', frozen / 'server.log')
    write(out / 'source-hashes.json', hashes)
    shutil.copy2(home / 'calibration.json', out / 'calibration.json')
    shutil.copy2(home / 'runs/greedy-guided/run-manifest.json', out / 'run-manifest.json')
    write(out / 'task-provenance.json', {**tasks_manifest,
          'tasks': [{k: v for k, v in t.items() if k != 'workspaces'} for t in tasks]})
    evaluations, audits = [], {}
    for task in tasks:
        run = next(r for r in runs if r['task_id'] == task['instance_id'])
        result = evaluate(home, task, run)
        wf = run['workflow_id']
        try:
            export(frozen / 'raw.jsonl', home / 'datasets/workflow-audits' / wf,
                   rm['backend_compatibility_id'], workflow_ids=[wf],
                   elapsed_points_s=SCHEDULE, max_snapshots_per_episode=20)
            audits[wf] = {'passed': True}
        except ValueError as exc:
            audits[wf] = {'passed': False, 'reason': str(exc), 'handling': 'whole workflow quarantined; raw retained'}
        result['criteria']['backend_audit'] = audits[wf]['passed']
        result['quality_passed'] = all(result['criteria'].values())
        evaluations.append(result)
        print(json.dumps(result), flush=True)
    write(out / 'execution-quality.json', evaluations)
    write(out / 'backend-audits.json', audits)
    raw = sorted(lines(frozen / 'raw.jsonl'), key=lambda r: (r['ts_ns'], r['pid'], r['seq']))
    scopes = {'all-observed': [r['workflow_id'] for r in runs if audits[r['workflow_id']]['passed']],
              'qualified': [e['workflow_id'] for e in evaluations if e['quality_passed']]}
    summaries = {}
    for scope, workflows in scopes.items():
        if not workflows:
            summaries[scope] = {'status': 'empty', 'workflows': 0}
            continue
        data = home / 'datasets' / scope
        summary = export(frozen / 'raw.jsonl', data, rm['backend_compatibility_id'],
                         workflow_ids=workflows, elapsed_points_s=SCHEDULE, max_snapshots_per_episode=20)
        samples, labels = lines(data / 'snapshots.jsonl'), lines(data / 'episode_labels.jsonl')
        extras = []
        for sample in samples:
            extra = project_extra(raw, sample['run_id'], sample['idle_episode_id'], sample['snapshot_ns'])
            extras.append({'sample_id': sample['sample_id'], 'feature_schema': 'expansion-v1',
                           'features': {**sample['features'], **extra}})
        (data / 'features-expansion-v1.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in extras))
        tools = [r for r in raw if r['kind'] == 'tool_end' and r.get('workflow_id') in workflows]
        starts = [r for r in raw if r['kind'] == 'tool_start' and r.get('workflow_id') in workflows]
        coverage = {k: {'known': sum(e['features'][k] is not None for e in extras), 'total': len(extras)}
                    for k in sorted(EXTRA_FEATURES)}
        tool_by_id = {r['tool_id']: r for r in starts}
        long_tools = [{'workflow_id': r['workflow_id'],
                       'command_category': tool_by_id[r['tool_id']]['command_category'],
                       'workload_family': tool_by_id[r['tool_id']].get('workload_family'),
                       'duration_s': r['duration_ns']/1e9, 'returncode': r['returncode']}
                      for r in tools if r['duration_ns'] >= 1e9]
        audit = {'workflow_count': len(workflows), 'unique_parents': len(workflows),
                 'tool_duration_s': distribution([r['duration_ns']/1e9 for r in tools]),
                 'idle_duration_s': distribution([(l['end_ns']-l['start_ns'])/1e9 for l in labels]),
                 'second_scale_tools': long_tools, 'feature_coverage': coverage,
                 'snapshot_elapsed_ge_1s': sum(s['features']['elapsed_idle_s'] >= 1 for s in samples),
                 'command_categories': dict(Counter(r['command_category'] for r in starts)),
                 'workload_families': dict(Counter(r.get('workload_family') for r in starts if r.get('workload_family'))),
                 'tool_states': dict(Counter(str(e['features']['tool_state']) for e in extras)),
                 'phases': dict(Counter(str(e['features']['phase']) for e in extras)),
                 'prior_failed_tests': dict(Counter(e['features']['prior_failed_tests'] for e in extras)),
                 'queued_snapshots': sum(s['features']['phase'] == 'queued' for s in samples),
                 'features_sidecar_sha256': digest(data / 'features-expansion-v1.jsonl'),
                 'dataset_summary': summary}
        summaries[scope] = audit
        write(out / f'{scope}-summary.json', audit)
        subprocess.run([str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/export_d2_hazards.py'),
                        '--dataset', str(data), '--output', str(home / 'hazards' / scope)], check=True)
        shutil.copy2(home / 'hazards' / scope / 'summary.json', out / f'{scope}-hazard-summary.json')
    workflows = set(scopes['all-observed'])
    # Preserve exact commands for a human audit of local-environment boundaries.
    write(out / 'command-audit.json', [{'workflow_id': r['workflow_id'], 'tool_id': r['tool_id'],
           'command': r['command'], 'category': r['command_category']}
          for r in raw if r['kind'] == 'tool_start' and r.get('workflow_id') in workflows])
    summary = {'status': 'completed', 'unique_new_parents': len(tasks), 'executions': len(runs),
               'correct_executions': sum(e['tests_passed'] for e in evaluations),
               'qualified_executions': sum(e['quality_passed'] for e in evaluations),
               'auditable_executions': len(workflows), 'raw_sha256': digest(frozen / 'raw.jsonl'),
               'data_root': str(home), 'dataset_scope': 'designed tasks, real measured agent tools and backend lookups',
               'feature_schema': 'expansion-v1 sidecar; frozen predictor interface unchanged',
               'limitations': ['six new development parents, not enough for independent generalization claims',
                              'seconds include test fixture construction and validation, not function runtime alone',
                              'explicit context close yields D; no artificial natural C',
                              'same-host LocalEnvironment; hashes and command review, no OS isolation',
                              'qualified subset conditions on success; all-observed is primary',
                              'failed tests count is historical failures, not proof of retry intent',
                              'no forced failures, artificial sleep, new training or policy benefit claim']}
    write(out / 'summary.json', summary)
    write(out / 'SHA256SUMS.json', {p.name: digest(p) for p in out.iterdir() if p.is_file()})
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
