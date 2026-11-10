"""Count resident file pages with bounded virtual address usage; never touch data."""
import ctypes as C
import os
from pathlib import Path


def resident_bytes(path, size, window_bytes=32 * 1024**2):
    if not size:
        return 0
    page = os.sysconf('SC_PAGE_SIZE')
    if window_bytes <= 0 or window_bytes % page:
        raise ValueError('residency window must be page aligned')
    lib = C.CDLL(None, use_errno=True)
    lib.mmap.argtypes = [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
    lib.mmap.restype = C.c_void_p
    lib.mincore.argtypes = [C.c_void_p, C.c_size_t, C.c_void_p]
    lib.munmap.argtypes = [C.c_void_p, C.c_size_t]
    table = bytes(value & 1 for value in range(256))
    total = 0
    with Path(path).open('rb') as stream:
        if os.fstat(stream.fileno()).st_size < size:
            raise ValueError('residency request exceeds file size')
        for offset in range(0, size, window_bytes):
            length = min(window_bytes, size - offset)
            vector = C.create_string_buffer((length + page - 1) // page)
            address = lib.mmap(None, length, 1, 1, stream.fileno(), offset)
            if address == C.c_void_p(-1).value:
                raise OSError(C.get_errno(), 'windowed mincore mmap')
            try:
                if lib.mincore(address, length, vector):
                    raise OSError(C.get_errno(), 'windowed mincore')
                total += vector.raw.translate(table).count(b'\x01') * page
                if vector.raw[-1] & 1:
                    total -= (page - length % page) % page
            finally:
                lib.munmap(address, length)
    return total
