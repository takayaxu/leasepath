"""Freeze a completed round, audit all traces and publish quality manifests."""
import argparse
from collections import Counter
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_dataset import export


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--root', type=Path, default=ROOT / '.artifacts/d2-next')
    p.add_argument('--output', type=Path, default=ROOT / 'results/prediction/d2_mbpp_round')
    args = p.parse_args()
    home, out = args.root.resolve(), args.output.resolve()
    profiles = [v for v in ['direct', 'greedy-mini', 'greedy-guided', 'sampled']
                if (home / 'runs' / v / 'results.json').exists()]
    if not {'direct', 'greedy-guided', 'sampled'} <= set(profiles):
        raise ValueError('finish direct, greedy-guided and sampled runs before finalizing')
    runs = {v: read(home / 'runs' / v / 'results.json') for v in profiles}
    evaluations = {v: read(home / 'evaluation-runs' / v / 'results.json') for v in ['baseline'] + profiles}
    cfg = read(ROOT / 'configs/prediction/d2_mbpp_round.json')
    backend = read(ROOT / cfg['backend_manifest'])
    for variant in ['greedy-guided', 'sampled']:
        rm = read(home / 'runs' / variant / 'run-manifest.json')
        if rm['runner_sha256'] != digest(ROOT / 'scripts/run_d2_mbpp.py'):
            raise ValueError('primary runner changed since collection; preserve exact source before finalizing')
        if rm['configuration'] != cfg:
            raise ValueError('primary run configuration changed after collection')
    out.mkdir(parents=True, exist_ok=False)
    frozen = home / 'frozen-round'
    frozen.mkdir(exist_ok=False)
    with (ROOT / cfg['raw_log']).open('rb') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        try:
            with (frozen / 'raw.jsonl').open('wb') as target:
                shutil.copyfileobj(stream, target)
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    for source in [ROOT / cfg['backend_manifest'], ROOT / 'configs/prediction/d2_mbpp_round.json', home / 'tasks.json']:
        shutil.copy2(source, frozen / source.name)
    shutil.copy2(ROOT / '.artifacts/d2/server.log', frozen / 'server.log')
    sources = [*Path(ROOT / 'src/leasepath').glob('d2_*.py'), *Path(ROOT / 'scripts').glob('*d2*.py'),
               ROOT / 'src/leasepath/trace.py', ROOT / 'schemas/trace.schema.json',
               ROOT / 'pyproject.toml', ROOT / cfg['upstream_prompt']]
    source_hashes = {}
    for source in sources:
        relative = source.relative_to(ROOT)
        target = frozen / 'source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        source_hashes[str(relative)] = digest(source)
    for v in profiles:
        shutil.copy2(home / 'runs' / v / 'run-manifest.json', out / f'{v}-run-manifest.json')
        shutil.copy2(home / 'evaluation-runs' / v / 'results.json', out / f'{v}-evaluation.json')
    shutil.copy2(home / 'evaluation-runs/baseline/results.json', out / 'baseline-evaluation.json')
    tasks = read(home / 'tasks.json')
    write(out / 'task-provenance.json', {**{k: v for k, v in tasks.items() if k != 'tasks'},
                                          'tasks': [{k: v for k, v in t.items() if k != 'workspaces'} for t in tasks['tasks']]})
    summaries = {}
    audits = {}
    for v in profiles:
        for run in runs[v]:
            workflow = run['workflow_id']
            try:
                export(frozen / 'raw.jsonl', home / 'datasets/workflow-audits' / workflow,
                       backend['compatibility_id'], workflow_ids=[workflow])
                audits[workflow] = {'passed': True}
            except ValueError as exc:
                audits[workflow] = {'passed': False, 'reason': str(exc),
                                    'handling': 'entire workflow quarantined from derived labels; raw and trajectory retained'}
    write(out / 'backend-audits.json', audits)
    for v in profiles:
        summaries[v] = export(frozen / 'raw.jsonl', home / 'datasets' / v, backend['compatibility_id'],
                              workflow_ids=[r['workflow_id'] for r in runs[v] if audits[r['workflow_id']]['passed']])
        write(out / f'{v}-dataset-summary.json', summaries[v])
    main_runs = runs['greedy-guided'] + runs['sampled']
    by_eval = {r['workflow_id']: r for v in ['greedy-guided', 'sampled'] for r in evaluations[v]['results']}
    quality = []
    for run in main_runs:
        e = by_eval[run['workflow_id']]
        if digest(home / 'runs' / run['variant'] / run['task_id'] / 'solution.py') != e['source_sha256']:
            raise ValueError('candidate source changed after independent evaluation')
        quality.append({'workflow_id': run['workflow_id'], 'task_id': run['task_id'],
                        'variant': run['variant'], 'group_id': 'google-research/mbpp:' + run['task_id'],
                        'split': 'pilot_development_only', 'calls': run['calls'], 'exit_status': run['exit_status'],
                        'tests_passed': e['tests_passed'], 'criteria': {**e['quality_criteria'], 'backend_event_audit': audits[run['workflow_id']]['passed']},
                        'quality_passed': e['execution_quality_passed'] and audits[run['workflow_id']]['passed'], 'candidate_sha256': e['source_sha256']})
    qualified = [r for r in quality if r['quality_passed']]
    if not qualified:
        raise ValueError('no execution satisfies the predeclared quality gate')
    write(out / 'execution-quality.json', {'all_main_executions': len(quality), 'qualified': len(qualified),
                                          'excluded': len(quality) - len(qualified), 'executions': quality})
    qualified_ids = {r['workflow_id'] for r in qualified}
    write(home / 'qualified-results.json', [r for r in main_runs if r['workflow_id'] in qualified_ids])
    summaries['all-main'] = export(frozen / 'raw.jsonl', home / 'datasets/all-main', backend['compatibility_id'],
                                   workflow_ids=[r['workflow_id'] for r in main_runs if audits[r['workflow_id']]['passed']])
    summaries['qualified'] = export(frozen / 'raw.jsonl', home / 'datasets/qualified', backend['compatibility_id'],
                                    workflow_ids=[r['workflow_id'] for r in qualified])
    for v in ['all-main', 'qualified']:
        write(out / f'{v}-dataset-summary.json', summaries[v])
        subprocess.run([str(ROOT / '.venv/bin/python'), str(ROOT / 'scripts/export_d2_hazards.py'),
                        '--dataset', str(home / 'datasets' / v), '--output', str(home / 'hazards' / v)], check=True)
        shutil.copy2(home / 'hazards' / v / 'summary.json', out / f'{v}-hazard-summary.json')
    raw = [json.loads(l) for l in (frozen / 'raw.jsonl').read_text().splitlines()]
    main_ids = {r['workflow_id'] for r in main_runs}
    tool_rows = [r for r in raw if r['kind'] == 'tool_start' and r.get('workflow_id') in main_ids]
    rejected = [r for r in raw if r['kind'] == 'action_rejected' and r.get('workflow_id') in main_ids]
    def optional(path, default=None):
        return read(path) if path.exists() else default

    controls = {'completed_original_interface_failures': optional(home / 'runs/greedy/partial-results.json', []),
                'original_interface_stop': optional(home / 'diagnostic-stop.json'),
                'explicit_interface_stop': optional(home / 'explicit-diagnostic-stop.json'),
                'note': 'Stopped diagnostic traces are retained in raw/trajectories and never relabeled as successful executions.'}
    write(out / 'calibration-records.json', controls)
    summary = {'status': 'completed', 'dataset_scope': 'guided MBPP-derived development executions; not native repository issues',
               'unique_tasks': len(tasks['tasks']), 'main_executions': len(main_runs),
               'qualified_executions': len(qualified), 'qualified_unique_tasks': len({r['task_id'] for r in qualified}),
               'quality_failures_retained': len(quality) - len(qualified),
               'evaluation': {v: {k: evaluations[v][k] for k in ['tasks', 'tests_passed_tasks', 'execution_quality_passed_tasks']} for v in evaluations},
               'qualified_dataset': {k: summaries['qualified'][k] for k in ['events', 'runs', 'idle_episodes', 'requests', 'snapshots', 'outcomes', 'cache_hit_requests']},
               'all_main_dataset': {k: summaries['all-main'][k] for k in ['events', 'runs', 'idle_episodes', 'requests', 'snapshots', 'outcomes']},
               'main_tool_calls': len(tool_rows), 'protocol_feedback': dict(Counter(r['reason'] for r in rejected)),
               'all_main_backend_audits_passed': all(audits[r['workflow_id']]['passed'] for r in main_runs),
               'auditable_main_executions': sum(audits[r['workflow_id']]['passed'] for r in main_runs),
               'quarantined_main_workflows': [r['workflow_id'] for r in main_runs if not audits[r['workflow_id']]['passed']],
               'qualified_backend_audits_passed': True, 'raw_sha256': digest(frozen / 'raw.jsonl'),
               'source_sha256': source_hashes, 'qualified_local_path': str(home / 'datasets/qualified'),
               'raw_local_path': str(frozen / 'raw.jsonl'),
               'limitations': ['all data are development-only; same task across profiles belongs to one group',
                              'qualified subset is conditioned on successful execution; not an unbiased workload distribution',
                              'runner enforces initial inspection and current-source visible testing; guided protocol',
                              'short single-file tasks do not cover large repositories, slow builds or parallel dependencies',
                              'no full observer overhead or online policy benefit claim',
                              'no natural censoring required; administrative views are explicitly separate']}
    write(out / 'summary.json', summary)
    write(out / 'SHA256SUMS.json', {p.name: digest(p) for p in sorted(out.iterdir()) if p.is_file()})
    print(json.dumps({k: summary[k] for k in ['status', 'unique_tasks', 'main_executions', 'qualified_executions', 'qualified_dataset']}))


if __name__ == '__main__':
    main()
