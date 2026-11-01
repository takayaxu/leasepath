"""Evaluate saved Agent patches in fresh clones; never supply a reference fix."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from leasepath.real_environment import prepare_environment, preflight_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['tasks', 'environments', 'pilot', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    args.output = args.output.absolute()
    args.output.mkdir(parents=True, exist_ok=False)
    cpus = sorted(os.sched_getaffinity(0))[:4]
    results = []
    for task in json.loads(args.tasks.read_text())['tasks']:
        name = task['instance_id']
        patch = args.pilot / name / 'agent.patch'
        if (args.pilot / name / 'agent.complete.patch').exists():
            patch = args.pilot / name / 'agent.complete.patch'
        target = args.output / name
        target.mkdir()
        work = target / 'workspace'
        subprocess.run(['git', 'clone', '-q', '--shared', task['workspace'], str(work)], check=True)
        sources = args.tasks.parent / 'evaluation' / (name + '.source-files.json')
        if hashlib.sha256(sources.read_bytes()).hexdigest() != task['source_files_manifest_sha256']:
            raise ValueError('frozen source manifest changed')
        for relative, digest in json.loads(sources.read_text()).items():
            if hashlib.sha256((work / relative).read_bytes()).hexdigest() != digest:
                raise ValueError('fresh source differs from frozen upstream')
        # Validate the frozen environment before candidate edits. A candidate
        # SyntaxError is an evaluation failure, not a broken baseline import.
        template = args.environments / ('flask' if task['repo'] == 'pallets/flask' else 'pytest-py39')
        env, wrappers = prepare_environment(work, template, target/'environment', task['repo'])
        evidence = preflight_environment(work, env, wrappers, task['repo'], cpus=cpus)
        (target/'environment.json').write_text(json.dumps(evidence,indent=2)+'\n')
        python = target/'environment/bin/python'
        preparation = 0
        # Evaluate production changes against independently frozen tests.
        # Agent-added regression tests stay in the original patch for review,
        # but cannot replace or weaken this acceptance suite.
        patch_paths = (subprocess.check_output(['git', 'apply', '--numstat', str(patch.absolute())],
                                              cwd=work, text=True).splitlines() if patch.stat().st_size else [])
        altered_tests = [line.split('\t')[-1] for line in patch_paths
                         if line.split('\t')[-1].startswith(('tests/', 'testing/'))]
        production_paths = [line.split('\t')[-1] for line in patch_paths
                            if not line.split('\t')[-1].startswith(('tests/', 'testing/'))]
        candidate = (subprocess.run(['git', 'apply', '--exclude=tests/*', '--exclude=testing/*',
                                    str(patch.absolute())], cwd=work,
                                    capture_output=True, text=True) if production_paths else
                     subprocess.CompletedProcess(['empty_patch_noop'], 0, stdout='', stderr=''))
        test_patch = args.tasks.parent / 'evaluation' / (name + '.test.patch')
        tests = subprocess.run(['git', 'apply', str(test_patch.absolute())], cwd=work,
                               capture_output=True, text=True)
        files = sorted({n.split('::')[0] for n in task['test_nodeids']})
        junit = work / 'issue-check.xml'
        command = ['taskset', '-c', ','.join(map(str, cpus)), str(python), '-m', 'pytest',
                   '-q', *files, '--junitxml', str(junit)]
        if task['repo'] == 'pallets/flask':
            for api in ['get_loader', 'find_loader']:
                command += ['-W', f"ignore:'pkgutil.{api}' is deprecated:DeprecationWarning"]
        start = time.monotonic()
        try:
            run = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=180)
            code, output = run.returncode, run.stdout + run.stderr
        except subprocess.TimeoutExpired as exc:
            code, output = -1, str(exc)
        (work / 'tests.log').write_text(output)
        cases = []
        if junit.exists():
            for c in ET.parse(junit).iter('testcase'):
                status = ('error' if c.find('error') is not None else 'failed' if c.find('failure') is not None
                          else 'skipped' if c.find('skipped') is not None else 'passed')
                cases.append({'name': c.attrib['name'], 'status': status})
        targets = {n: [c['status'] for c in cases if c['name'] == n.split('::')[-1]] for n in task['test_nodeids']}
        results.append({'task_id': name, 'patch_sha256': hashlib.sha256(patch.read_bytes()).hexdigest(),
                        'candidate_apply_returncode': candidate.returncode, 'test_apply_returncode': tests.returncode,
                        'apply_errors': candidate.stderr + tests.stderr, 'candidate_test_changes_excluded': altered_tests,
                        'build_returncode': preparation, 'test_returncode': code, 'targets': targets,
                        'counts': {s: sum(c['status'] == s for c in cases) for s in ['passed', 'failed', 'error', 'skipped']},
                        'command': command, 'elapsed_s': time.monotonic() - start,
                        'passed': (candidate.returncode == tests.returncode == code == 0
                                   and preparation in (None, 0) and all(v == ['passed'] for v in targets.values()))})
    summary = {'scope': 'independent_local_issue_file_tests_not_official_SWE_bench_harness',
               'reference_fix_used': False, 'cpu_affinity': cpus, 'results': results,
               'passed': sum(r['passed'] for r in results), 'total': len(results)}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
