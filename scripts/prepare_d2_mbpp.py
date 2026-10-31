"""Prepare fixed MBPP-derived tasks, visible examples and independent checks."""
import argparse
import ast
import hashlib
import itertools
import json
from pathlib import Path
import random
import re
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / 'configs/prediction/d2_mbpp_round.json'


def additional_checks(task_id):
    rng = random.Random(2300 + task_id)
    checks = []
    if task_id == 2:
        for _ in range(40):
            a = [rng.randrange(-5, 10) for _ in range(rng.randrange(12))]
            b = [rng.randrange(-5, 10) for _ in range(rng.randrange(12))]
            checks.append(f'assert set(similar_elements({a!r}, {b!r})) == {set(a) & set(b)!r}')
    elif task_id == 3:
        for n in [2, 3, 4, 9, 25, 49, 97, 121, 169, 997] + rng.sample(range(2, 900), 40):
            composite = any(n % d == 0 for d in range(2, int(n ** .5) + 1))
            checks.append(f'assert bool(is_not_prime({n})) == {composite}')
    elif task_id == 4:
        for _ in range(40):
            a = [rng.randrange(-50, 50) for _ in range(rng.randrange(15))]
            n = rng.randrange(20)
            checks.append(f'assert heap_queue_largest({a!r}, {n}) == {sorted(a, reverse=True)[:n]!r}')
    elif task_id == 7:
        texts = ['', 'one two six', 'four five', 'alpha,beta;gamma!', 'a_b_c 1234 abc', 'word WORD AbCd']
        texts += [' '.join(''.join(rng.choices('abcxyz', k=rng.randrange(1, 9))) for _ in range(8)) for _ in range(34)]
        for text in texts:
            checks.append(f'assert find_char_long({text!r}) == {re.findall(r"\b\w{4,}\b", text)!r}')
    elif task_id == 8:
        for _ in range(40):
            a = [rng.randrange(-30, 31) for _ in range(rng.randrange(15))]
            checks.append(f'assert square_nums({a!r}) == {[x*x for x in a]!r}')
    elif task_id == 9:
        for text in ['a', 'abab', 'abcabc', 'ababa', 'aaaaab'] + [''.join(t) for n in range(1, 6) for t in itertools.product('ab', repeat=n)]:
            period = next(i for i in range(1, len(text) + 1) if text[i:] + text[:i] == text)
            checks.append(f'assert find_Rotations({text!r}) == {period}')
    elif task_id == 11:
        for _ in range(40):
            s = ''.join(rng.choices('abcxyz', k=rng.randrange(15)))
            ch = rng.choice('abcxyz')
            positions = [i for i, c in enumerate(s) if c == ch]
            remove = {positions[0], positions[-1]} if positions else set()
            expected = ''.join(c for i, c in enumerate(s) if i not in remove)
            checks.append(f'assert remove_Occ({s!r}, {ch!r}) == {expected!r}')
    elif task_id == 12:
        for _ in range(40):
            rows = [[rng.randrange(-5, 6) for _ in range(rng.randrange(5))] for _ in range(rng.randrange(8))]
            checks.append(f'assert sort_matrix({rows!r}) == {sorted(rows, key=sum)!r}')
    elif task_id == 14:
        for _ in range(40):
            a, b, c = [rng.randrange(1, 30) for _ in range(3)]
            checks.append(f'assert find_Volume({a}, {b}, {c}) == {a*b*c/2!r}')
    elif task_id == 16:
        texts = ['', '_', 'a_', '_a', 'a_b', 'aaa_bbb', 'a_b_c', 'A_b', 'a_B', 'ab', 'a_1', 'a b_c']
        texts += [''.join(rng.choices('ab_AB19 ', k=rng.randrange(12))) for _ in range(28)]
        for text in texts:
            checks.append(f'assert text_lowercase_underscore({text!r}) == {bool(re.fullmatch("[a-z]+_[a-z]+", text))}')
    return checks


