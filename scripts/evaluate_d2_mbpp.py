"""Evaluate frozen source independently and report observable execution quality."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--variant', choices=['baseline', 'direct', 'greedy', 'sampled', 'greedy-explicit', 'greedy-mini', 'greedy-guided'], required=True)
    p.add_argument('--tasks', type=Path, default=ROOT / '.artifacts/d2-next/tasks.json')
    p.add_argument('--runs', type=Path)
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    home = args.tasks.resolve().parent
    runs = args.runs or home / 'runs' / args.variant
    out = (args.output or home / 'evaluation-runs' / args.variant).resolve()
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for task in json.loads(args.tasks.read_text())['tasks']:
        name = task['instance_id']
        work = out / name
        work.mkdir(exist_ok=False)
        if args.variant == 'baseline':
            source = subprocess.check_output(['git', '-C', task['workspaces']['direct'], 'show', 'HEAD:solution.py'])
            record = {}
        else:
            source = (runs / name / 'solution.py').read_bytes()
            record = json.loads((runs / name / 'result.json').read_text())
        (work / 'solution.py').write_bytes(source)
        visible = Path(task['workspaces']['greedy']) / 'test_visible.py'
        original_visible = subprocess.check_output(['git', '-C', task['workspaces']['greedy'], 'show', 'HEAD:test_visible.py'])
        if hashlib.sha256(original_visible).hexdigest() != task['visible_test_sha256']:
            raise ValueError('visible test provenance differs')
        (work / 'test_visible.py').write_bytes(original_visible)
        independent = home / 'evaluation' / name / 'test_independent.py'
        if hashlib.sha256(independent.read_bytes()).hexdigest() != task['independent_test_sha256']:
            raise ValueError('independent test provenance differs')
        shutil.copy2(independent, work / 'test_independent.py')
        env = {**os.environ, 'PYTHONPATH': str(work), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
        command = [str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q', 'test_visible.py',
                   'test_independent.py', '--junitxml', str(work / 'junit.xml')]
        try:
            run = subprocess.run(command, cwd=work, env=env, text=True, capture_output=True, timeout=60)
            stdout, returncode = run.stdout + run.stderr, run.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, returncode = str(exc), -1
        (work / 'pytest.log').write_text(stdout)
        cases = []
        if (work / 'junit.xml').exists():
            for case in ET.parse(work / 'junit.xml').iter('testcase'):
                status = 'failed' if case.find('failure') is not None else 'error' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
                cases.append({'name': case.attrib['name'], 'file': case.attrib.get('classname'), 'status': status})
        counts = {s: sum(c['status'] == s for c in cases) for s in ['passed', 'failed', 'error', 'skipped']}
        all_observed = len(cases) == task['independent_tests'] + task['visible_tests']
        tests_passed = returncode == 0 and all_observed and all(c['status'] == 'passed' for c in cases)
        tools = record.get('tool_stats', [])
        criteria = {'independent_and_visible_tests_passed': tests_passed,
                    'submitted': record.get('exit_status') == 'Submitted',
                    'source_edited': record.get('source_edited') is True,
                    'protected_files_unchanged': record.get('protected_files_unchanged') is True,
                    'real_inspection': any(t['category'] == 'inspect' for t in tools),
                    'real_source_edit': any(t['source_changed'] for t in tools),
                    'successful_visible_pytest': any(t['category'] == 'test' and t['returncode'] == 0 for t in tools),
                    'no_excess_identical_loop': record.get('loop_feedback_count', 0) <= 1}
        result = {'task_id': name, 'variant': args.variant, 'workflow_id': record.get('workflow_id'),
                  'returncode': returncode, 'tests_observed': all_observed, 'test_counts': counts,
                  'tests_passed': tests_passed, 'quality_criteria': criteria,
                  'execution_quality_passed': args.variant in {'greedy', 'sampled', 'greedy-explicit', 'greedy-mini', 'greedy-guided'} and all(criteria.values()),
                  'source_sha256': hashlib.sha256(source).hexdigest(),
                  'evaluation_scope': 'MBPP-derived local protocol; independent additional tests, not official MBPP pass@1'}
        results.append(result)
        (work / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    summary = {'variant': args.variant, 'tasks': len(results), 'tests_passed_tasks': sum(r['tests_passed'] for r in results),
               'execution_quality_passed_tasks': sum(r['execution_quality_passed'] for r in results),
               'results': results}
    (out / 'results.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
