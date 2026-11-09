"""Extract kernel-opened executable and descriptor paths, including shebangs."""
from pathlib import Path
import re


def trace_paths(lines):
    found=set()
    for line in lines:
        executable=re.search(r'\bexecve\("(/[^"\\]+)".*\) = 0',line)
        names=[executable.group(1)] if executable else []
        if any(key in line for key in ('openat(','open(','read(','pread64(','mmap(')):
            names+=re.findall(r'\b[0-9]+<(/[^<>]+)>',line)
        for name in names:
            if name.startswith(('/proc/','/sys/')):continue
            path=Path(name)
            try:
                if not path.is_file():continue
                path=path.resolve();found.add(path)
                if executable:
                    with path.open('rb') as stream:head=stream.read(256)
                    if head.startswith(b'#!'):
                        interpreter=Path(head[2:].splitlines()[0].split()[0].decode())
                        if interpreter.is_file():found.add(interpreter.resolve())
            except (OSError,ValueError):continue
    return found
