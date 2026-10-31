"""Versioned designed-task collector; preserves the historical MBPP runner."""
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
from leasepath.d2_repair_guidance import category, edit_rejection


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--variant', choices=['greedy-guided'], default='greedy-guided')
    p.add_argument('--tasks', type=Path, default=ROOT / '.artifacts/d2-diversity-v3/tasks.json')
    p.add_argument('--config', type=Path, default=ROOT / 'configs/prediction/d2_diversity_v3.json')
    p.add_argument('--output', type=Path)
    p.add_argument('--task-id', help='Optional preselected task ID for an incremental run')
    p.add_argument('--resume', action='store_true', help='Resume completed task records under the identical frozen configuration')
    args = p.parse_args()
    import litellm
    import requests
    import yaml
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel
    cfg = json.loads(args.config.read_text())
    manifest = json.loads((ROOT / cfg['backend_manifest']).read_text())
    if manifest['configuration']['model_revision'] != cfg['model_revision']:
        raise ValueError('backend model differs from configuration')
    commit = subprocess.check_output(['git', '-C', str(ROOT / '.artifacts/mini-swe-agent'), 'rev-parse', 'HEAD'], text=True).strip()
    if commit != cfg['mini_swe_agent_commit']:
        raise ValueError('Agent source differs from fixed revision')
    source = ROOT / cfg['upstream_prompt']
    upstream = yaml.safe_load(source.read_text())
    system = upstream['agent']['system_template']
    instance = upstream['agent']['instance_template'].split('    ## Submission')[0]
    # YAML has already removed indentation. Replace only upstream submission:
    instance = instance.split('## Submission')[0]
    instance += '''## Submission\nAfter implementing solution.py and running its tests, submit with exactly this command alone:\n`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`\nThe runner saves your actual source diff. Do not commit or create a patch file.\n'''
    instance += '''\n## Local task protocol\nThis is a designed file-processing task with real integration tests.\nImplement the function in solution.py; only solution.py is submitted.\nRead README.md, solution.py and test_visible.py first. Then read test_workload.py and write the implementation. Replace the complete solution.py using a cat heredoc; do not use sed -i. Editing and tests must be separate actions.\nRun exactly `python -m pytest -q test_visible.py test_workload.py` before submitting; independent edge tests remain outside this workspace.\nDo not modify README.md, test_visible.py, test_workload.py, or configuration files. Do not access parent directories or other workspaces.\nDo not install packages, use the network, run git checkout/pull/reset, or replace file edits with comments about edits.\nUse a single command per action, or connect necessary commands with && so failures remain visible.\nUse concise explanations. Python, pytest, and the standard library are already available.\n'''
    model_options = {k: upstream['model'][k] for k in ['observation_template', 'format_error_template']}
    generation = cfg['generation_profiles']['sampled' if args.variant == 'sampled' else 'greedy']
    output = (args.output or args.tasks.resolve().parent / 'runs' / args.variant).resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_manifest = {'variant': args.variant, 'configuration': cfg, 'generation': generation,
                    'backend_compatibility_id': manifest['compatibility_id'], 'upstream_prompt_sha256': sha(source),
                    'runner_sha256': sha(Path(__file__)), 'system_template': system,
                    'instance_template_before_workspace_substitution': instance,
                    'dependency_sha256': {'src/leasepath/d2_repair_guidance.py': sha(ROOT / 'src/leasepath/d2_repair_guidance.py')},
                    'tasks_sha256': sha(args.tasks), 'raw_log': str(ROOT / cfg['raw_log'])}
    if args.resume:
        previous = json.loads((output / 'run-manifest.json').read_text())
        if previous != run_manifest:
            raise ValueError('refusing to resume under a changed configuration or source')
    else:
        (output / 'run-manifest.json').write_text(json.dumps(run_manifest, indent=2) + '\n')
    tasks = json.loads(args.tasks.read_text())['tasks']
    log = writer(str(ROOT / cfg['raw_log']))
    http = requests.Session()
    http.trust_env = False
    base = f"http://{cfg['host']}:{cfg['port']}"
    http.get(base + '/health', timeout=5).raise_for_status()
    results = []
    for task in tasks:
        if args.task_id and args.task_id != task['instance_id']:
            continue
        target = output / task['instance_id']
        if args.resume and (target / 'result.json').exists():
            results.append(json.loads((target / 'result.json').read_text()))
            continue
        target.mkdir(exist_ok=False)
        work = Path(task['workspaces'][args.variant])
        baseline_commit = subprocess.check_output(['git', '-C', str(work), 'rev-parse', 'HEAD'], text=True).strip()
        if subprocess.check_output(['git', '-C', str(work), 'status', '--porcelain'], text=True):
            raise ValueError('refusing to start from a modified task workspace')
        before = sha(work / 'solution.py')
        workflow = f"{task['instance_id']}-{args.variant}-{uuid.uuid4().hex[:8]}"
        scope = 'function_generation_diagnostic' if args.variant == 'direct' else task['evidence_scope']
        log.emit('workflow_submit', workflow_id=workflow, task_id=task['instance_id'],
                 source_group=task['source_group'], parent_task_id=task['parent_task_id'], group_id=task['group_id'], compatibility_id=manifest['compatibility_id'],
                 evidence_scope=scope, generation=generation, variant=args.variant)

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

        model = Model(model_name='openai/' + cfg['served_model_name'], cost_tracking='ignore_errors', **model_options)
        command_results = Counter()
        tool_stats = []

        class Environment(LocalEnvironment):
            inspected = False
            tested_source_hash = None
            failure_count = 0

            def execute(self, action, **kwargs):
                command = action.get('command', '')
                correction = edit_rejection(command)
                if correction:
                    log.emit('action_rejected', workflow_id=workflow, request_id=model.last_request,
                             reason='separate_actions_and_whole_file_edits', command=command)
                    return {'output': correction, 'returncode': 1, 'exception_info': ''}
                reads_inputs = bool(re.search(r'\bcat\b', command)) and all(name in command for name in ['README.md', 'solution.py', 'test_visible.py'])
                if args.variant in cfg.get('guided_protocol_variants', []) and not self.inspected and not reads_inputs:
                    log.emit('action_rejected', workflow_id=workflow, request_id=model.last_request,
                             reason='initial_workspace_inspection_required', command=command)
                    return {'output': 'First inspect the actual task files with exactly: cat README.md solution.py test_visible.py. Then implement and test solution.py.',
                            'returncode': 1, 'exception_info': ''}
                if cfg.get('reject_interactive_editors') and re.search(r'(^|[\s;&|])(vi|vim|nano|emacs)(\s|$)', command):
                    log.emit('action_rejected', workflow_id=workflow, request_id=model.last_request,
                             reason='interactive_editor_unavailable', command=command)
                    return {'output': 'Interactive editors cannot work here. Write the actual Python implementation noninteractively, e.g. cat <<\'PY\' > solution.py\n# your complete Python module\nPY\nRequired API: ' + task['function_signature'],
                            'returncode': 1, 'exception_info': ''}
                if 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' in command and command.strip() != 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT':
                    log.emit('action_rejected', workflow_id=workflow, request_id=model.last_request,
                             reason='submission_must_be_a_standalone_command', command=command)
                    return {'output': 'Submit using only: echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT. First actually implement and test solution.py.',
                            'returncode': 1, 'exception_info': ''}
                if (args.variant in cfg.get('guided_protocol_variants', [])
                        and command.strip() == 'echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT'
                        and self.tested_source_hash != sha(work / 'solution.py')):
                    log.emit('action_rejected', workflow_id=workflow, request_id=model.last_request,
                             reason='current_source_requires_a_successful_visible_test', command=command)
                    return {'output': 'Before submitting, run exactly python -m pytest -q test_visible.py test_workload.py on the current solution.py and fix any failure. Then submit separately.',
                            'returncode': 1, 'exception_info': ''}
                self.tool_id = uuid.uuid4().hex
                self.started = time.monotonic_ns()
                self.before_source = sha(work / 'solution.py')
                self.command = command
                log.emit('tool_start', workflow_id=workflow, tool_id=self.tool_id, request_id=model.last_request,
                         tool_kind='bash', command_category=category(command), command=command,
                         prior_failed_tests=self.failure_count, timeout_s=cfg['tool_timeout_s'],
                         workload_family=task['workload_family'] if 'test_workload.py' in command and category(command) == 'test' else None,
                         work_units=task['work_units'] if 'test_workload.py' in command and category(command) == 'test' else None,
                         work_unit=task['work_unit'] if 'test_workload.py' in command and category(command) == 'test' else None)
                executed = {**action, 'command': 'set -e\n' + command} if cfg.get('shell_errexit') else action
                output = super().execute(executed, **kwargs)
                if reads_inputs and output['returncode'] == 0:
                    self.inspected = True
                key = (command, hashlib.sha256(output['output'].encode()).hexdigest())
                command_results[key] += 1
                if command_results[key] >= cfg['repeat_feedback_after']:
                    output['output'] += '\n<repeat_warning>This same command and result have already repeated. Read the actual files and error; perform a different action that makes progress. Do not repeat checkout or a failed command.</repeat_warning>'
                    log.emit('loop_feedback', workflow_id=workflow, request_id=model.last_request,
                             repetitions=command_results[key])
                return output

            def _check_finished(self, output):
                changed = self.before_source != sha(work / 'solution.py')
                if self.command.strip() == 'python -m pytest -q test_visible.py test_workload.py' and output['returncode'] == 0:
                    self.tested_source_hash = sha(work / 'solution.py')
                if category(self.command) == 'test' and output['returncode'] != 0:
                    self.failure_count += 1
                record = {'command': self.command, 'returncode': output['returncode'],
                          'source_changed': changed, 'category': 'edit' if changed else category(self.command),
                          'duration_ns': time.monotonic_ns() - self.started}
                tool_stats.append(record)
                log.emit('tool_end', workflow_id=workflow, tool_id=self.tool_id, request_id=model.last_request,
                         output_sha256=hashlib.sha256(output['output'].encode()).hexdigest(),
                         output_chars=len(output['output']), source_changed=changed,
                         returncode=output['returncode'], duration_ns=record['duration_ns'])
                return super()._check_finished(output)

        agent = None
        started = time.monotonic_ns()
        result = {}
        try:
            if args.variant == 'direct':
                messages = [{'role': 'system', 'content': (
                    'You are a Python programming assistant implementing a small, precisely specified function. '
                    'Return a complete self-contained solution.py module in one Python code block. Use the given function name and argument signature unchanged. '
                    'Use only the Python standard library. Your function must work for all valid inputs, not only the provided example. '
                    'Do not print answers, read standard input, invoke a shell, or run commands. Do not include test code or a main block in the module. '
                    'Include any imports required by your implementation. Do not use hardcoded example outputs. '
                    'Choose a straightforward general algorithm and consider boundaries and duplicate values where relevant. '
                    'No explanation outside the code block is required. The module will be independently imported and tested after generation.')},
                    {'role': 'user', 'content': task['problem_statement'] + '\nRequired signature: ' + task['function_signature'] +
                     '\nVisible example:\n' + (work / 'test_visible.py').read_text()}]
                response = model._query(messages)
                text = response.choices[0].message.content or ''
                (target / 'response.json').write_text(response.model_dump_json(indent=2) + '\n')
                blocks = re.findall(r'```(?:python)?\s*\n(.*?)\n```', text, re.S)
                candidate = blocks[0] if len(blocks) == 1 else text
                compile(candidate, '<generated-solution>', 'exec')
                (work / 'solution.py').write_text(candidate + '\n')
                result = {'exit_status': 'Generated'}
            else:
                environment = Environment(cwd=str(work), timeout=cfg['tool_timeout_s'], env={
                    'PATH': str(ROOT / '.venv/bin') + ':' + os.environ['PATH'], 'PYTHONPATH': str(work),
                    'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PAGER': 'cat', 'PYTHONHASHSEED': '23'})
                agent = DefaultAgent(model, environment, system_template=system,
                                     instance_template=instance.replace('/testbed', str(work)),
                                     step_limit=cfg['agent_step_limit'], cost_limit=0,
                                     wall_time_limit_seconds=cfg['task_wall_time_limit_s'], output_path=target / 'trajectory.json')
                task_prompt = task['problem_statement']
                if args.variant in cfg.get('explicit_task_contract_variants', []):
                    task_prompt += ('\n\nREQUIRED API: Keep this exact existing function signature in solution.py:\n'
                                    + task['function_signature'] + '\nDo not rename the function or its arguments.\n'
                                    + 'VISIBLE TEST (read-only):\n' + (work / 'test_visible.py').read_text())
                if args.variant in cfg.get('guided_protocol_variants', []):
                    task_prompt += '\nFIRST COMMAND REQUIRED: cat README.md solution.py test_visible.py\nThen implement the unchanged API, run pytest, and submit using the standalone completion command.\n'
                result = agent.run(task_prompt)
        except Exception as exc:
            result = {'exit_status': type(exc).__name__, 'error': str(exc)}
        finally:
            log.emit('context_close', workflow_id=workflow, reason='runner_closed_context', agent_exit=result.get('exit_status'))
            log.emit('workflow_end', workflow_id=workflow)
            log.flush()
        patch = subprocess.check_output(['git', '-C', str(work), 'diff', '--binary', baseline_commit, '--', 'solution.py'], text=True)
        (target / 'agent.patch').write_text(patch)
        (target / 'solution.py').write_bytes((work / 'solution.py').read_bytes())
        protected = task['protected_sha256']
        record = {'task_id': task['instance_id'], 'group_id': task['group_id'], 'workflow_id': workflow, 'variant': args.variant,
                  'evidence_scope': scope, **result, 'calls': model.turn, 'agent_calls': agent.n_calls if agent else None,
                  'elapsed_s': (time.monotonic_ns() - started) / 1e9, 'patch_bytes': len(patch.encode()),
                  'source_edited': before != sha(work / 'solution.py'),
                  'protected_files_unchanged': all(sha(work / name) == expected for name, expected in protected.items()),
                  'tool_stats': tool_stats, 'loop_feedback_count': sum(max(0, n-1) for n in command_results.values()),
                  'evaluation_status': 'pending_independent_tests'}
        (target / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
        results.append(record)
        print(json.dumps({k: v for k, v in record.items() if k != 'tool_stats'}), flush=True)
        (output / 'partial-results.json').write_text(json.dumps(results, indent=2) + '\n')
    log.emit('pilot_observation_end', workflows=[r['workflow_id'] for r in results])
    log.flush()
    (output / 'results.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
