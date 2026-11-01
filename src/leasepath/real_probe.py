"""Opt-in vLLM stage-0 probe. Logical scheduler events occur once for TP.

Raw identities/tokens stay on the data disk. This probe observes decisions,
never selects blocks or changes request status. Rank memory is logged by
real_worker separately. A single full-attention group is explicitly required.
"""
import atexit
from contextvars import ContextVar
import functools
import os
from pathlib import Path
import time

from .real_observer import Observer

_installed = False
_observers = {}
_logging_gap_reported = False
_current_request = ContextVar('leasepath_request', default=None)


def observer(source='engine', rank=None):
    key = (os.getpid(), source, rank)
    if key not in _observers:
        root = Path(os.environ['LEASEPATH_REAL_RAW'])
        _observers[key] = Observer(root / f'{source}-{os.getpid()}.jsonl',
                             run_id=os.environ['LEASEPATH_REAL_RUN'], source=source, rank=rank)
        atexit.register(close_observer)
    return _observers[key]


def close_observer():
    for key, log in list(_observers.items()):
        if key[0] == os.getpid() and not log.closed:
            log.finish()


def emit(kind, **data):
    # Legacy logging-only diagnostics must make the run ineligible for labels.
    # Full overhead baselines use startup PROBE_MODE=off (no hooks installed).
    global _logging_gap_reported
    if Path(os.environ['LEASEPATH_REAL_RAW']).parent.joinpath('probe-disabled').exists():
        if not _logging_gap_reported:
            observer().emit('observation_gap', reason='logging_only_toggle_used')
            _logging_gap_reported = True
        return
    observer().emit(kind, **data)


def block(pool, value):
    return {'slot': value.block_id, 'generation': pool._lp_generations[value.block_id],
            'hash': value.block_hash.hex() if value.block_hash is not None else None,
            'ref_count': value.ref_cnt, 'is_null': value.is_null}


def groups(manager, values):
    return [[block(manager.block_pool, b) for b in group] for group in values]


