"""Prepare five genuine SWE-bench issues at their published base commits."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SELECTED = ['sympy__sympy-20590', 'sympy__sympy-21614', 'sympy__sympy-21847',
            'sympy__sympy-23262', 'sympy__sympy-24909']


def prepare(row, destination):
    task = destination / 'workspaces' / row['instance_id']
    if task.exists():
        raise ValueError(f'refusing to overwrite task workspace: {task}')
    url = f"https://codeload.github.com/{row['repo']}/tar.gz/{row['base_commit']}"
    with urllib.request.urlopen(url, timeout=120) as response:
        blob = response.read()
    task.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:gz') as archive:
        for member in archive.getmembers():
            relative = Path(*Path(member.name).parts[1:])
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('unsafe source archive')
            target = task / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError('nonregular source archive entry')
    subprocess.run(['git', 'init', '-q', str(task)], check=True)
    subprocess.run(['git', '-C', str(task), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(task), '-c', 'user.name=LeasePath Pilot',
                    '-c', 'user.email=leasepath@localhost', 'commit', '-qm',
                    f"Pinned upstream {row['base_commit']}"], check=True)
    evaluation = destination / 'evaluation'
    evaluation.mkdir(exist_ok=True)
    (evaluation / (row['instance_id'] + '.test.patch')).write_text(row['test_patch'])
    # Golden code patches are never handed to the agent.
    result = {k: row[k] for k in ['instance_id', 'repo', 'base_commit', 'problem_statement', 'FAIL_TO_PASS', 'PASS_TO_PASS']}
    result.update(workspace=str(task), archive_url=url,
                  archive_sha256=hashlib.sha256(blob).hexdigest(),
                  test_patch_sha256=hashlib.sha256(row['test_patch'].encode()).hexdigest(),
                  source_group=row['repo'], split='pilot_development_only')
    print(json.dumps({'prepared': row['instance_id']}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parquet', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/d2')
    args = parser.parse_args()
    import pyarrow.parquet as pq
    rows = {r['instance_id']: r for r in pq.read_table(args.parquet).to_pylist()}
    args.output.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = list(pool.map(lambda key: prepare(rows[key], args.output), SELECTED))
    (args.output / 'tasks.json').write_text(json.dumps({
        'dataset': 'princeton-nlp/SWE-bench_Lite', 'split': 'test',
        'parquet_sha256': hashlib.sha256(args.parquet.read_bytes()).hexdigest(),
        'selection_rule': 'five fixed Python-only SymPy issues; not selected by model outcomes',
        'limitation': 'single source project; no independent train/test split or generalization claim',
        'tasks': tasks,
    }, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
