"""Pinned CUDA adapter: single native engine, exact token and physical cell evidence."""
import ctypes as C
import json
from pathlib import Path
import time

PINNED_LLAMA = 'fd27b2034905f8dadd8bd6d2cc44595f4929d391'

class NativeEngine:
    def __init__(self, library, model, *, context=16384, batch=512, threads=8, gpus=3):
        self.lib = C.CDLL(str(Path(library).resolve()))
        self.lib.lp_qd_error.restype = C.c_char_p
        self.lib.lp_qd_open.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_int, C.c_uint]
        self.lib.lp_qd_open.restype = C.c_void_p
        self.lib.lp_qd_close.argtypes = [C.c_void_p]
        self.lib.lp_qd_clear.argtypes = [C.c_void_p]
        self.lib.lp_qd_cancel.argtypes = [C.c_void_p]
        self.lib.lp_qd_generate.argtypes = [C.c_void_p, C.c_char_p, C.c_int,
                                          C.c_uint, C.c_int64, C.c_char_p]
        self.lib.lp_qd_generate.restype = C.c_char_p
        self.ptr = self.lib.lp_qd_open(str(Path(model).resolve()).encode(), context, batch, threads, gpus)
        if not self.ptr:
            raise RuntimeError(self.lib.lp_qd_error().decode())

    def generate(self, prompt, *, limit=1024, timeout=180, stops=()):
        if not self.ptr:
            raise RuntimeError('closed engine')
        data = prompt.encode()
        deadline = time.monotonic_ns() + int(timeout * 1e9)
        result = self.lib.lp_qd_generate(self.ptr, data, len(data), limit, deadline,
                                          json.dumps(list(stops)).encode())
        if result is None:
            raise RuntimeError(self.lib.lp_qd_error().decode())
        return json.loads(result)

    def cancel(self):
        self.lib.lp_qd_cancel(self.ptr)

    def clear(self):
        if self.lib.lp_qd_clear(self.ptr):
            raise RuntimeError(self.lib.lp_qd_error().decode())

    def close(self):
        if self.ptr:
            self.lib.lp_qd_close(self.ptr)
            self.ptr = None

def verify_request(request, expected_tokens):
    """Independent Python validation of engine counters and physical token references."""
    if request['tokens'] != expected_tokens:
        raise ValueError('client and engine prompt token IDs differ')
    n = len(expected_tokens)
    hit = request['hit']
    if not 0 <= hit < n or request['prefill'] > n-hit:
        raise ValueError('invalid lookup counters')
    if request['reason'] in ('eog', 'protocol_stop', 'length'):
        if request['prefill'] + hit != n:
            raise ValueError('incomplete prefill on completed request')
        cells = request['release_cells']
        if len(cells) != n or [c[1] for c in cells] != expected_tokens:
            raise ValueError('release has no exact physical input prefix')
        if len({c[0] for c in cells}) != n:
            raise ValueError('duplicate physical cell')
        if request['lookup_cells'][:hit] != cells[:hit]:
            raise ValueError('hit cells do not survive unchanged')
    elif request['release_cells']:
        raise ValueError('failed attempt fabricated idle cells')
    if not request['enqueue_ns'] <= request['dispatch_ns'] <= request['lookup_ns'] < request['release_ns']:
        raise ValueError('engine stage clock order')
    if not request['device_kv_bytes'] or any(n <= 0 for n in request['device_kv_bytes'].values()):
        raise ValueError('missing device KV evidence')
    return True
