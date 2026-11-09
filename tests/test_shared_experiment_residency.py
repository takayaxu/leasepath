from pathlib import Path
import pytest
from leasepath.shared_experiment_residency import LockedFiles


def test_locks_original_readonly_file_pages_and_closes(tmp_path):
    path=tmp_path/'pages';path.write_bytes(bytes(65536))
    guard=LockedFiles([path])
    assert len(guard.maps)==1
    lines=Path('/proc/self/smaps').read_text().splitlines()
    block=[];active=False
    for line in lines:
        if '-' in line.split(' ',1)[0]:active=str(path) in line
        if active:block.append(line)
    assert 'r--s' in block[0]
    assert int(next(l for l in block if l.startswith('Anonymous:')).split()[1])==0
    assert int(next(l for l in block if l.startswith('Locked:')).split()[1])==64
    guard.close();assert guard.maps==[]
    guard.close()


def test_lock_failure_releases_earlier_mappings(tmp_path):
    path=tmp_path/'pages';path.write_bytes(bytes(4096))
    with pytest.raises(FileNotFoundError):LockedFiles([path,tmp_path/'absent'])
