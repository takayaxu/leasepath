"""Guided repair runner; whole-file edits, distinct test actions, real retry links."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from leasepath.d2_raw import writer
from leasepath.d2_repair import INSPECT, SUITE, SUBMIT, RepairState
from leasepath.d2_repair_guidance import category, edit_rejection


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--tasks', type=Path, default=ROOT / '.artifacts/d2-repair-v2-guided/tasks.json')
    p.add_argument('--config', type=Path, default=ROOT / 'configs/prediction/d2_repair_v2.json')
    p.add_argument('--resume', action='store_true')
    args = p.parse_args()
    import litellm
    import requests
    import yaml
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

    cfg = json.loads(args.config.read_text())
    backend = json.loads((ROOT / cfg['backend_manifest']).read_text())
    if backend['configuration']['model_revision'] != cfg['model_revision']:
        raise ValueError('backend model revision mismatch')
    commit = subprocess.check_output(['git', '-C', str(ROOT / '.artifacts/mini-swe-agent'), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != cfg['mini_swe_agent_commit']:
        raise ValueError('agent revision mismatch')
    upstream = yaml.safe_load((ROOT / cfg['upstream_prompt']).read_text())
    instance = upstream['agent']['instance_template'].split('## Submission')[0]
    instance += ('## Repair protocol\nThis task starts with an actual defective implementation. '
                 'Inspect all four files, run the original full suite before editing, repair solution.py, '
                 'then rerun the full suite. Do not modify tests/README, skip work, use network or access other directories. '
                 'Use noninteractive file edits. Python, pytest and standard library are available.\n'
                 "After reproducing, replace the entire solution.py using cat <<'PY' > solution.py with your complete Python module. "
                 'Do not use sed -i. Make editing and pytest separate actions. Never combine them in one action.\n'
                 f'First command: {INSPECT}\nSecond command: {SUITE}\n'
                 f'After the current source passes, submit with exactly: {SUBMIT}\n')
    generation = cfg['generation_profiles']['greedy']
    output = args.tasks.resolve().parent / 'runs/greedy-guided'
    dependencies = ['src/leasepath/d2_repair.py', 'src/leasepath/d2_repair_guidance.py']
    manifest = {'variant': 'greedy-guided', 'configuration': cfg, 'generation': generation,
                'backend_compatibility_id': backend['compatibility_id'], 'runner_sha256': sha(Path(__file__)),
                'dependency_sha256': {f: sha(ROOT / f) for f in dependencies},
                'tasks_sha256': sha(args.tasks), 'upstream_prompt_sha256': sha(ROOT / cfg['upstream_prompt']),
                'system_template': upstream['agent']['system_template'], 'instance_template': instance,
                'raw_log': str(ROOT / cfg['raw_log'])}
    if args.resume:
        if json.loads((output / 'run-manifest.json').read_text()) != manifest:
            raise ValueError('refusing resume after source/configuration change')
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / 'run-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    http = requests.Session()
    http.trust_env = False
    base = f'http://{cfg["host"]}:{cfg["port"]}'
    http.get(base + '/health', timeout=5).raise_for_status()
    log = writer(str(ROOT / cfg['raw_log']))
    results = []
    for task in json.loads(args.tasks.read_text())['tasks']:
        target = output / task['instance_id']
        if args.resume and (target / 'result.json').exists():
            results.append(json.loads((target / 'result.json').read_text()))
            continue
        target.mkdir(exist_ok=False)
        (target / 'tools').mkdir()
        work = Path(task['workspaces']['greedy-guided'])
        baseline = subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], text=True).strip()
        if subprocess.check_output(['git', '-C', str(work), 'status', '--porcelain'], text=True):
            raise ValueError('task workspace must start clean')
        initial = sha(work / 'solution.py')
        if initial != task['initial_source_sha256']:
            raise ValueError('initial defective source mismatch')
        state = RepairState(initial)
        workflow = task['instance_id'] + '-greedy-guided-' + uuid.uuid4().hex[:8]
        log.emit('workflow_submit', workflow_id=workflow, task_id=task['instance_id'],
                 parent_task_id=task['parent_task_id'], group_id=task['group_id'],
                 source_group=task['source_group'], evidence_scope=task['evidence_scope'],
                 compatibility_id=backend['compatibility_id'], generation=generation,
                 variant='greedy-guided', scale_band=task['scale_band'])

        class Model(LitellmTextbasedModel):
            last_request = None
            turn = 0
            abort_exceptions = LitellmTextbasedModel.abort_exceptions + [requests.HTTPError]

            def _query(self, messages, **kwargs):
                self.turn += 1
                rid = uuid.uuid4().hex
                log.emit('request_ready', workflow_id=workflow, owner=workflow, request_id=rid,
                         context_version=f'turn-{self.turn}')
                response = http.post(base + '/v1/chat/completions', timeout=300, json={
                    'model': cfg['served_model_name'], 'request_id': rid, 'messages': messages, **generation})
                if not response.ok:
                    log.emit('client_http_error', workflow_id=workflow, request_id=rid,
                             status_code=response.status_code, response_body=response.text[:16384])
                response.raise_for_status()
                data = response.json()
                self.last_request = rid
                log.emit('client_response', workflow_id=workflow, request_id=rid,
                         response_id=data['id'], finish_reason=data['choices'][0]['finish_reason'])
                return litellm.ModelResponse(**data)

        model = Model(model_name='openai/' + cfg['served_model_name'], cost_tracking='ignore_errors',
                      **{k: upstream['model'][k] for k in ['observation_template', 'format_error_template']})
        stats, repetitions, rejections = [], Counter(), []

        def protected_ok():
            return all(sha(work / f) == h for f, h in task['protected_sha256'].items())

        class Environment(LocalEnvironment):
            def execute(self, action, **kwargs):
                command = action.get('command', '').strip()
                rejection = state.rejection(command, sha(work / 'solution.py'))
                rejection = rejection or edit_rejection(command)
                if not protected_ok():
                    rejection = 'Protected files changed; only solution.py is editable.'
                if re.search(r'(^|[\s;&|])(vi|vim|nano|emacs)(\s|$)', command):
                    rejection = 'Use noninteractive edits such as a Python script or cat heredoc.'
                if rejection:
                    log.emit('action_rejected', workflow_id=workflow, request_id=model.last_request,
                             command=command, reason=rejection)
                    rejections.append({'command': command, 'reason': rejection})
                    return {'output': rejection, 'returncode': 1, 'exception_info': ''}
                self.active = {'tool_id': uuid.uuid4().hex, 'command': command,
                               'start_ns': time.monotonic_ns(), 'before': sha(work / 'solution.py')}
                meta = state.start(command, self.active['tool_id'], self.active['before'])
                workload = command == SUITE
                log.emit('tool_start', workflow_id=workflow, request_id=model.last_request,
                         tool_id=self.active['tool_id'], tool_kind='bash', command_category=category(command),
                         command=command, timeout_s=cfg['tool_timeout_s'],
                         workload_family=task['workload_family'] if workload else None,
                         work_units=task['work_units'] if workload else None,
                         work_unit=task['work_unit'] if workload else None, **meta)
                result = super().execute({**action, 'command': 'set -e\n' + command}, **kwargs)
                key = (command, hashlib.sha256(result['output'].encode()).hexdigest())
                repetitions[key] += 1
                if repetitions[key] >= cfg['repeat_feedback_after']:
                    result['output'] += '\nRepeated identical result; inspect the actual failure and make a concrete source repair.'
                return result

            def _check_finished(self, result):
                active = self.active
                after = sha(work / 'solution.py')
                state.end(active['command'], active['tool_id'], active['before'], after,
                          result['returncode'], category(active['command']))
                record = {'tool_id': active['tool_id'], 'command': active['command'],
                          'category': category(active['command']), 'returncode': result['returncode'],
                          'source_changed': active['before'] != after,
                          'source_before_sha256': active['before'], 'source_after_sha256': after,
                          'duration_ns': time.monotonic_ns() - active['start_ns']}
                stats.append(record)
                (target / 'tools' / (active['tool_id'] + '.json')).write_text(
                    json.dumps({**record, 'output': result['output'], 'exception_info': result.get('exception_info')}, indent=2) + '\n')
                log.emit('tool_end', workflow_id=workflow, request_id=model.last_request,
                         tool_id=active['tool_id'], returncode=result['returncode'],
                         source_changed=record['source_changed'], source_sha256=after,
                         duration_ns=record['duration_ns'], output_chars=len(result['output']),
                         output_sha256=hashlib.sha256(result['output'].encode()).hexdigest())
                return super()._check_finished(result)

        environment = Environment(cwd=str(work), timeout=cfg['tool_timeout_s'], env={
            'PATH': str(ROOT / '.venv/bin') + ':' + os.environ['PATH'], 'PYTHONPATH': str(work),
            'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': '', 'PAGER': 'cat', 'PYTHONHASHSEED': '23'})
        agent = DefaultAgent(model, environment, system_template=upstream['agent']['system_template'],
                             instance_template=instance.replace('/testbed', str(work)),
                             step_limit=cfg['agent_step_limit'], cost_limit=0,
                             wall_time_limit_seconds=cfg['task_wall_time_limit_s'], output_path=target / 'trajectory.json')
        started = time.monotonic_ns()
        result = {}
        try:
            result = agent.run(task['problem_statement'] + '\nRequired API: ' + task['function_signature'])
        except Exception as exc:
            result = {'exit_status': type(exc).__name__, 'error': str(exc)}
        finally:
            log.emit('context_close', workflow_id=workflow, reason='runner_closed_context', agent_exit=result.get('exit_status'))
            log.emit('workflow_end', workflow_id=workflow)
            log.flush()
        patch = subprocess.check_output(['git', '-C', str(work), 'diff', '--binary', baseline, '--', 'solution.py'], text=True)
        (target / 'agent.patch').write_text(patch)
        (target / 'solution.py').write_bytes((work / 'solution.py').read_bytes())
        record = {'task_id': task['instance_id'], 'parent_task_id': task['parent_task_id'],
                  'group_id': task['group_id'], 'workflow_id': workflow, 'variant': 'greedy-guided',
                  'evidence_scope': task['evidence_scope'], **result, 'calls': model.turn,
                  'agent_calls': agent.n_calls, 'elapsed_s': (time.monotonic_ns() - started) / 1e9,
                  'source_edited': initial != sha(work / 'solution.py'), 'patch_bytes': len(patch.encode()),
                  'protected_files_unchanged': protected_ok(), 'initial_defect_reproduced': state.reproduced,
                  'failed_tests': state.failed_tests, 'full_suite_attempts': state.attempts,
                  'full_suite_retries': state.retries, 'tool_stats': stats, 'rejections': rejections,
                  'loop_feedback_count': sum(max(0, n-1) for n in repetitions.values())}
        (target / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
        results.append(record)
        (output / 'partial-results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps({k: v for k, v in record.items() if k not in ['tool_stats', 'rejections']}), flush=True)
    log.emit('pilot_observation_end', workflows=[r['workflow_id'] for r in results])
    log.flush()
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
