"""Run the frozen real issue pilot with the plan's mini-swe/32B fallback.

No reference patches enter the agent workspace. Each retry needs a fresh output
and stays in the same issue group. Agent outcome and backend integrity are
evaluated independently after the run.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shlex
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.real_observer import Observer
from leasepath.real_patches import capture_patch

SYSTEM = '''You are a coding agent fixing a real issue in the current repository.
Inspect the code, reproduce the issue, implement a minimal fix, and run relevant tests.
Each response MUST contain exactly one shell command in this format:
```mswea_bash_command
your command here
```
Use concise analysis. Commands run in fresh shells; directory changes do not persist.
Do not install packages, access sibling directories, delegate, or modify tests to hide failures.
The configured python and pytest are already installed. Work only in this repository.
When finished, run only: echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', type=Path, required=True)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--environments', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--task-id')
    args = parser.parse_args()
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
    import litellm
    import requests
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((args.run / 'service-manifest.json').read_text())
    tasks = json.loads(args.tasks.read_text())['tasks']
    commit = subprocess.check_output(['git', '-C', str(ROOT / '.artifacts/mini-swe-agent'), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != '04d809ceab9df28f9adaed044884180159172930':
        raise ValueError('mini-swe source commit differs from validated stack')
    http = requests.Session()
    http.trust_env = False
    base = f"http://127.0.0.1:{manifest['port']}"
    http.get(base + '/health', timeout=5).raise_for_status()
    cpus = sorted(os.sched_getaffinity(0))[-4:]
    results = []
    for task in tasks:
        if args.task_id and args.task_id != task['instance_id']:
            continue
        target = args.output / task['instance_id']
        target.mkdir()
        work = target / 'workspace'
        subprocess.run(['git', 'clone', '-q', '--shared', task['workspace'], str(work)], check=True)
        initial_commit = subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], text=True).strip()
        expected = json.loads((args.tasks.parent / 'evaluation' / (task['instance_id'] + '.source-files.json')).read_text())
        for relative, digest in expected.items():
            if hashlib.sha256((work / relative).read_bytes()).hexdigest() != digest:
                raise ValueError('source workspace differs from frozen upstream')
        envname = 'flask' if task['repo'] == 'pallets/flask' else 'pytest-py39'
        python_dir = args.environments.absolute() / envname / 'bin'
        env = {**os.environ, 'PATH': str(python_dir) + ':' + os.environ['PATH'],
               'PYTHONPATH': str(work / 'src'), 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1',
               'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
               'PAGER': 'cat', 'PYTHONHASHSEED': '23'}
        if task['repo'] == 'pytest-dev/pytest':
            env['SETUPTOOLS_SCM_PRETEND_VERSION'] = '6.3.0.dev0'
            built = subprocess.run([str(python_dir / 'python'), '-m', 'pip', 'install', '--no-deps',
                                    '--no-build-isolation', '--no-index', '.'], cwd=work, env=env,
                                   capture_output=True, text=True, timeout=120)
            (target / 'package-build.log').write_text(built.stdout + built.stderr)
            if built.returncode:
                raise RuntimeError('frozen pytest package preparation failed')
        else:
            env['PYTEST_ADDOPTS'] = shlex.join([part for api in ['get_loader', 'find_loader']
                for part in ['-W', f"ignore:'pkgutil.{api}' is deprecated:DeprecationWarning"]])
        workflow = task['instance_id'] + '-' + uuid.uuid4().hex[:8]
        log = Observer(target / 'runner.jsonl', run_id=args.run.name, source='mini_swe_runner')
        log.emit('workflow_submit', owner=workflow, context_version='initial', task_id=task['instance_id'],
                 source_group=task['source_group'], project_group=task['repo'],
                 base_commit=task['base_commit'], compatibility_id=manifest['compatibility_id'],
                 agent='mini-swe-agent', evidence_scope='autonomous_real_repository_pilot',
                 cpu_affinity=cpus, thread_limit=1)

        class Model(LitellmTextbasedModel):
            last_request = None
            turn = 0

            def _query(self, messages, **kwargs):
                self.turn += 1
                rid = uuid.uuid4().hex
                log.emit('request_ready', request_id=rid, owner=workflow, context_version='initial', turn=self.turn)
                payload = {'model': 'leasepath-qwen25-coder32b', 'messages': messages,
                           'request_id': rid, 'temperature': 0, 'top_p': 1, 'max_tokens': 768, 'seed': 23}
                (target / f'request-{self.turn:03d}.json').write_text(json.dumps(payload, ensure_ascii=False) + '\n')
                response = http.post(base + '/v1/chat/completions', json=payload, timeout=300)
                if not response.ok:
                    (target / f'request-{self.turn:03d}.error').write_text(response.text)
                response.raise_for_status()
                data = response.json()
                self.last_request = rid
                log.emit('client_response', request_id=rid, owner=workflow, response_id=data['id'],
                         finish_reason=data['choices'][0]['finish_reason'], usage=data.get('usage'))
                return litellm.ModelResponse(**data)

        model = Model(model_name='openai/leasepath-qwen25-coder32b', cost_tracking='ignore_errors',
                      observation_template='<returncode>{{output.returncode}}</returncode>\n<output>{{output.output[:10000]}}</output>')

        class Environment(LocalEnvironment):
            tool_id = None
            tool_started = None

            def execute(self, action, **kwargs):
                self.tool_id = uuid.uuid4().hex
                self.tool_started = time.monotonic_ns()
                log.emit('tool_start', owner=workflow, tool_id=self.tool_id,
                         request_id=model.last_request, command=action.get('command', ''))
                command = ('taskset -c ' + ','.join(map(str, cpus)) + ' /bin/bash -c ' +
                           shlex.quote(action.get('command', '')))
                return super().execute({**action, 'command': command}, **kwargs)

            def _check_finished(self, output):
                log.emit('tool_end', owner=workflow, tool_id=self.tool_id,
                         request_id=model.last_request, returncode=output['returncode'],
                         output_sha256=hashlib.sha256(output['output'].encode()).hexdigest(),
                         duration_ns=time.monotonic_ns() - self.tool_started)
                log.emit('results_ready', owner=workflow, tool_id=self.tool_id, request_id=model.last_request)
                return super()._check_finished(output)

        environment = Environment(cwd=str(work), timeout=90, env=env)
        agent = DefaultAgent(model, environment, system_template=SYSTEM,
                             instance_template='Fix this real issue:\n{{task}}\nInspect, reproduce, edit, and test before submitting.',
                             step_limit=30, cost_limit=0, wall_time_limit_seconds=900,
                             output_path=target / 'trajectory.json')
        start = time.monotonic()
        try:
            result = agent.run(task['problem_statement'])
        except Exception as exc:
            result = {'exit_status': type(exc).__name__, 'error': str(exc)}
        finally:
            log.emit('context_retire', owner=workflow, context_version='initial', reason='runner_explicitly_discards_context')
            log.emit('observation_end', owner=workflow, client_complete=True,
                     backend_completeness_requires_separate_validation=True)
            log.finish()
        patch = capture_patch(work, initial_commit)
        (target / 'agent.patch').write_bytes(patch)
        record = {'task_id': task['instance_id'], 'owner': workflow, 'agent': 'mini-swe-agent',
                  'model': 'Qwen2.5-Coder-32B-Instruct', **result, 'calls': agent.n_calls,
                  'elapsed_s': time.monotonic() - start, 'patch_bytes': len(patch),
                  'initial_local_commit': initial_commit,
                  'task_correctness': 'pending_independent_evaluation'}
        (target / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
        results.append(record)
        (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
