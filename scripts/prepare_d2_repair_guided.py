"""Derive fresh repair workspaces from the unchanged, calibrated v2 task set."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, default=ROOT / '.artifacts/d2-repair-v2')
    p.add_argument('--output', type=Path, default=ROOT / '.artifacts/d2-repair-v2-guided')
    args = p.parse_args()
    source, out = args.source.resolve(), args.output.resolve()
    previous = json.loads((source / 'tasks.json').read_text())
    if previous['source_sha256'] != sha(ROOT / 'scripts/prepare_d2_repair.py'):
        raise ValueError('calibrated preparation source changed')
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / 'evaluation', out / 'evaluation')
    shutil.copy2(source / 'calibration.json', out / 'calibration.json')
    for task in previous['tasks']:
        old = Path(task['workspaces']['greedy-guided'])
        work = out / 'workspaces/greedy-guided' / task['instance_id']
        work.mkdir(parents=True)
        for f in ['solution.py', *task['protected_sha256']]:
            (work / f).write_bytes(subprocess.check_output(['git', '-C', str(old), 'show', 'HEAD:' + f]))
            expected = task['initial_source_sha256'] if f == 'solution.py' else task['protected_sha256'][f]
            if sha(work / f) != expected:
                raise ValueError('initial calibrated workspace mismatch')
        subprocess.run(['git', 'init', '-q', str(work)], check=True)
        subprocess.run(['git', '-C', str(work), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(work), '-c', 'user.name=LeasePath Pilot',
                        '-c', 'user.email=leasepath@localhost', 'commit', '-qm', 'Same frozen defect, fresh guided run'], check=True)
        task['workspaces']['greedy-guided'] = str(work)
    previous.update(source_sha256=sha(Path(__file__)),
                    derived_from={'path': str(source / 'tasks.json'), 'sha256': sha(source / 'tasks.json'),
                                  'reason': 'first runner entered shell-quoting loops; preserve diagnostic; whole-file edit guidance only'},
                    dependency_sha256={**previous['dependency_sha256'],
                                       'scripts/prepare_d2_repair.py': sha(ROOT / 'scripts/prepare_d2_repair.py')})
    (out / 'tasks.json').write_text(json.dumps(previous, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'tasks': len(previous['tasks']), 'same_initial_sources_tests_and_scales': True, 'output': str(out)}))


if __name__ == '__main__':
    main()
