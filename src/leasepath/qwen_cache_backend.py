"""Native multi-owner KV enactment with independently checked physical cells."""
import ctypes as C
from copy import deepcopy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

from .cache import block_keys
from .predictive_cache import PredictiveCache
from .qwen_data_backend import verify_request


def check_physical(snapshot, expected, *, block_size, capacity, headroom, active=False):
    """expected maps native slot to actual (position, token) pairs, not caller counts."""
    seqs = snapshot['sequences']
    if not active and '0' in seqs:
        raise ValueError('native active sequence leaked')
    present = {int(s) for s in seqs if s != '0'}
    if present != set(expected):
        raise ValueError('native/controller slot membership differs')
    union = set()
    for slot, pairs in expected.items():
        rows = seqs[str(slot)]
        if [(r[0], r[2]) for r in rows] != pairs or len(rows) != block_size:
            raise ValueError('native block positions/tokens differ')
        cells = {r[1] for r in rows}
        if len(cells) != block_size or cells & union:
            raise ValueError('different logical blocks alias physical cells')
        union.update(cells)
    if snapshot['idle_union_cells'] != len(union) or len(union) > capacity * block_size:
        raise ValueError('idle payload quota/mapping differs')
    if not active and snapshot['used_cells'] != len(union):
        raise ValueError('unaccounted native cells')
    if snapshot['used_cells'] > capacity * block_size + headroom:
        raise ValueError('active payload quota exceeded')
    if not snapshot['device_kv_reserved_bytes'] or any(v <= 0 for v in snapshot['device_kv_reserved_bytes'].values()):
        raise ValueError('missing physical device KV allocation')


