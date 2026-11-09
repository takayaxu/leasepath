"""Dedicated non-GPU model-page keeper; each process respects its lock limit."""
import json
from pathlib import Path
import resource
import sys
from .resident_runtime import ResidentFiles


def main():
    guard=ResidentFiles([Path(sys.argv[1])])
    try:
        def answer():
            print(json.dumps({'status':'ready','residency':guard.verify(),
                'records':guard.records,'process_status':Path('/proc/self/status').read_text(),
                'mlock_limit_bytes':resource.getrlimit(resource.RLIMIT_MEMLOCK)}),flush=True)
        answer()
        for line in sys.stdin:
            if line.strip()=='verify':answer()
            else:raise ValueError('unknown keeper command')
    finally:guard.close()

if __name__=='__main__':main()
