"""CPU-only issue environment checks, isolated from future agent workspaces.

Optional reference patches validate tests only. They never enter agent inputs.
This is a local issue-level check, not the official SWE-bench harness.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', type=Path, required=True)
    parser.add_argument('--environments', type=Path, required=True)
    parser.add_argument('--pytest-python', type=Path, help='Optional issue-compatible pytest interpreter')
    parser.add_argument('--reference-parquet', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    import pyarrow.parquet as pq
    references = {r['instance_id']: r['patch'] for r in pq.read_table(args.reference_parquet).to_pylist()}
    tasks = json.loads(args.tasks.read_text())['tasks']
    cpus = sorted(os.sched_getaffinity(0))[:4]
    results = []
    for task in tasks:
        name = task['instance_id']
        python = args.environments.resolve() / ('flask' if task['repo'] == 'pallets/flask' else 'pytest') / 'bin/python'
        if task['repo'] == 'pytest-dev/pytest' and args.pytest_python:
            # Preserve the venv path: resolving its symlink selects system
            # Python and silently loses the isolated dependency environment.
            python = args.pytest_python.absolute()
        interpreter = subprocess.run([str(python), '--version'], capture_output=True, text=True, check=True).stdout.strip()
        patch = args.tasks.resolve().parent / 'evaluation' / (name + '.test.patch')
        source_manifest = args.tasks.resolve().parent / 'evaluation' / (name + '.source-files.json')
        if hashlib.sha256(source_manifest.read_bytes()).hexdigest() != task['source_files_manifest_sha256']:
            raise ValueError('source-file manifest differs from task freeze')
        source_files = json.loads(source_manifest.read_text())
        modes = {}
        for mode in ['baseline', 'reference']:
            work = args.output / f'{name}-{mode}'
            subprocess.run(['git', 'clone', '-q', '--shared', task['workspace'], str(work)], check=True)
            for relative, expected in source_files.items():
                path = work / relative
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError(f'clone lost or changed upstream file: {relative}')
            subprocess.run(['git', '-C', str(work), 'apply', str(patch)], check=True)
            if mode == 'reference':
                subprocess.run(['git', '-C', str(work), 'apply', '-'], input=references[name], text=True, check=True)
            files = sorted({n.split('::')[0] for n in task['test_nodeids']})
            junit = work / 'issue-check.xml'
            env = {**os.environ, 'PYTHONPATH': str(work / 'src'),
                   'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'OMP_NUM_THREADS': '1',
                   'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'}
            preparation = None
            if task['repo'] == 'pytest-dev/pytest':
                # Standard build generates src/_pytest/_version.py. This is
                # package metadata, never a manual change to an issue solution.
                env['SETUPTOOLS_SCM_PRETEND_VERSION'] = '6.3.0.dev0'
                built = subprocess.run([str(python), '-m', 'pip', 'install', '--no-deps',
                                        '--no-build-isolation', '--no-index', '.'], cwd=work,
                                       env=env, capture_output=True, text=True, timeout=120)
                (args.output / f'{name}-{mode}-build.log').write_text(built.stdout + built.stderr)
                preparation = {'returncode': built.returncode, 'version': '6.3.0.dev0'}
            command = ['taskset', '-c', ','.join(map(str, cpus)), str(python), '-m', 'pytest',
                       '-q', *files, '--junitxml', str(junit)]
            if task['repo'] == 'pallets/flask':
                # Python 3.12 deprecates these stdlib APIs used by old Flask.
                # Keep all other upstream warnings-as-errors rules intact.
                for api in ['get_loader', 'find_loader']:
                    command += ['-W', f"ignore:'pkgutil.{api}' is deprecated:DeprecationWarning"]
            start = time.monotonic()
            try:
                run = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=180)
                code, log = run.returncode, run.stdout + run.stderr
            except subprocess.TimeoutExpired as exc:
                code, log = -1, str(exc)
            (args.output / f'{name}-{mode}.log').write_text(log)
            cases = []
            if junit.exists():
                for c in ET.parse(junit).iter('testcase'):
                    status = ('error' if c.find('error') is not None else 'failed' if c.find('failure') is not None
                              else 'skipped' if c.find('skipped') is not None else 'passed')
                    cases.append({'name': c.attrib['name'], 'status': status})
            targets = {node: [c['status'] for c in cases if c['name'] == node.split('::')[-1]]
                       for node in task['test_nodeids']}
            modes[mode] = {'returncode': code, 'elapsed_s': time.monotonic() - start,
                           'command': command, 'targets': targets, 'package_preparation': preparation,
                           'counts': {s: sum(c['status'] == s for c in cases)
                                      for s in ['passed', 'failed', 'error', 'skipped']}}
        ready = (modes['baseline']['returncode'] == 1 and
                 all(s == ['failed'] for s in modes['baseline']['targets'].values()) and
                 modes['baseline']['counts']['error'] == 0 and
                 modes['reference']['returncode'] == 0 and
                 all(v['package_preparation'] is None or v['package_preparation']['returncode'] == 0
                     for v in modes.values()) and
                 all(s == ['passed'] for s in modes['reference']['targets'].values()))
        results.append({'task_id': name, 'repo': task['repo'], 'environment_ready': ready,
                        'agent_execution_status': 'not_started', 'interpreter': interpreter,
                        'source_files_verified_per_clone': len(source_files), 'modes': modes})
        print(json.dumps({'task_id': name, 'environment_ready': ready,
                          'counts': {m: v['counts'] for m, v in modes.items()}}), flush=True)
    summary = {'scope': 'CPU_environment_and_reference_checks_not_agent_success',
               'cpu_affinity': cpus, 'thread_limit': 1, 'results': results,
               'tasks_sha256': hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
               'limitations': ['Local per-task interpreters, not official SWE-bench container harness.',
                              'Reference code used only in isolated evaluation clones.',
                              'Original agent workspaces unmodified.']}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    raise SystemExit(0 if all(r['environment_ready'] for r in results) else 1)


if __name__ == '__main__':
    main()
