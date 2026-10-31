"""Freeze six designed file-processing tasks and calibrate real test workloads.

Reference implementations stay outside agent workspaces. Calibration selects
input size before any agent outcomes; no synthetic delays or repeated busy work.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]

TASKS = [
    dict(name='csv-ledger', family='csv_aggregation', unit='rows', sizes=[250000, 500000, 1000000, 2000000],
         signature='def summarize_csv(path):',
         statement='Read a UTF-8 CSV with header tenant,cents using the csv module (quoted tenant names are valid). Return a dict mapping each tenant string to the sum of its signed integer cents. Empty data returns {}. Do not modify the file.',
         reference='''import csv
def summarize_csv(path):
    totals = {}
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            k = row['tenant']
            totals[k] = totals.get(k, 0) + int(row['cents'])
    return totals
''', visible='''    p = tmp_path / 'ledger.csv'
    p.write_text('tenant,cents\\n"a,b",4\\nx,-2\\n"a,b",7\\n')
    assert summarize_csv(p) == {'a,b': 11, 'x': -2}
''', workload='''    p = tmp_path / 'ledger.csv'
    expected = {}
    with p.open('w') as f:
        f.write('tenant,cents\\n')
        for i in range(N):
            key, cents = f't{i % 97}', i % 201 - 100
            f.write(f'{key},{cents}\\n')
            expected[key] = expected.get(key, 0) + cents
    assert summarize_csv(p) == expected
''', independent='''    import csv, random
    rng = random.Random(711)
    for size in [0, 1, 97, 701]:
        p = tmp_path / 'edge.csv'
        totals = {}
        with p.open('w', newline='') as f:
            w = csv.writer(f); w.writerow(['tenant', 'cents'])
            for _ in range(size):
                k, v = rng.choice(['', 'a,b', '你好', 'a"b']), rng.randint(-10000, 10000)
                w.writerow([k, v]); totals[k] = totals.get(k, 0) + v
        assert summarize_csv(p) == totals
'''),
    dict(name='jsonl-status', family='jsonl_parsing', unit='records', sizes=[150000, 300000, 600000, 1200000],
         signature='def count_statuses(path):',
         statement='Read a UTF-8 JSON Lines log. Ignore blank/whitespace lines. Every other line is a valid JSON object with a string status field and optional other fields. Return a dict mapping each status to its record count. Empty data returns {}. Do not modify the file.',
         reference='''import json
def count_statuses(path):
    counts = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                k = json.loads(line)['status']
                counts[k] = counts.get(k, 0) + 1
    return counts
''', visible='''    p = tmp_path / 'log.jsonl'
    p.write_text('{"status":"ok","x":1}\\n\\n{"status":"fail"}\\n{"status":"ok"}\\n')
    assert count_statuses(p) == {'ok': 2, 'fail': 1}
''', workload='''    import json
    p = tmp_path / 'log.jsonl'
    with p.open('w') as f:
        for i in range(N):
            f.write(json.dumps({'status': f's{i % 11}', 'id': i, 'message': 'worker completed item'}) + '\\n')
    assert count_statuses(p) == {f's{k}': N // 11 + (k < N % 11) for k in range(11)}
''', independent='''    import json
    for values in [[], [''], ['ok', 'ok', 'failed'], ['你好', 'a"b', '你好']]:
        p = tmp_path / 'edge.jsonl'
        p.write_text('  \\n' + '\\n\\n'.join(json.dumps({'status': v}) for v in values))
        assert count_statuses(p) == {v: values.count(v) for v in set(values)}
'''),
    dict(name='sqlite-sales', family='sqlite_query', unit='rows', sizes=[250000, 500000, 1000000, 2000000],
         signature='def sales_totals(path):',
         statement='Open the existing SQLite database at path. Its table sales(region TEXT NOT NULL, cents INTEGER NOT NULL) holds signed integer amounts. Return a dict of summed cents per region (region strings, integer totals). Empty table returns {}. Close your connection; do not modify the database.',
         reference='''import sqlite3
def sales_totals(path):
    c = sqlite3.connect(path)
    try:
        return dict(c.execute('SELECT region, SUM(cents) FROM sales GROUP BY region'))
    finally:
        c.close()
''', visible='''    import sqlite3
    p = tmp_path / 'sales.db'
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE sales(region TEXT NOT NULL, cents INTEGER NOT NULL)')
        c.executemany('INSERT INTO sales VALUES (?, ?)', [('west', 5), ('east', -2), ('west', 8)])
    assert sales_totals(p) == {'west': 13, 'east': -2}
''', workload='''    import sqlite3
    p = tmp_path / 'sales.db'
    expected = {}
    def rows():
        for i in range(N):
            k, v = f'r{i % 53}', i % 101 - 50
            expected[k] = expected.get(k, 0) + v
            yield k, v
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE sales(region TEXT NOT NULL, cents INTEGER NOT NULL)')
        c.executemany('INSERT INTO sales VALUES (?, ?)', rows())
    assert sales_totals(p) == expected
''', independent='''    import sqlite3
    for j, rows in enumerate([[], [('', 0)], [('a', -4), ('a', 4), ('b', 7)], [('你好', 2**40), ('你好', 9)]]):
        p = tmp_path / f'{j}.db'
        with sqlite3.connect(p) as c:
            c.execute('CREATE TABLE sales(region TEXT NOT NULL, cents INTEGER NOT NULL)')
            c.executemany('INSERT INTO sales VALUES (?, ?)', rows)
        expected = {k: sum(v for key, v in rows if key == k) for k, _ in rows}
        assert sales_totals(p) == expected
'''),
    dict(name='gzip-digest', family='gzip_hashing', unit='bytes', sizes=[33554432, 67108864, 134217728, 268435456],
         signature='def gzip_sha256(path):',
         statement='Return the lowercase hexadecimal SHA-256 digest of the DECOMPRESSED contents of an existing gzip file at path. Binary data and empty contents are valid. Use the standard library and do not modify the file.',
         reference='''import gzip, hashlib
def gzip_sha256(path):
    h = hashlib.sha256()
    with gzip.open(path, 'rb') as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            h.update(block)
    return h.hexdigest()
''', visible='''    import gzip, hashlib
    p = tmp_path / 'blob.gz'
    with gzip.open(p, 'wb') as f:
        f.write(b'hello\\x00world')
    assert gzip_sha256(p) == hashlib.sha256(b'hello\\x00world').hexdigest()
''', workload='''    import gzip, hashlib, random
    p = tmp_path / 'blob.gz'
    data = random.Random(711).randbytes(N)
    with gzip.open(p, 'wb', compresslevel=6) as f:
        f.write(data)
    assert gzip_sha256(p) == hashlib.sha256(data).hexdigest()
''', independent='''    import gzip, hashlib
    for data in [b'', b'x', bytes(range(256)) * 101, b'long' * 100000]:
        p = tmp_path / 'edge.gz'
        with gzip.open(p, 'wb') as f:
            f.write(data)
        assert gzip_sha256(p) == hashlib.sha256(data).hexdigest()
'''),
    dict(name='integer-sort', family='integer_sorting', unit='lines', sizes=[500000, 1000000, 2000000, 4000000],
         signature='def sort_integer_file(source, destination):',
         statement='Read a UTF-8 text file source containing one signed integer per nonblank line. Ignore blank lines. Write these integers in numeric ascending order to destination, normalized as decimal integers, one per line with a trailing newline. Preserve duplicates; for empty input write an empty file. Return the number written. Do not modify source; inputs fit in memory.',
         reference='''def sort_integer_file(source, destination):
    with open(source, encoding='utf-8') as f:
        values = sorted(int(line) for line in f if line.strip())
    with open(destination, 'w', encoding='utf-8') as f:
        for v in values:
            f.write(str(v) + '\\n')
    return len(values)
''', visible='''    p, out = tmp_path / 'in.txt', tmp_path / 'out.txt'
    p.write_text('10\\n-2\\n\\n10\\n1\\n')
    assert sort_integer_file(p, out) == 4
    assert out.read_text() == '-2\\n1\\n10\\n10\\n'
''', workload='''    p, out = tmp_path / 'in.txt', tmp_path / 'out.txt'
    with p.open('w') as f:
        for i in range(N, 0, -1):
            f.write(str(i) + '\\n')
    assert sort_integer_file(p, out) == N
    with out.open() as f:
        for expected, line in enumerate(f, 1):
            assert line == str(expected) + '\\n'
        assert expected == N
''', independent='''    import random
    rng = random.Random(711)
    for values in [[], [0], [10, 2, -100, 10], [rng.randrange(-99, 100) for _ in range(701)]]:
        p, out = tmp_path / 'in', tmp_path / 'out'
        text = '\\n \\n'.join(map(str, values)) + '\\n'
        p.write_text(text)
        assert sort_integer_file(p, out) == len(values)
        assert out.read_text() == ''.join(str(x) + '\\n' for x in sorted(values))
        assert p.read_text() == text
'''),
    dict(name='graph-distance', family='graph_bfs', unit='edges', sizes=[150000, 300000, 600000, 1200000],
         signature='def shortest_distance(path, start, target):',
         statement='Read an undirected unweighted graph from a UTF-8 edge-list file path: each nonblank line has two integer vertex IDs separated by whitespace. Duplicates and self-loops are valid. Return the minimum edge count from integer start to target, or None if unreachable. Return 0 when start == target even if absent. Do not modify the file.',
         reference='''from collections import defaultdict, deque
def shortest_distance(path, start, target):
    if start == target:
        return 0
    adj = defaultdict(list)
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip():
                a, b = map(int, line.split())
                adj[a].append(b); adj[b].append(a)
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        a, d = queue.popleft()
        for b in adj[a]:
            if b == target:
                return d + 1
            if b not in seen:
                seen.add(b); queue.append((b, d + 1))
    return None
''', visible='''    p = tmp_path / 'edges.txt'
    p.write_text('0 1\\n1 2\\n0 2\\n2 3\\n8 9\\n')
    assert shortest_distance(p, 0, 3) == 2
    assert shortest_distance(p, 0, 9) is None
''', workload='''    p = tmp_path / 'chain.txt'
    with p.open('w') as f:
        for i in range(N):
            f.write(f'{i} {i+1}\\n')
    assert shortest_distance(p, 0, N) == N
''', independent='''    import random
    rng = random.Random(711)
    for size in [0, 1, 7, 19]:
        edges = [(a, b) for a in range(size) for b in range(size) if rng.random() < .18]
        p = tmp_path / 'edges'
        p.write_text('\\n\\n'.join(f'{a} {b}' for a, b in edges))
        dist = [[0 if a == b else 999 for b in range(size + 1)] for a in range(size + 1)]
        for a, b in edges:
            dist[a][b] = dist[b][a] = min(dist[a][b], 1)
        for k in range(size + 1):
            for a in range(size + 1):
                for b in range(size + 1):
                    dist[a][b] = min(dist[a][b], dist[a][k] + dist[k][b])
        for a in range(size + 1):
            for b in range(size + 1):
                assert shortest_distance(p, a, b) == (None if dist[a][b] == 999 else dist[a][b])
'''),
]


def module(body, name):
    return 'from solution import *\n\ndef test_' + name + '(tmp_path):\n' + body


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/d2-expansion-v1')
    args = parser.parse_args()
    home = args.output.resolve()
    home.mkdir(parents=True, exist_ok=False)
    records, calibration = [], []
    for spec in TASKS:
        name = 'designed-' + spec['name']
        evaluation = home / 'evaluation' / name
        evaluation.mkdir(parents=True)
        (evaluation / 'solution.py').write_text(spec['reference'])
        visible = module(spec['visible'], 'visible')
        independent = module(spec['independent'], 'independent')
        (evaluation / 'test_visible.py').write_text(visible)
        (evaluation / 'test_independent.py').write_text(independent)
        attempts = []
        for size in spec['sizes']:
            workload = f'N = {size}\n' + module(spec['workload'], 'workload')
            (evaluation / 'test_workload.py').write_text(workload)
            start = time.monotonic()
            run = subprocess.run([str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q',
                                  'test_workload.py'], cwd=evaluation, capture_output=True, text=True,
                                 env={**os.environ, 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}, timeout=60)
            elapsed = time.monotonic() - start
            attempts.append({'units': size, 'elapsed_s': elapsed, 'returncode': run.returncode})
            if run.returncode:
                raise ValueError(run.stdout + run.stderr)
            if elapsed >= 1.5:
                break
        checks = subprocess.run([str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q',
                                 'test_visible.py', 'test_independent.py'], cwd=evaluation,
                                capture_output=True, text=True, timeout=60)
        if checks.returncode:
            raise ValueError(checks.stdout + checks.stderr)
        statement = spec['statement'] + (
            '\nThis is a designed data-processing development task, not a benchmark or production trace. '
            'Read test_workload.py as well as the visible example. The workload is a real '
            f'{spec["family"]} integration test with {size} {spec["unit"]}. '
            'Run exactly `python -m pytest -q test_visible.py test_workload.py` before submitting. '
            'Do not modify either test or skip its workload. Only solution.py is submitted.')
        readme = statement + '\n\nImplement ' + spec['signature'] + '\n'
        work = home / 'workspaces' / 'greedy-guided' / name
        work.mkdir(parents=True)
        files = {'solution.py': spec['signature'] + '\n    raise NotImplementedError("Implement the task")\n',
                 'README.md': readme, 'test_visible.py': visible, 'test_workload.py': workload,
                 '.gitignore': '__pycache__/\n.pytest_cache/\n'}
        for filename, content in files.items():
            (work / filename).write_text(content)
        # Verify integration tests do call the implementation, before any agent run.
        stub = subprocess.run([str(ROOT / '.venv/bin/python'), '-m', 'pytest', '-q',
                               'test_workload.py'], cwd=work, capture_output=True, text=True, timeout=60)
        (evaluation / 'stub-negative-control.log').write_text(stub.stdout + stub.stderr)
        if stub.returncode != 1 or 'NotImplementedError' not in stub.stdout:
            raise ValueError('workload negative control did not reject stub')
        subprocess.run(['git', 'init', '-q', str(work)], check=True)
        subprocess.run(['git', '-C', str(work), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(work), '-c', 'user.name=LeasePath Pilot',
                        '-c', 'user.email=leasepath@localhost', 'commit', '-qm', 'Designed task initial stub'], check=True)
        record = {'instance_id': name, 'problem_statement': statement,
                  'function_signature': spec['signature'], 'source_group': 'leasepath/designed-file-tasks-v1',
                  'split': 'pilot_development_only', 'evidence_scope': 'designed_real_compute_agent_execution',
                  'workload_family': spec['family'], 'work_units': size, 'work_unit': spec['unit'],
                  'workspaces': {'greedy-guided': str(work)}, 'visible_tests': 2, 'independent_tests': 1,
                  'visible_test_sha256': sha(work / 'test_visible.py'),
                  'independent_test_sha256': sha(evaluation / 'test_independent.py'),
                  'protected_sha256': {f: sha(work / f) for f in files if f != 'solution.py'},
                  'reference_sha256': sha(evaluation / 'solution.py'), 'reference_validated': True}
        records.append(record)
        calibration.append({'task_id': name, 'attempts': attempts, 'selected_units': size,
                            'reference_checks_passed': True, 'stub_rejected': True})
        print(json.dumps(calibration[-1]), flush=True)
    (home / 'tasks.json').write_text(json.dumps({'dataset': 'designed-file-tasks-v1',
        'selection': 'six predeclared different operations; deterministic real workloads; no outcome selection',
        'calibration_rule': 'first predeclared size with reference pytest wall time >=1.5s, else largest; cap60s',
        'source_sha256': sha(Path(__file__)), 'tasks': records}, indent=2) + '\n')
    (home / 'calibration.json').write_text(json.dumps(calibration, indent=2) + '\n')


if __name__ == '__main__':
    main()
