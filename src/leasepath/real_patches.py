"""Capture a task's complete patch against its frozen initial git tree."""
import subprocess


def capture_patch(workspace, initial_commit):
    command = ['git', '-C', str(workspace)]
    patch = subprocess.check_output(command + ['diff', '--binary', initial_commit, '--'])
    untracked = subprocess.check_output(command + ['ls-files', '--others', '--exclude-standard', '-z'])
    for name in untracked.split(b'\0'):
        if not name:
            continue
        result = subprocess.run(['git', 'diff', '--no-index', '--binary', '--', '/dev/null', name.decode()],
                                cwd=workspace, capture_output=True)
        if result.returncode not in (0, 1):
            raise ValueError('failed to capture untracked Agent file')
        patch += result.stdout
    return patch
