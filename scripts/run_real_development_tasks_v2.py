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
from leasepath.real_environment import prepare_environment, preflight_environment
from leasepath.real_workflow import ProgressGuard

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
    parser.add_argument('--agent-config', type=Path, required=True)
    args = parser.parse_args()
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
    import litellm
    import requests
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    agent_config = json.loads(args.agent_config.read_text())
    if agent_config.get('disable_model_retries'):
        os.environ['MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT'] = '1'
    config_hash = hashlib.sha256(args.agent_config.read_bytes()).hexdigest()
    (args.output / 'frozen-agent-config.json').write_bytes(args.agent_config.read_bytes())
    (args.output / 'implementation-sha256.json').write_text(json.dumps({str(p.relative_to(ROOT)):
        hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),
        ROOT/'src/leasepath/real_environment.py', ROOT/'src/leasepath/real_patches.py',
        ROOT/'src/leasepath/real_workflow.py']}, indent=2)+'\n')
    manifest = json.loads((args.run / 'service-manifest.json').read_text())
    (args.output/'run-manifest.json').write_text(json.dumps({'agent_config_sha256':config_hash,
         'tasks_sha256':hashlib.sha256(args.tasks.read_bytes()).hexdigest(),
         'service_manifest_sha256':hashlib.sha256((args.run/'service-manifest.json').read_bytes()).hexdigest(),
         'environments_root':str(args.environments.absolute()), 'run_id':args.run.name,
         'attempts':'one predetermined greedy attempt per selected issue; no automatic HTTP retry',
         'measurement_scope':'real autonomous repository work, not natural user sessions'},indent=2)+'\n')
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
        source_manifest = args.tasks.parent / 'evaluation' / (task['instance_id'] + '.source-files.json')
        if hashlib.sha256(source_manifest.read_bytes()).hexdigest() != task['source_files_manifest_sha256']:
            raise ValueError('source file manifest differs from frozen task')
        expected = json.loads(source_manifest.read_text())
        for relative, digest in expected.items():
            if hashlib.sha256((work / relative).read_bytes()).hexdigest() != digest:
                raise ValueError('source workspace differs from frozen upstream')
        envname = 'flask' if task['repo'] == 'pallets/flask' else 'pytest-py39'
        env, wrappers = prepare_environment(work, args.environments / envname, target / 'environment', task['repo'])
        evidence = preflight_environment(work, env, wrappers, task['repo'], cpus=cpus)
        (target/'environment.json').write_text(json.dumps(evidence,indent=2)+'\n')
        workflow = task['instance_id'] + '-' + uuid.uuid4().hex[:8]
        log = Observer(target / 'runner.jsonl', run_id=args.run.name, source='mini_swe_runner')
        log.emit('workflow_submit', owner=workflow, context_version='initial', task_id=task['instance_id'],
                 source_group=task['source_group'], project_group=task['repo'],
                 base_commit=task['base_commit'], compatibility_id=manifest['compatibility_id'],
                 agent='mini-swe-agent', evidence_scope='autonomous_real_repository_pilot',
                 cpu_affinity=cpus, thread_limit=1, agent_config_sha256=config_hash,
                 agent_version=agent_config['version'])
        request_metrics, tool_metrics, behavior = [], [], []

        class Model(LitellmTextbasedModel):
            last_request = None
            turn = 0

            def _query(self, messages, **kwargs):
                self.turn += 1
                rid = uuid.uuid4().hex
                log.emit('request_ready', request_id=rid, owner=workflow, context_version='initial', turn=self.turn,
                         attempt_id=rid, logical_request_id=rid, purpose='demand', wire_api='chat', retry_of=None)
                payload = {'model': 'leasepath-qwen25-coder32b', 'messages': messages,
                           'request_id': rid, 'temperature': agent_config['temperature'], 'top_p': 1,
                           'max_tokens': agent_config['max_tokens'], 'seed': agent_config['seed']}
                (target / f'request-{self.turn:03d}.json').write_text(json.dumps(payload, ensure_ascii=False) + '\n')
                request_start = time.monotonic_ns()
                response = http.post(base + '/v1/chat/completions', json=payload, timeout=300)
                if not response.ok:
                    (target / f'request-{self.turn:03d}.error').write_text(response.text)
                    log.emit('client_error', request_id=rid, owner=workflow, status=response.status_code,
                             body_sha256=hashlib.sha256(response.content).hexdigest())
                response.raise_for_status()
                data = response.json()
                request_metrics.append({'request_id':rid,'elapsed_s':(time.monotonic_ns()-request_start)/1e9,
                                        'usage':data.get('usage'),'finish_reason':data['choices'][0]['finish_reason']})
                self.last_request = rid
                log.emit('client_response', request_id=rid, owner=workflow, response_id=data['id'],
                         finish_reason=data['choices'][0]['finish_reason'], usage=data.get('usage'))
                return litellm.ModelResponse(**data)

        observation_limit = agent_config.get('observation_char_limit',10000)
        template = ('<returncode>{{output.returncode}}</returncode>\n<output>' +
                    ('{{output.output[:10000]}}' if observation_limit==10000 else
                     '{% if output.output|length > '+str(observation_limit)+' %}'+
                     '{{output.output[:'+str(observation_limit//2)+']}}\n[output shortened before delivery; request narrower source/test output]\n'+
                     '{{output.output[-'+str(observation_limit//2)+':]}}{% else %}{{output.output}}{% endif %}') + '</output>')
        model = Model(model_name='openai/leasepath-qwen25-coder32b', cost_tracking='ignore_errors', observation_template=template)
        progress_guard = ProgressGuard()

        class Environment(LocalEnvironment):
            tool_id = None
            tool_started = None
            repeats = 0
            previous = None
            submission_feedback = 0
            stopped = False
            action_command = ''
            patch_before = None

            def execute(self, action, **kwargs):
                if self.stopped:
                    from minisweagent.exceptions import LimitsExceeded
                    raise LimitsExceeded({'role':'exit','content':'RepeatedNoProgress',
                        'extra':{'exit_status':'RepeatedNoProgress','submission':''}})
                self.tool_id = uuid.uuid4().hex
                self.tool_started = time.monotonic_ns()
                self.action_command = action.get('command','')
                self.patch_before = capture_patch(work, initial_commit)
                log.emit('tool_start', owner=workflow, tool_id=self.tool_id,
                         request_id=model.last_request, command=action.get('command', ''))
                command = ('taskset -c ' + ','.join(map(str, cpus)) + ' /bin/bash -c ' +
                           shlex.quote(action.get('command', '')))
                return super().execute({**action, 'command': command}, **kwargs)

            def _check_finished(self, output):
                patch = capture_patch(work, initial_commit)
                fingerprint = (self.action_command, hashlib.sha256(patch).hexdigest())
                if agent_config.get('equivalent_failure_guard'):
                    feedback = progress_guard.observe(self.action_command,output['returncode'],output['output'],self.patch_before,patch)
                    if feedback['notes']:
                        output['output'] += '\nWORKFLOW FEEDBACK: '+' '.join(feedback['notes'])+'\n'
                    if feedback['stop']:
                        self.stopped = True
                    log.emit('workflow_feedback',owner=workflow,tool_id=self.tool_id,**feedback)
                self.repeats = self.repeats + 1 if self.previous == fingerprint else 1
                self.previous = fingerprint
                if self.repeats == agent_config['identical_command_no_progress_feedback_at']:
                    output['output'] += ('\nWORKFLOW FEEDBACK: This identical command has made no source progress three times. '
                        'Read the actual traceback and repository implementation, then change your approach.\n')
                if self.repeats >= agent_config['identical_command_no_progress_stop_at']:
                    self.stopped = True
                flags = {'command':self.action_command, 'returncode':output['returncode'],
                         'source_read_candidate':any(x in self.action_command for x in ['sed ', 'cat ', '.read_text(', 'rg ']),
                         'validation_candidate':any(x in self.action_command for x in ['pytest', 'python ', 'flask ']),
                         'patch_changed':patch != self.patch_before}
                behavior.append(flags)
                if output['output'].strip().startswith('COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'):
                    paths = subprocess.check_output(['git','-C',str(work),'diff','--name-only',initial_commit],text=True).splitlines()
                    production = [p for p in paths if p.startswith('src/')]
                    if (not production or not any(b['source_read_candidate'] for b in behavior) or
                            not any(b['validation_candidate'] for b in behavior)) and self.submission_feedback < agent_config['submission_feedback_limit']:
                        self.submission_feedback += 1
                        output['output'] = ('WORKFLOW FEEDBACK: Submission lacks production-source changes, source inspection or validation. '
                            'Inspect the library source, implement the fix and run relevant tests. If blocked, report the failure honestly.\n')
                tool_metrics.append({'command':self.action_command,'returncode':output['returncode'],
                                     'duration_s':(time.monotonic_ns()-self.tool_started)/1e9,
                                     'output_sha256':hashlib.sha256(output['output'].encode()).hexdigest()})
                log.emit('tool_end', owner=workflow, tool_id=self.tool_id,
                         request_id=model.last_request, returncode=output['returncode'],
                         output_sha256=hashlib.sha256(output['output'].encode()).hexdigest(),
                         duration_ns=time.monotonic_ns() - self.tool_started)
                log.emit('results_ready', owner=workflow, tool_id=self.tool_id, request_id=model.last_request)
                return super()._check_finished(output)

        environment = Environment(cwd=str(work), timeout=agent_config['tool_seconds'], env=env)
        agent = DefaultAgent(model, environment, system_template=agent_config['system_template'],
                             instance_template=agent_config['instance_template'],
                             step_limit=agent_config['step_limit'], cost_limit=0,
                             wall_time_limit_seconds=agent_config['wall_seconds'],
                             output_path=target / 'trajectory.json')
        start = time.monotonic()
        try:
            result = agent.run(task['problem_statement'])
        except Exception as exc:
            result = {'exit_status': type(exc).__name__, 'error': str(exc)}
        finally:
            task_end = time.monotonic()
            log.emit('context_retire', owner=workflow, context_version='initial', reason='runner_explicitly_discards_context')
            log.emit('observation_end', owner=workflow, client_complete=True,
                     backend_completeness_requires_separate_validation=True)
            log.finish()
        patch = capture_patch(work, initial_commit)
        (target / 'agent.patch').write_bytes(patch)
        record = {'task_id': task['instance_id'], 'owner': workflow, 'agent': 'mini-swe-agent',
                  'model': 'Qwen2.5-Coder-32B-Instruct', **result, 'calls': agent.n_calls,
                  'elapsed_s': task_end - start, 'patch_bytes': len(patch),
                  'JCT_scope':'agent.run entry to return/exception, including tools/inference/orchestration; preparation/archive excluded',
                  'agent_config_sha256':config_hash, 'agent_version':agent_config['version'],
                  'requests':request_metrics,'tools':tool_metrics,
                  'initial_local_commit': initial_commit,
                  'task_correctness': 'pending_independent_evaluation'}
        (target / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
        changed = subprocess.check_output(['git','-C',str(work),'diff','--name-only',initial_commit],text=True).splitlines()
        (target/'behavior-review.json').write_text(json.dumps({'automated_flags_not_correctness_or_manual_review':behavior,
             'changed_tracked_files':changed,'full_patch_includes_untracked':True,
             'production_files':[p for p in changed if p.startswith('src/')],
             'repeated_no_progress_stopped':environment.stopped,'submission_feedback_count':environment.submission_feedback},indent=2)+'\n')
        results.append(record)
        (args.output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({k:record[k] for k in ['task_id','exit_status','calls','elapsed_s','patch_bytes']}), flush=True)


if __name__ == '__main__':
    main()
