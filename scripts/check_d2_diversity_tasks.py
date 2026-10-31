"""Run frozen reference pytest and real unimplemented-function negative controls."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', type=Path, default=ROOT / '.artifacts/d2-diversity-v3/tasks.json')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    home = args.tasks.resolve().parent
    output = (args.output or home / 'reference-validation').resolve()
    output.mkdir(parents=True, exist_ok=False)
    tasks = json.loads(args.tasks.read_text())['tasks']

    def check(task):
        target = output / task['instance_id']
        target.mkdir()
        evaluation = home / 'evaluation' / task['instance_id']
        work = Path(task['workspaces']['greedy-guided'])
        if (sha(evaluation / 'reference.py') != task['reference_sha256']
                or sha(evaluation / 'test_independent.py') != task['independent_test_sha256']):
            raise ValueError('frozen reference/oracle changed')
        shutil.copy2(evaluation / 'reference.py', target / 'solution.py')
        for f in ['test_visible.py', 'test_workload.py']:
            original = subprocess.check_output(['git', '-C', str(work), 'show', 'HEAD:' + f])
            if hashlib.sha256(original).hexdigest() != task['protected_sha256'][f]:
                raise ValueError('frozen visible/workload test changed')
            (target / f).write_bytes(original)
        shutil.copy2(evaluation / 'test_independent.py', target / 'test_independent.py')
        env = {**os.environ, 'PYTHONPATH': str(target), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': ''}
        cmd = [str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q']
        reference = subprocess.run(cmd, cwd=target, env=env, capture_output=True, text=True, timeout=30)
        (target / 'pytest.log').write_text(reference.stdout + reference.stderr)
        (target / 'solution.py').write_text(task['function_signature'] + '\n    raise NotImplementedError\n')
        negative = subprocess.run([*cmd, 'test_workload.py'], cwd=target, env=env,
                                  capture_output=True, text=True, timeout=30)
        (target / 'negative-control.log').write_text(negative.stdout + negative.stderr)
        return {'task_id': task['instance_id'], 'reference_returncode': reference.returncode,
                'stub_returncode': negative.returncode,
                'stub_rejected': negative.returncode == 1 and 'NotImplementedError' in negative.stdout}

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check, tasks))
    (output.parent / (output.name + '.json')).write_text(json.dumps(results, indent=2) + '\n')
    if not all(r['reference_returncode'] == 0 and r['stub_rejected'] for r in results):
        raise ValueError('reference or negative-control validation failed; preserve output')
    print(json.dumps({'reference_pytest_passed': len(results), 'stub_rejected': len(results)}))


if __name__ == '__main__':
    main()
