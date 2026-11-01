#!/home/ma-user/takaya/leasepath/.venv/bin/python
"""Observed shell selected by the model's exec_command arguments.

Executes exactly the CLI-supplied command, linked to the validated model call.
No patch generation, command rewriting, environment repair or hidden retry.
The separate process has a 90s deadline, including termination of its children.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

sys.path.insert(0, '/home/ma-user/takaya/leasepath/src')
from leasepath.real_observer import Observer


def main():
    root = Path(os.environ['LEASEPATH_CODEX_EXECUTION_ROOT'])
    args = sys.argv[1:]
    # Codex may use `-c` or `--noprofile --norc -c`. Login is forbidden.
    if '-c' not in args or any(x in {'-l', '-lc', '--login'} for x in args):
        raise ValueError('only non-login -c shell execution is supported')
    command = args[args.index('-c') + 1]
    candidates = []
    for p in sorted((root / 'tool-intents').glob('*.json')):
        item = json.loads(p.read_text())
        if json.loads(item['arguments']).get('cmd') == command and not p.with_suffix('.claimed').exists():
            candidates.append((p, item))
    if len(candidates) != 1:
        raise ValueError('shell command has no unique unclaimed validated model call')
    path, item = candidates[0]
    with path.with_suffix('.claimed').open('x') as f:
        f.write(str(os.getpid()))
    key = hashlib.sha256(item['id'].encode()).hexdigest()
    log = Observer(root / 'tool-execution' / (key + '.jsonl'), run_id=item['run_id'], source='codex32b_shell')
    log.emit('tool_start', owner=item['owner'], tool_call_id=item['id'], tool_name=item['name'],
             command=command, cwd=os.getcwd(), argv=args, shell='/bin/bash',
             PATH=os.environ.get('PATH'), PYTHONPATH=os.environ.get('PYTHONPATH'),
             timestamp_semantics='actual_observed_executor_before_spawn')
    child = None
    def stop(signum, frame):
        if child:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        log.emit('tool_cancelled', tool_call_id=item['id'], signal=signum)
        log.finish()
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    start = time.monotonic()
    env = dict(os.environ)
    env.pop('BASH_ENV', None)  # avoid recursively observing inner shell commands
    child = subprocess.Popen(['/bin/bash', *args], start_new_session=True, env=env)
    timed_out = False
    try:
        code = child.wait(timeout=90)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        code = 124
    try:
        os.killpg(child.pid, signal.SIGKILL)  # no orphan background work
    except ProcessLookupError:
        pass
    log.emit('tool_end', owner=item['owner'], tool_call_id=item['id'], returncode=code,
             timed_out=timed_out, elapsed_s=time.monotonic() - start,
             timestamp_semantics='shell_process_exit_not_CLI_delivery')
    log.finish()
    raise SystemExit(code)


if __name__ == '__main__':
    main()
