"""Lock read-only file-backed pages without allocating a private COW duplicate."""
import ctypes
import os
from pathlib import Path
import shutil
from .prediction_training import sha256


class LockedFiles:
    def __init__(self, paths):
        self.maps, self.records = [], []
        self.lib = ctypes.CDLL(None, use_errno=True)
        self.lib.mmap.argtypes = [ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
        self.lib.mmap.restype = ctypes.c_void_p
        self.lib.mlock.argtypes = [ctypes.c_void_p,ctypes.c_size_t]
        self.lib.munmap.argtypes = [ctypes.c_void_p,ctypes.c_size_t]
        try:
            for path in dict.fromkeys(map(Path,paths)):
                size = path.stat().st_size
                if not size:
                    continue
                with path.open('rb') as stream:
                    # PROT_READ, MAP_SHARED: mlock holds the original immutable
                    # file pages. Writable MAP_PRIVATE can COW an entire model.
                    pointer = self.lib.mmap(None,size,1,1,stream.fileno(),0)
                if pointer == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_errno(),'readonly mmap '+str(path))
                if self.lib.mlock(pointer,size):
                    error = ctypes.get_errno()
                    self.lib.munmap(pointer,size)
                    raise OSError(error,'mlock '+str(path))
                self.maps.append((pointer,size))
                self.records.append({'path':str(path),'bytes':size,'locked':True,'mapping':'PROT_READ MAP_SHARED'})
        except BaseException:
            self.close()
            raise

    def close(self):
        for pointer,size in self.maps:
            self.lib.munmap(pointer,size)
        self.maps.clear()


class LockedRuntime(LockedFiles):
    def __init__(self, manifest):
        root = Path(manifest['dst'])
        paths = []
        for row in manifest['files']:
            path = root/row['relative']
            if sha256(path) != row['sha256']:
                raise ValueError('runtime copy changed: '+str(path))
            paths.append(path)
        super().__init__(paths)


class LockedInputs(LockedFiles):
    def __init__(self,tasks,directory):
        directory.mkdir(parents=True,exist_ok=False)
        copies = []
        for task in tasks:
            source = Path(task['file'])
            dest = directory/(task['id']+'.npz')
            shutil.copyfile(source,dest)
            digest = sha256(dest)
            if digest != task['input_sha256']:
                raise ValueError('input copy differs')
            dest.chmod(0o444)
            copies.append({'task':task['id'],'source':str(source),'path':str(dest),
                           'sha256':digest,'bytes':dest.stat().st_size,'mlock':True})
            task['file'] = str(dest)
        super().__init__([row['path'] for row in copies])
        self.records = copies
