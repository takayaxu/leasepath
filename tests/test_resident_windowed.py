import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from resident_windowed_v1 import resident_bytes


def test_partial_last_page_is_not_overcredited():
    with tempfile.TemporaryDirectory(dir='/dev/shm', prefix='leasepath-mincore-test-') as td:
        path = Path(td) / 'file'; path.write_bytes(b'X' * 5001)
        assert resident_bytes(path, 5001, 4096) == 5001


def test_file_larger_than_address_space_limit(tmp_path):
    code = '''import os,resource,sys,tempfile
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from resident_windowed_v1 import resident_bytes
with tempfile.TemporaryDirectory(dir='/dev/shm',prefix='leasepath-mincore-large-') as td:
 p=Path(td)/'sparse'
 with p.open('wb') as f:f.truncate(3*1024**3)
 resource.setrlimit(resource.RLIMIT_AS,(256*1024**2,256*1024**2))
 assert resident_bytes(p,p.stat().st_size)==0
 print('bounded resident check passed')
'''
    result = subprocess.run([sys.executable, '-c', code, str(Path(__file__).resolve().parents[1] / 'scripts')],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
