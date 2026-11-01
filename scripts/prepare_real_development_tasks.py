"""Freeze stage-0 genuine issue workspaces; no model invocation or golden solution."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from prepare_d2_tasks import prepare

SELECTED = ['pallets__flask-4992', 'pallets__flask-5063', 'pytest-dev__pytest-9359']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parquet', type=Path, required=True)
    parser.add_argument('--dataset-revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task-id', action='append', help='Explicit preselected new issues; default is the original pilot')
    args = parser.parse_args()
    import pyarrow.parquet as pq
    rows = {r['instance_id']: r for r in pq.read_table(args.parquet).to_pylist()}
    args.output.mkdir(parents=True, exist_ok=False)
    tasks = []
    selected = args.task_id or SELECTED
    if len(selected) != len(set(selected)):
        raise ValueError('duplicate selected issue')
    for key in selected:
        task = prepare(rows[key], args.output)
        # Archives contain tracked upstream fixtures which upstream .gitignore
        # also matches (Flask's .env/.flaskenv). Preserve every archive member.
        subprocess.run(['git', '-C', task['workspace'], 'add', '--force', '.'], check=True)
        subprocess.run(['git', '-C', task['workspace'], '-c', 'user.name=LeasePath Pilot',
                        '-c', 'user.email=leasepath@localhost', 'commit', '--amend',
                        '--no-edit', '--quiet'], check=True)
        workspace = Path(task['workspace'])
        files = {str(p.relative_to(workspace)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(workspace.rglob('*')) if p.is_file() and '.git' not in p.relative_to(workspace).parts}
        manifest = args.output / 'evaluation' / (key + '.source-files.json')
        manifest.write_text(json.dumps(files, indent=2) + '\n')
        task['source_files_manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        task['source_file_count'] = len(files)
        task['source_group'] = key
        task['project_group'] = task['repo']
        task['split'] = 'stage0_integration_only_not_final_test'
        task['agent_execution_status'] = 'not_started'
        task['test_nodeids'] = json.loads(task['FAIL_TO_PASS'])
        tasks.append(task)
    result = {
        'dataset': 'princeton-nlp/SWE-bench_Lite', 'split': 'test',
        'dataset_revision': args.dataset_revision,
        'parquet_sha256': hashlib.sha256(args.parquet.read_bytes()).hexdigest(),
        'selection_rule': ('Explicit issue IDs selected before their model outcomes; see remediation preregistration; environment failures retained.'
                           if args.task_id else 'Fixed three real CLI/config/assertion issues in two Python repositories before any new model outcomes; environment failures retained.'),
        'source_preservation': 'All regular files from upstream archive forcibly tracked, including ignored test fixtures.',
        'limitations': ['Public benchmark may have appeared in pretraining.',
                       'Integration pilot only; no natural-session or independent-confirmation claim.',
                       'Source archive hash, not synthetic local git commit, identifies upstream.'],
        'tasks': tasks,
    }
    (args.output / 'tasks.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'tasks': len(tasks), 'repositories': len({t['repo'] for t in tasks})}))


if __name__ == '__main__':
    main()
