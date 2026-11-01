"""Auditable paged reading and exact editing for the separate v3 diagnostic chain."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid


class ToolRejected(ValueError):
    pass


class RepairTools:
    def __init__(self, work, artifacts, env, cpus, count_tokens, *, page_tokens=3500,
                 edit_paths=None):
        self.work = Path(work).resolve()
        self.artifacts = Path(artifacts)
        self.artifacts.mkdir(exist_ok=False)
        self.env, self.cpus, self.count_tokens = env, cpus, count_tokens
        self.page_tokens = page_tokens
        self.edit_paths = set(edit_paths) if edit_paths is not None else None
        self.outputs = {}

    def path(self, relative):
        p = Path(relative)
        if p.is_absolute() or '..' in p.parts or '.git' in p.parts:
            raise ToolRejected('path must be relative to checkout and outside .git')
        target = (self.work / p).resolve()
        if not target.is_relative_to(self.work):
            raise ToolRejected('path escapes checkout')
        return target

    def page(self, text, *, offset=0):
        if type(offset) is not int or not 0 <= offset <= len(text):
            raise ToolRejected('invalid output character offset')
        low, high = offset, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if self.count_tokens(text[offset:mid]) <= self.page_tokens:
                low = mid
            else:
                high = mid - 1
        return {'text': text[offset:low], 'visible_char_interval': [offset, low],
                'omitted_char_intervals': ([[0, offset]] if offset else []) +
                ([[low, len(text)]] if low < len(text) else []),
                'next_offset': low if low < len(text) else None,
                'total_chars': len(text)}

    def store_output(self, text):
        rid = uuid.uuid4().hex
        self.outputs[rid] = text
        (self.artifacts / (rid + '.txt')).write_text(text)
        return {'result_id': rid, 'raw_output_sha256': hashlib.sha256(text.encode()).hexdigest(),
                **self.page(text)}

    def syntax(self, paths):
        checks = []
        for path in sorted(set(paths)):
            if not path.endswith('.py'):
                continue
            start = time.monotonic_ns()
            # Use the TASK interpreter; do not write pyc or repair the candidate.
            code = ('import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
                    'compile(p.read_bytes(),str(p),"exec")')
            proc = subprocess.run([str(Path(self.env['PATH'].split(':')[0]) / 'python'),
                                   '-c', code, path], cwd=self.work, env=self.env,
                                  capture_output=True, text=True, timeout=20)
            output = proc.stdout + proc.stderr
            if proc.returncode:
                # Actual error and nearby candidate source; no suggested fix.
                import re
                match = re.search(r'line (\d+)', output)
                if match:
                    line = int(match[1]); source = self.path(path).read_text().splitlines()
                    output += '\n' + '\n'.join(f'{i+1}: {source[i]}'
                                               for i in range(max(0, line-4), min(len(source), line+3)))
            checks.append({'path': path, 'returncode': proc.returncode, 'output': output,
                           'started_ns': start, 'ended_ns': time.monotonic_ns(),
                           'purpose': 'automatic_syntax_only_not_behavior_validation'})
        return checks

    def edit(self, edits):
        if not isinstance(edits, list) or not 1 <= len(edits) <= 12:
            raise ToolRejected('provide 1..12 exact replacements')
        originals, staged = {}, {}
        for e in edits:
            name, old, new = e['path'], e['old'], e['new']
            if self.edit_paths is not None and name not in self.edit_paths:
                raise ToolRejected('L1 edits outside preregistered production files')
            p = self.path(name)
            if not p.is_file() or not isinstance(old, str) or not old or not isinstance(new, str):
                raise ToolRejected('edit requires existing text file and nonempty old string')
            original = originals.setdefault(name, p.read_text())
            text = staged.get(name, original)
            count = text.count(old)
            if count != 1:
                raise ToolRejected(f'{name}: old text occurs {count} times, expected exactly one; no writes applied')
            if old == new:
                raise ToolRejected('no-op replacement; no writes applied')
            staged[name] = text.replace(old, new, 1)
        for name, text in staged.items():
            self.path(name).write_text(text)
        return {'changed_files': sorted(staged), 'automatic_syntax': self.syntax(staged),
                'old_sha256': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in originals.items()},
                'new_sha256': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in staged.items()}}

    def execute(self, action):
        op = action['op']
        if op == 'edit':
            return self.edit(action['edits'])
        if op == 'read':
            name = action['path']; lines = self.path(name).read_text().splitlines()
            start, end = action.get('start', 1), action.get('end', len(lines))
            if type(start) is not int or type(end) is not int or not 1 <= start <= end or end-start >= 120:
                raise ToolRejected('read 1..120 lines; request next range explicitly')
            end = min(end, len(lines))
            result = self.store_output('\n'.join(f'{i}: {lines[i-1]}' for i in range(start, end+1)))
            return {**result, 'path': name, 'requested_lines': [start, end], 'file_lines': len(lines),
                    'omitted_line_intervals': ([[1, start-1]] if start > 1 else []) +
                    ([[end+1, len(lines)]] if end < len(lines) else [])}
        if op == 'output':
            rid = action['result_id']
            if rid not in self.outputs:
                raise ToolRejected('unknown output result_id')
            return {'result_id': rid, **self.page(self.outputs[rid], offset=action['offset'])}
        if op == 'shell':
            start = time.monotonic_ns()
            try:
                p = subprocess.run(['taskset', '-c', ','.join(map(str, self.cpus)),
                                    '/bin/bash', '-c', action['command']], cwd=self.work,
                                   env=self.env, capture_output=True, text=True, timeout=90)
                code, output = p.returncode, p.stdout + p.stderr
            except subprocess.TimeoutExpired as error:
                code = -1
                output = 'TOOL TIMEOUT\n' + (error.stdout or b'').decode(errors='replace') + (error.stderr or b'').decode(errors='replace')
            return {'returncode': code, 'started_ns': start, 'ended_ns': time.monotonic_ns(),
                    **self.store_output(output)}
        if op == 'diff':
            p = subprocess.run(['git', 'diff', 'HEAD'], cwd=self.work, capture_output=True, text=True, check=True)
            return self.store_output(p.stdout)
        if op == 'submit':
            return {'submitted': True, 'summary': action.get('summary', ''),
                    'meaning': 'agent ended; independent acceptance still required'}
        raise ToolRejected('unknown op; use read, edit, shell, output, diff, submit')


def decode_action(content):
    """No heuristic JSON repairs; malformed output is a recorded failed action."""
    text = content.strip()
    if text.startswith('```json\n') and text.endswith('\n```'):
        text = text[len('```json\n'):-len('\n```')]
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ToolRejected('one JSON action object required')
    return result
