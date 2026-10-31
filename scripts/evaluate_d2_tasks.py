"""Evaluate published issue tests in separate workspaces, outside agent input."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mode', choices=['baseline', 'agent'], required=True)
    p.add_argument('--pilot', type=Path, default=ROOT / '.artifacts/d2/pilot')
    p.add_argument('--tasks', type=Path, default=ROOT / '.artifacts/d2/tasks.json')
    p.add_argument('--output', type=Path, help='Fresh evaluation root; defaults to evaluation-runs/<mode>')
    args = p.parse_args()
    args.tasks = args.tasks.resolve()
    args.pilot = args.pilot.resolve()
    tasks = json.loads(args.tasks.read_text())['tasks']
    out = (args.output or ROOT / '.artifacts/d2/evaluation-runs' / args.mode).resolve()
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for task in tasks:
        work = out / task['instance_id']
        if work.exists():
            raise ValueError(f'refusing to overwrite evaluation: {work}')
        subprocess.run(['git', 'clone', '--quiet', '--shared', task['workspace'], str(work)], check=True)
        agent_patch = args.pilot / task['instance_id'] / 'agent.patch'
        if args.mode == 'agent' and agent_patch.stat().st_size:
            subprocess.run(['git', '-C', str(work), 'apply', str(agent_patch)], check=True)
        tests = args.tasks.parent / 'evaluation' / (task['instance_id'] + '.test.patch')
        subprocess.run(['git', '-C', str(work), 'apply', str(tests)], check=True)
        files = sorted(set(re.findall(r'^\+\+\+ b/(.*\.py)$', tests.read_text(), re.M)))
        expected_fail = json.loads(task['FAIL_TO_PASS'])
        junit = work / 'evaluation.xml'
        # These are issue-level checks, not the official SWE-bench harness.
        selection = ' or '.join(json.loads(task['FAIL_TO_PASS']) + json.loads(task['PASS_TO_PASS']))
        command = [str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q', *files,
                   '-k', selection, '--junitxml', str(junit)]
        env = {**os.environ, 'PYTHONPATH': str(work), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
        try:
            result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=180)
            stdout, returncode = result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout, returncode = str(exc), -1
        (out / (task['instance_id'] + '.log')).write_text(stdout)
        cases = []
        if junit.exists():
            for case in ET.parse(junit).iter('testcase'):
                status = 'failed' if case.find('failure') is not None else 'error' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
                cases.append({'name': case.attrib['name'], 'status': status})
        target_cases = [c for c in cases if c['name'].split('[')[0] in expected_fail]
        target_observed = all(any(c['name'].split('[')[0] == name for c in target_cases) for name in expected_fail)
        record = {'task_id': task['instance_id'], 'mode': args.mode, 'returncode': returncode,
                  'issue_tests_observed': target_observed, 'target_tests': target_cases,
                  'test_counts': {s: sum(c['status'] == s for c in cases) for s in ['passed', 'failed', 'error', 'skipped']},
                  'issue_tests_passed': target_observed and all(c['status'] == 'passed' for c in target_cases),
                  'evaluation_scope': 'published SWE-bench test patch and named tests; local Python 3.12, not official container harness'}
        results.append(record)
        print(json.dumps(record), flush=True)
    (out / 'results.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
