import subprocess

from leasepath.real_patches import capture_patch


def test_patch_includes_agent_commits_index_and_untracked_files(tmp_path):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(tmp_path), *args])
    git('init', '-q')
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@localhost')
    (tmp_path / 'source.py').write_text('original\n')
    git('add', '.')
    git('commit', '-qm', 'initial')
    initial = git('rev-parse', 'HEAD').decode().strip()
    (tmp_path / 'source.py').write_text('agent commit\n')
    git('commit', '-qam', 'agent')
    (tmp_path / 'indexed.py').write_text('staged\n')
    git('add', 'indexed.py')
    (tmp_path / 'new.py').write_text('untracked\n')
    patch = capture_patch(tmp_path, initial)
    git('reset', '--hard', initial)
    (tmp_path / 'new.py').unlink()
    subprocess.run(['git', 'apply', '-'], cwd=tmp_path, input=patch, check=True)
    assert (tmp_path / 'source.py').read_text() == 'agent commit\n'
    assert (tmp_path / 'indexed.py').read_text() == 'staged\n'
    assert (tmp_path / 'new.py').read_text() == 'untracked\n'