def register():
    global _installed
    if (_installed or not os.environ.get('LEASEPATH_REAL_RAW') or
            os.environ.get('LEASEPATH_REAL_PROBE_MODE') == 'off'):
        return
    from vllm.v1.core.block_pool import BlockPool
    from vllm.v1.core.kv_cache_manager import KVCacheManager
    from vllm.v1.core.sched.scheduler import Scheduler
    from vllm.v1.engine.core import EngineCore
    _installed = True
    emit('probe_installed', version='real-probe-v1', compatibility_id=os.environ['LEASEPATH_REAL_COMPATIBILITY'],
         scope='logical_engine_TP_deduplicated; physical_rank_memory_separate')

    def wrap(cls, name, factory):
        original = getattr(cls, name)
        setattr(cls, name, functools.wraps(original)(factory(original)))

    def pool_init(original):
        def call(self, *args, **kwargs):
            original(self, *args, **kwargs)
            self._lp_generations = [0] * self.num_gpu_blocks
            emit('block_pool_init', num_blocks=self.num_gpu_blocks,
                 null_slot=self.null_block.block_id, hash_block_size=self.hash_block_size)
        return call

    def new_blocks(original):
        def call(self, count):
            # Record the actual allocator queue before it pops selected slots.
            # Candidate metadata is kept at the same physical block granularity.
            candidates = [block(self, b) for b in self.free_block_queue.get_all_free_blocks()]
            emit('allocation_candidates', request_id=_current_request.get(),
                 requested=count, ordered_candidates=candidates)
            result = original(self, count)
            for b in result:
                self._lp_generations[b.block_id] += 1
            emit('block_allocate', request_id=_current_request.get(),
                 blocks=[block(self, b) for b in result], free_blocks=self.get_num_free_blocks())
            return result
        return call

    def evict(original):
        def call(self, b):
            before = block(self, b)
            result = original(self, b)
            if result:
                emit('block_evict', request_id=_current_request.get(), before=before,
                     after=block(self, b), eligible_refcount_zero=before['ref_count'] == 0)
            return result
        return call

    def refs(name):
        def factory(original):
            def call(self, values, *args, **kwargs):
                values = list(values)
                before = [block(self, b) for b in values]
                result = original(self, values, *args, **kwargs)
                emit(name, request_id=_current_request.get(), before=before,
                     after=[block(self, b) for b in values], free_blocks=self.get_num_free_blocks())
                return result
            return call
        return factory

    def insert(original):
        def call(self, request, blocks, num_cached_blocks, num_full_blocks, *args, **kwargs):
            before = [block(self, b) for b in blocks[num_cached_blocks:num_full_blocks]]
            result = original(self, request, blocks, num_cached_blocks, num_full_blocks, *args, **kwargs)
            if before:
                emit('block_cache_insert', request_id=request.request_id, before=before,
                     after=[block(self, b) for b in blocks[num_cached_blocks:num_full_blocks]])
            return result
        return call

    def manager_init(original):
        def call(self, *args, **kwargs):
            original(self, *args, **kwargs)
            config = self.kv_cache_config
            if len(config.kv_cache_groups) != 1 or type(config.kv_cache_groups[0].kv_cache_spec).__name__ != 'FullAttentionSpec':
                raise RuntimeError('stage0 supports exactly one full-attention KV group')
            spec = config.kv_cache_groups[0].kv_cache_spec
            self._lp_block_size = spec.block_size
            emit('cache_config', block_size=spec.block_size, num_blocks=config.num_blocks,
                 page_size_bytes_per_layer_per_rank=spec.page_size_bytes,
                 layer_names=config.kv_cache_groups[0].layer_names,
                 prefix_caching=self.enable_caching)
        return call

    def enqueue(original):
        def call(self, request):
            emit('request_enqueue', request_id=request.request_id,
                 prompt_token_ids=list(request.prompt_token_ids), cache_salt=request.cache_salt,
                 compatibility_id=os.environ['LEASEPATH_REAL_COMPATIBILITY'])
            return original(self, request)
        return call

    def lookup(original):
        def call(self, request):
            start = time.monotonic_ns()
            result = original(self, request)
            blocks, count = result
            emit('cache_lookup', lookup_started_ns=start, request_id=request.request_id,
                 cached_tokens=count, blocks=groups(self, blocks.blocks),
                 preemptions=request.num_preemptions, lookup_duration_ns=time.monotonic_ns() - start)
            return result
        return call

    def request_scope(original):
        def call(self, request, *args, **kwargs):
            token = _current_request.set(request.request_id)
            try:
                return original(self, request, *args, **kwargs)
            finally:
                _current_request.reset(token)
        return call

    def release(original):
        def call(self, request):
            before = groups(self, self.get_blocks(request.request_id).blocks)
            size = self._lp_block_size
            length = request.num_prompt_tokens // size * size
            token = _current_request.set(request.request_id)
            try:
                result = original(self, request)
            finally:
                _current_request.reset(token)
            emit('request_release' if request.is_finished() else 'preemption_release',
                 request_id=request.request_id, status=str(request.status),
                 prefix_token_ids=list(request.prompt_token_ids[:length]),
                 output_token_ids=list(request.output_token_ids), blocks_before_release=before,
                 block_size=size, preemptions=request.num_preemptions,
                 num_computed_tokens=request.num_computed_tokens)
            return result
        return call

    def scheduled(original):
        def call(self, output):
            for rid, count in output.num_scheduled_tokens.items():
                request = self.requests[rid]
                prefill = min(count, max(0, request.num_prompt_tokens - request.num_computed_tokens))
                if prefill:
                    emit('prefill_scheduled', request_id=rid, tokens=prefill,
                         start_token=request.num_computed_tokens)
            return original(self, output)
        return call

    def reset(original):
        def call(self, *args, **kwargs):
            cached = [block(self, b) for b in self.blocks if b.block_hash is not None]
            result = original(self, *args, **kwargs)
            emit('cache_reset', success=result, cached_before=cached)
            return result
        return call

    def checkpoint(original):
        def call(self, method, *args, **kwargs):
            result = original(self, method, *args, **kwargs)
            if method == 'leasepath_checkpoint':
                emit('engine_checkpoint', outstanding_requests=len(self.scheduler.requests))
                if self.scheduler.requests:
                    raise RuntimeError('cannot close complete observation with pending requests')
                close_observer()
            return result
        return call

    wrap(BlockPool, '__init__', pool_init)
    wrap(BlockPool, 'get_new_blocks', new_blocks)
    wrap(BlockPool, '_maybe_evict_cached_block', evict)
    wrap(BlockPool, 'touch', refs('block_touch'))
    wrap(BlockPool, 'free_blocks', refs('block_free'))
    wrap(BlockPool, 'cache_full_blocks', insert)
    wrap(BlockPool, 'reset_prefix_cache', reset)
    wrap(KVCacheManager, '__init__', manager_init)
    wrap(KVCacheManager, 'get_computed_blocks', lookup)
    wrap(KVCacheManager, 'allocate_slots', request_scope)
    wrap(KVCacheManager, 'free', release)
    wrap(Scheduler, 'add_request', enqueue)
    wrap(Scheduler, '_update_after_schedule', scheduled)
    wrap(EngineCore, 'collective_rpc', checkpoint)