class MultiOwnerEngine:
    def __init__(self, library, model, tokenizer, *, capacity=64, context=16384,
                 block_size=128, headroom=2048, policy='retired_first_lru',
                 predictor=None, compatibility='qwen32b-cache-stage2', lock_path=None):
        if not 0 < capacity <= 254 or context < capacity*block_size+headroom:
            raise ValueError('invalid native capacity/headroom')
        visible = os.environ.get('CUDA_VISIBLE_DEVICES', '').split(',')
        if visible != ['5', '6', '7']:
            raise ValueError('stage2 is restricted to GPU 5/6/7')
        self.lock_fd = None
        if lock_path:
            self.lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observed = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,memory.used,memory.total',
                                            '--format=csv,noheader,nounits'], text=True)
        self.resource_preflight = []
        for line in observed.splitlines():
            index, uuid, used, total = [s.strip() for s in line.split(',')]
            if index in visible:
                if int(used) > 256 or int(total)-int(used) < 30000:
                    raise ValueError('selected GPU is no longer idle: ' + index)
                self.resource_preflight.append({'index': index, 'uuid': uuid, 'used_mib': int(used), 'total_mib': int(total)})
        if len(self.resource_preflight) != 3:
            raise ValueError('GPU resolution differs')
        self.tokenizer = tokenizer
        self.capacity, self.block_size, self.headroom = capacity, block_size, headroom
        self.compatibility = compatibility
        self.policy, self.predictor = policy, predictor
        self.owner = None
        self.history = []
        self._reset_controller()
        self.lib = C.CDLL(str(Path(library).resolve()))
        self.lib.lp_qd_error.restype = C.c_char_p
        self.lib.lp_qm_open.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_int, C.c_uint, C.c_uint, C.c_uint, C.c_uint]
        self.lib.lp_qm_open.restype = C.c_void_p
        self.lib.lp_qm_close.argtypes = [C.c_void_p]
        self.lib.lp_qd_cancel.argtypes = [C.c_void_p]
        self.lib.lp_qm_inspect.argtypes = [C.c_void_p]
        self.lib.lp_qm_inspect.restype = C.c_char_p
        self.lib.lp_qm_drop.argtypes = [C.c_void_p, C.c_char_p]
        self.lib.lp_qm_drop.restype = C.c_char_p
        self.lib.lp_qm_generate.argtypes = [C.c_void_p, C.c_char_p, C.c_int, C.c_uint, C.c_int64, C.c_char_p]
        self.lib.lp_qm_generate.restype = C.c_char_p
        self.ptr = self.lib.lp_qm_open(str(Path(model).resolve()).encode(), context, 512, 8, 3,
                                       capacity, block_size, headroom)
        if not self.ptr:
            self.close()
            raise RuntimeError(self.lib.lp_qd_error().decode())
        self.inspect()

    def _reset_controller(self):
        self.cache = PredictiveCache(self.capacity, block_size=self.block_size, block_bytes=262144*self.block_size,
                                     policy=self.policy, predictor=self.predictor, horizon_s=3.)
        self.slots, self.expected = {}, {}

    def _read(self, result):
        if result is None:
            raise RuntimeError(self.lib.lp_qd_error().decode())
        return json.loads(result)

    def inspect(self):
        result = self._read(self.lib.lp_qm_inspect(self.ptr))
        check_physical(result, self.expected, block_size=self.block_size, capacity=self.capacity, headroom=self.headroom)
        return result

    def clear(self):
        self._read(self.lib.lp_qm_drop(self.ptr, json.dumps(list(range(1, self.capacity+1))).encode()))
        self._reset_controller()
        return self.inspect()

    def retire(self, owner):
        self.cache.retire(owner, time.monotonic())
        return self.inspect()

    def observe(self, owner, features):
        self.cache.observe(owner, features, time.monotonic())

    def generate(self, prompt, *, limit=1024, timeout=180, stops=()):
        if not self.owner:
            raise ValueError('explicit live owner required')
        tokens = self.tokenizer.encode(prompt, add_special_tokens=False)
        size = self.block_size
        count = (len(tokens)-1)//size
        if count < 1:
            raise ValueError('native cache gate requires at least one complete block')
        keys = block_keys(tokens[:count*size], self.compatibility, size)
        control_start = time.monotonic_ns()
        before = self.inspect()
        decision = self.cache.start(self.owner, keys, time.monotonic())
        evicted = []
        for row in decision['evicted']:
            slot = self.slots.pop(row['key'])
            evicted.append(slot)
            self.expected.pop(slot)
        if evicted:
            dropped = self._read(self.lib.lp_qm_drop(self.ptr, json.dumps(evicted).encode()))
            check_physical(dropped, self.expected, block_size=size, capacity=self.capacity, headroom=self.headroom)
        available = iter(s for s in range(1, self.capacity+1) if s not in self.slots.values())
        for key in keys:
            if key not in self.slots:
                self.slots[key] = next(available)
        plan = {'slots': [self.slots[k] for k in keys], 'stops': list(stops)}
        prepared_ns = time.monotonic_ns() - control_start
        deadline = time.monotonic_ns() + int(timeout*1e9)
        encoded = prompt.encode()
        try:
            native = self._read(self.lib.lp_qm_generate(self.ptr, encoded, len(encoded), limit, deadline,
                                                        json.dumps(plan).encode()))
        except RuntimeError:
            # Fatal native adapter errors explicitly clear the full pool.
            self._reset_controller()
            self.inspect()
            raise
        if native['hit'] != decision['cached_blocks']*size or native['tokens'] != tokens:
            raise ValueError('native hits/token IDs differ from policy plan')
        if native['reason'] in {'eog', 'protocol_stop', 'length'}:
            for i, key in enumerate(keys):
                self.expected[self.slots[key]] = [(j, tokens[j]) for j in range(i*size, (i+1)*size)]
            check_physical(native['after'], self.expected, block_size=size, capacity=self.capacity, headroom=self.headroom)
            features = {'elapsed_idle_s': 0., 'compatibility_id': self.compatibility,
                'prefix_tokens': len(keys)*size, 'tool_name': None, 'tool_elapsed_s': None,
                'phase': None, 'retry_count': None, 'is_queued': None, 'queue_length': None}
            self.cache.release(self.owner, features, time.monotonic())
        else:
            # Native abort drops sequence 0 and never publishes new persistent blocks.
            # Remove the unmaterialized suffix and active metadata, preserving others.
            check_physical(native['after'], self.expected, block_size=size, capacity=self.capacity, headroom=self.headroom)
            for key, slot in list(self.slots.items()):
                if slot not in self.expected:
                    del self.slots[key]
                    del self.cache.blocks[key]
            for block in self.cache.blocks.values():
                block.active.discard(self.owner)
            self.cache.bindings.pop(self.owner, None)
            self.cache.idle.pop(self.owner, None)
            self.cache.check()
        result = {**native, 'owner': self.owner, 'policy_decision': decision,
                  'controller_prepare_ns': prepared_ns, 'physical_before': before,
                  'native_evidence': deepcopy(native),
                  'lookup_cells': [[r[1], r[2]] for r in native['lookup_cells']],
                  'release_cells': [[r[1], r[2]] for r in native['release_cells']],
                  'device_kv_bytes': native['after']['device_kv_reserved_bytes']}
        verify_request(result, tokens)
        self.history.append(result)
        return result

    def cancel(self):
        self.lib.lp_qd_cancel(self.ptr)

    def close(self):
        if getattr(self, 'ptr', None):
            self.lib.lp_qm_close(self.ptr)
            self.ptr = None
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None