def tests_module(checks):
    return 'from solution import *\n\n' + '\n\n'.join(
        f'def test_case_{n:03d}():\n    {check}' for n, check in enumerate(checks)) + '\n'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, default=ROOT / '.artifacts/d2-next')
    p.add_argument('--extend-variant', help='Create clean workspaces for an added variant from committed stubs')
    args = p.parse_args()
    dest = args.output.resolve()
    cfg = json.loads(CONFIG.read_text())
    if args.extend_variant:
        if args.extend_variant not in cfg['variants']:
            raise ValueError('variant is not declared in configuration')
        manifest = json.loads((dest / 'tasks.json').read_text())
        for task in manifest['tasks']:
            work = dest / 'workspaces' / args.extend_variant / task['instance_id']
            work.mkdir(parents=True, exist_ok=False)
            for name in ['solution.py', 'README.md', 'test_visible.py', '.gitignore']:
                content = subprocess.check_output(['git', '-C', task['workspaces']['direct'], 'show', 'HEAD:' + name])
                (work / name).write_bytes(content)
            subprocess.run(['git', 'init', '-q', str(work)], check=True)
            subprocess.run(['git', '-C', str(work), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(work), '-c', 'user.name=LeasePath Pilot',
                            '-c', 'user.email=leasepath@localhost', 'commit', '-qm', 'MBPP-derived initial stub'], check=True)
            task['workspaces'][args.extend_variant] = str(work)
        path = dest / f'tasks-{args.extend_variant}.json'
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps({'extended_variant': args.extend_variant, 'tasks': str(path)}))
        return
    source = dest / 'source'
    source.mkdir(parents=True, exist_ok=True)
    path = source / 'sanitized-mbpp.json'
    url = f"https://raw.githubusercontent.com/google-research/google-research/{cfg['task_source_revision']}/mbpp/sanitized-mbpp.json"
    if not path.exists():
        path.write_bytes(urllib.request.urlopen(url, timeout=60).read())
    rows = {t['task_id']: t for t in json.loads(path.read_text())}
    tasks = []
    for task_id in cfg['task_ids']:
        row = rows[task_id]
        functions = [n for n in ast.parse(row['code']).body if isinstance(n, ast.FunctionDef)]
        if len(functions) != 1 or row['test_imports']:
            raise ValueError('selection must contain one function with no test dependencies')
        imports = [n for n in ast.walk(ast.parse(row['code'])) if isinstance(n, (ast.Import, ast.ImportFrom))]
        for n in imports:
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module]
            if any(name not in {'math', 'heapq', 're'} for name in names):
                raise ValueError('unexpected reference dependencies')
        f = functions[0]
        signature = f'def {f.name}({ast.unparse(f.args)}):'
        stub = signature + '\n    raise NotImplementedError("Implement the task")\n'
        visible = tests_module(row['test_list'][:1])
        extra = additional_checks(task_id)
        independent_checks = row['test_list'][1:] + extra
        # Verify added tests against the public reference, outside Agent workspaces.
        scope = {}
        exec(compile(row['code'], '<reference>', 'exec'), scope)
        for check in row['test_list'] + extra:
            exec(compile(check, '<oracle-check>', 'exec'), scope)
        evaluation = dest / 'evaluation' / f'mbpp-{task_id:04d}'
        evaluation.mkdir(parents=True, exist_ok=False)
        hidden = tests_module(independent_checks)
        (evaluation / 'test_independent.py').write_text(hidden)
        clarifications = {3: 'Inputs are integers n >= 2.',
                          7: 'Words are contiguous letters/digits/underscore, as Python regex \\w; preserve their order.',
                          9: 'Input strings are nonempty.',
                          16: 'The entire string must consist of two nonempty lowercase ASCII letter groups separated by one underscore.'}
        statement = row['prompt'] + '\n' + clarifications.get(task_id, '')
        record = {'instance_id': f'mbpp-{task_id:04d}', 'task_id': task_id,
                  'problem_statement': statement, 'function_signature': signature,
                  'source_group': 'google-research/mbpp', 'split': 'pilot_development_only',
                  'evidence_scope': 'mbpp_derived_agent_development',
                  'independent_tests': len(independent_checks), 'visible_tests': 1,
                  'independent_test_sha256': hashlib.sha256(hidden.encode()).hexdigest(),
                  'visible_test_sha256': hashlib.sha256(visible.encode()).hexdigest(),
                  'reference_sha256': hashlib.sha256(row['code'].encode()).hexdigest(),
                  'reference_validated': True, 'workspaces': {}}
        for variant in cfg['variants']:
            work = dest / 'workspaces' / variant / record['instance_id']
            work.mkdir(parents=True, exist_ok=False)
            (work / 'solution.py').write_text(stub)
            (work / 'test_visible.py').write_text(visible)
            (work / 'README.md').write_text(statement + '\n\nImplement ' + signature + '\n')
            (work / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
            subprocess.run(['git', 'init', '-q', str(work)], check=True)
            subprocess.run(['git', '-C', str(work), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(work), '-c', 'user.name=LeasePath Pilot',
                            '-c', 'user.email=leasepath@localhost', 'commit', '-qm', 'MBPP-derived initial stub'], check=True)
            record['workspaces'][variant] = str(work)
        tasks.append(record)
    manifest = {'dataset': cfg['task_dataset'], 'revision': cfg['task_source_revision'], 'source_url': url,
                'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'selection_rule': 'ten predetermined single-function standard-library tasks; IDs frozen before outcomes',
                'protocol': 'benchmark-derived workspaces; one visible published example, remaining published and additional independent tests',
                'limitations': ['development only; public benchmark may be in model training',
                                'not native GitHub issues; not official MBPP pass@1'], 'tasks': tasks}
    (dest / 'tasks.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'tasks': len(tasks), 'independent_tests': sum(t['independent_tests'] for t in tasks),
                      'all_reference_checks_passed': True, 'output': str(dest)}))


if __name__ == '__main__':
    main()
