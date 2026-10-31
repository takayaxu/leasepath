"""Run real SWE-bench tasks with mini-swe-agent and observed local commands."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_raw import writer


SYSTEM = '''You are a coding agent fixing a real issue in the current repository.
Inspect the code, reproduce the issue, implement a minimal fix, and run relevant tests.
Each response MUST contain exactly one shell command in this format:
```mswea_bash_command
your command here
```
Use concise analysis. Commands run in fresh shells; directory changes do not persist.
Do not install packages or access other task workspaces. The configured Python is available.
When finished, run only: echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
'''
INSTANCE = '''Fix the following issue in the current repository:
{{task}}

First inspect the relevant files, then reproduce and fix the bug. Run a targeted
test or reproduction before submitting. Do not modify tests to hide failures.
'''


def category(command):
    if any(word in command for word in ('pytest', 'bin/test', 'unittest')):
        return 'test'
    if any(word in command for word in ('compile', 'build', 'setup.py')):
        return 'build'
    if any(word in command for word in ('write_text', "sed -i", ' >', 'open(')):
        return 'edit'
    if any(word in command for word in ('grep', 'rg ', 'cat ', 'sed ', 'ls', 'find ')):
        return 'inspect'
    return 'shell'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tasks', type=Path, default=ROOT / '.artifacts/d2/tasks.json')
    parser.add_argument('--output', type=Path, default=ROOT / '.artifacts/d2/pilot')
    parser.add_argument('--task-id', help='Run one selected task only')
    args = parser.parse_args()
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
    import litellm
    import requests
    config = json.loads((ROOT / 'configs/prediction/d2_first_round.json').read_text())
    installed_commit = subprocess.check_output(['git', '-C', str(ROOT / '.artifacts/mini-swe-agent'),
                                                'rev-parse', 'HEAD'], text=True).strip()
    if installed_commit != config['mini_swe_agent_commit']:
        raise ValueError('mini-swe-agent revision differs from the frozen configuration')
    manifest = json.loads((ROOT / '.artifacts/d2/run-manifest.json').read_text())
    tasks = json.loads(args.tasks.read_text())['tasks']
    args.output.mkdir(parents=True, exist_ok=True)
    log = writer(str(ROOT / '.artifacts/d2/raw.jsonl'))
    http = requests.Session()
    http.trust_env = False
    base = f"http://{config['host']}:{config['port']}"
    http.get(base + '/health', timeout=5).raise_for_status()
    results = []
    for task in tasks:
        if args.task_id and task['instance_id'] != args.task_id:
            continue
        target = args.output / task['instance_id']
        if target.exists():
            raise ValueError(f'refusing to overwrite prior run: {target}')
        target.mkdir()
        workflow = task['instance_id'] + '-' + uuid.uuid4().hex[:8]
        log.emit('workflow_submit', workflow_id=workflow, task_id=task['instance_id'],
                 source_group=task['source_group'], base_commit=task['base_commit'],
                 compatibility_id=manifest['compatibility_id'], evidence_scope='autonomous_agent_pilot')

        class Model(LitellmTextbasedModel):
            last_request = None
            turn = 0

            def _query(self, messages, **kwargs):
                self.turn += 1
                rid = uuid.uuid4().hex
                log.emit('request_ready', request_id=rid, workflow_id=workflow,
                         context_version=f'turn-{self.turn}', owner=workflow)
                payload = {'model': config['served_model_name'], 'messages': messages,
                           'request_id': rid, **config['generation']}
                response = http.post(base + '/v1/chat/completions', json=payload, timeout=300)
                response.raise_for_status()
                data = response.json()
                self.last_request = rid
                log.emit('client_response', request_id=rid, workflow_id=workflow,
                         response_id=data['id'], finish_reason=data['choices'][0]['finish_reason'])
                return litellm.ModelResponse(**data)

        model = Model(model_name='openai/' + config['served_model_name'], cost_tracking='ignore_errors',
                      observation_template='<returncode>{{output.returncode}}</returncode>\n<output>{{output.output[:8000]}}</output>')

        class Environment(LocalEnvironment):
            tool_id = None
            tool_started = 0

            def execute(self, action, **kwargs):
                self.tool_id = uuid.uuid4().hex
                self.tool_started = time.monotonic_ns()
                log.emit('tool_start', workflow_id=workflow, tool_id=self.tool_id,
                         request_id=model.last_request, tool_kind='bash',
                         command_category=category(action.get('command', '')),
                         command=action.get('command', ''))
                return super().execute(action, **kwargs)

            def _check_finished(self, output):
                log.emit('tool_end', workflow_id=workflow, tool_id=self.tool_id,
                         request_id=model.last_request, returncode=output['returncode'],
                         output_sha256=hashlib.sha256(output['output'].encode()).hexdigest(),
                         output_chars=len(output['output']),
                         duration_ns=time.monotonic_ns() - self.tool_started)
                return super()._check_finished(output)

        environment = Environment(cwd=task['workspace'], timeout=config['tool_timeout_s'], env={
            'PATH': str(ROOT / '.venv/bin') + ':' + os.environ['PATH'],
            'PYTHONPATH': task['workspace'], 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
            'PAGER': 'cat', 'PYTHONHASHSEED': '23',
        })
        agent = DefaultAgent(model, environment, system_template=SYSTEM, instance_template=INSTANCE,
                             step_limit=config['agent_step_limit'], cost_limit=0,
                             wall_time_limit_seconds=config['task_wall_time_limit_s'],
                             output_path=target / 'trajectory.json')
        started = time.monotonic_ns()
        result = {}
        try:
            result = agent.run(task['problem_statement'])
        except Exception as exc:
            result = {'exit_status': type(exc).__name__, 'error': str(exc)}
        finally:
            # The runner explicitly closes and discards this owner's context.
            # This is a real lifecycle decision, including after a budget exit.
            log.emit('context_close', workflow_id=workflow, reason='runner_closed_context',
                     agent_exit=result.get('exit_status', 'unknown'))
            log.emit('workflow_end', workflow_id=workflow)
            log.flush()
        patch = subprocess.run(['git', '-C', task['workspace'], 'diff'],
                               capture_output=True, text=True, check=True).stdout
        (target / 'agent.patch').write_text(patch)
        record = {'task_id': task['instance_id'], 'workflow_id': workflow, **result,
                  'calls': agent.n_calls, 'elapsed_s': (time.monotonic_ns() - started) / 1e9,
                  'patch_bytes': len(patch.encode()), 'issue_resolved': None,
                  'evaluation_status': 'not_yet_evaluated_with_upstream_tests'}
        (target / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
        results.append(record)
        print(json.dumps(record), flush=True)
    log.emit('pilot_observation_end', workflows=[r['workflow_id'] for r in results])
    log.flush()
    (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
