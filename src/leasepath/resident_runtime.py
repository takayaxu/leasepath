"""Read-only file residency and file access evidence for the new natural attempt.

mincore proves residency of file pages, not process PTEs or exclusive bandwidth.
No cache drops, privileged tracing or changes to other tenants are used.
"""
import ctypes
import os
from pathlib import Path
from .shared_experiment_residency import LockedFiles
from .prediction_training import sha256


class ResidentFiles(LockedFiles):
    def __init__(self, paths):
        unique = {}
        for path in map(Path, paths):
            path = path.resolve(strict=True)
            stat = path.stat()
            if not path.is_file():
                raise ValueError('not a regular residency file: '+str(path))
            unique.setdefault((stat.st_dev, stat.st_ino), path)
        super().__init__(unique.values())
        self.lib.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
        self.page_size = os.sysconf('SC_PAGE_SIZE')
        self.identities = {str(p): (p.stat().st_dev,p.stat().st_ino,p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns) for p in unique.values()}

    def verify(self):
        rows = []
        for row, (address,size) in zip(self.records,self.maps):
            path = Path(row['path']); stat = path.stat()
            if (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns) != self.identities[str(path)]:
                raise ValueError('resident file identity changed: '+str(path))
            count = (size+self.page_size-1)//self.page_size
            vector = (ctypes.c_ubyte*count)()
            if self.lib.mincore(address,size,vector):
                raise OSError(ctypes.get_errno(),'mincore '+str(path))
            absent = sum(not (value&1) for value in vector)
            rows.append({'path':str(path),'pages':count,'nonresident_pages':absent})
            if absent:
                raise RuntimeError('locked file has nonresident pages: '+str(path))
        return {'files':len(rows),'pages':sum(r['pages'] for r in rows),'nonresident_pages':sum(r['nonresident_pages'] for r in rows)}


class ResidentRuntime(ResidentFiles):
    def __init__(self, manifest):
        paths = []
        for row in manifest['files']:
            path = Path(manifest['dst'])/row['relative']
            if sha256(path) != row['sha256']:
                raise ValueError('runtime copy changed: '+str(path))
            paths.append(path)
        super().__init__(paths)


class ModelResidentKeeper:
    def __init__(self,path):
        import subprocess
        import sys
        self.proc=subprocess.Popen([sys.executable,'-m','leasepath.resident_file_keeper',str(path)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:self.initial=self._read()
        except BaseException:
            self.close()
            raise

    def _read(self):
        import json
        import select
        ready,_,_=select.select([self.proc.stdout],[],[],60)
        if not ready:raise TimeoutError('model residency keeper did not respond')
        line=self.proc.stdout.readline()
        if not line:raise RuntimeError('model residency keeper exited')
        result=json.loads(line)
        if result['status']!='ready' or result['residency']['nonresident_pages']:
            raise RuntimeError('model residency keeper failed')
        return result

    def verify(self):
        if self.proc.poll() is not None:raise RuntimeError('model residency keeper exited')
        self.proc.stdin.write('verify\n');self.proc.stdin.flush()
        return self._read()['residency']

    def close(self):
        self.proc.stdin.close()
        try:self.proc.wait(timeout=15)
        except __import__('subprocess').TimeoutExpired:
            self.proc.kill();self.proc.wait()
        self.proc.stdout.close()
