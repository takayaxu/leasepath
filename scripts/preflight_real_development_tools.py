"""CPU validation of private environments, using fresh frozen source clones."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from leasepath.real_environment import prepare_environment, preflight_environment

parser = argparse.ArgumentParser(description=__doc__)
for name in ['tasks', 'environments', 'output']:
    parser.add_argument('--' + name, type=Path, required=True)
args = parser.parse_args()
args.output = args.output.absolute()
args.output.mkdir(exist_ok=False, parents=True)
results = []
for task in json.loads(args.tasks.read_text())['tasks']:
    target = args.output / task['instance_id']
    target.mkdir()
    work = target / 'workspace'
    subprocess.run(['git', 'clone', '-q', '--shared', task['workspace'], str(work)], check=True)
    template = args.environments / ('flask' if task['repo'] == 'pallets/flask' else 'pytest-py39')
    env, wrappers = prepare_environment(work, template, target / 'environment', task['repo'])
    evidence = preflight_environment(work, env, wrappers, task['repo'], cpus=sorted(os.sched_getaffinity(0))[-4:])
    (target / 'environment.json').write_text(json.dumps(evidence, indent=2) + '\n')
    results.append({'task_id': task['instance_id'], 'passed': True, 'evidence': evidence})
    print(task['instance_id'], 'passed', flush=True)
(args.output / 'summary.json').write_text(json.dumps(results, indent=2) + '\n')
