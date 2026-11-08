"""CPU-only diagnostic: disable THP for this child, then run frozen computation.

No host sysctl or other process is modified. Not used by the GPU matrix.
"""
import ctypes
from pathlib import Path


def main():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    # Linux PR_SET_THP_DISABLE / PR_GET_THP_DISABLE. Zero other arguments.
    if libc.prctl(41, 1, 0, 0, 0) != 0 or libc.prctl(42, 0, 0, 0, 0) != 1:
        raise OSError(ctypes.get_errno(), 'per-process THP disable failed')
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if fields.get('THP_enabled', '').strip() != '0':
        raise ValueError('kernel did not confirm per-process THP disable')
    from .restricted_data_timed_worker import main as compute
    compute()


if __name__ == '__main__':
    main()
