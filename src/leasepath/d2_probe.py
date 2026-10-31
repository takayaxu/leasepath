"""Opt-in vLLM 0.23 probe for the single-card, full-attention D2 pilot.

Uses general-plugin hooks; never changes backend lookup/allocation decisions.
Raw backend identities are kept separate from the synthetic physical contract.
"""
import functools
import os
import time

from .d2_raw import writer

_installed = False


def _blocks(groups):
    return [[{'slot': b.block_id, 'hash': b.block_hash.hex() if b.block_hash else None,
              'ref_count': b.ref_cnt, 'is_null': b.is_null} for b in group] for group in groups]


def register():
    global _installed
    if _installed or not os.environ.get('LEASEPATH_D2_RAW'):
        return
    from vllm.v1.core.kv_cache_manager import KVCacheManager
    from vllm.v1.core.sched.scheduler import Scheduler
    _installed = True
    log = writer()
    log.emit('probe_installed', probe_version=1, compatibility_id=os.environ['LEASEPATH_D2_COMPATIBILITY'])

    def wrap(cls, name, replacement):
        original = getattr(cls, name)
        setattr(cls, name, functools.wraps(original)(replacement(original)))

    def init(original):
        def call(self, *args, **kwargs):
            original(self, *args, **kwargs)
            groups = self.kv_cache_config.kv_cache_groups
            if len(groups) != 1 or type(groups[0].kv_cache_spec).__name__ != 'FullAttentionSpec':
                raise RuntimeError('D2 pilot requires one full-attention cache group')
            self._lp_block_size = groups[0].kv_cache_spec.block_size
            log.emit('cache_config', block_size=self._lp_block_size,
                     num_blocks=self.kv_cache_config.num_blocks,
                     layers=list(groups[0].layer_names),
                     page_size_bytes=groups[0].kv_cache_spec.page_size_bytes,
                     prefix_caching=self.enable_caching)
        return call

    def add(original):
        def call(self, request):
            log.emit('request_enqueue', request_id=request.request_id,
                     prompt_token_ids=list(request.prompt_token_ids),
                     cache_salt=request.cache_salt)
            return original(self, request)
        return call

    def lookup(original):
        def call(self, request):
            start = time.monotonic_ns()
            log.emit('request_dispatch', request_id=request.request_id, ts_ns=start)
            result = original(self, request)
            blocks, count = result
            log.emit('cache_lookup', ts_ns=start, request_id=request.request_id,
                     prompt_token_ids=list(request.prompt_token_ids), cached_tokens=count,
                     blocks=_blocks(blocks.blocks), block_size=self._lp_block_size,
                     preemptions=request.num_preemptions,
                     lookup_duration_ns=time.monotonic_ns() - start)
            return result
        return call

    def scheduled(original):
        def call(self, output):
            for rid, count in output.num_scheduled_tokens.items():
                request = self.requests[rid]
                prefill = min(count, max(0, request.num_prompt_tokens - request.num_computed_tokens))
                request._lp_prefill = getattr(request, '_lp_prefill', 0) + prefill
                if prefill:
                    log.emit('prefill_scheduled', request_id=rid, tokens=prefill,
                             start_token=request.num_computed_tokens)
            return original(self, output)
        return call

    def free(original):
        def call(self, request):
            finished = request.is_finished()
            groups = self.get_blocks(request.request_id).blocks
            retained = [list(group) for group in groups]
            size = self._lp_block_size
            # Object selection is fixed before any future request: the longest
            # complete cached prefix within this request's input prompt.
            length = request.num_prompt_tokens // size * size
            prefix = list(request.prompt_token_ids[:length])
            result = original(self, request)
            log.emit('request_release' if finished else 'preemption_release',
                     request_id=request.request_id, status=str(request.status),
                     prefix_token_ids=prefix,
                     prompt_token_ids=list(request.prompt_token_ids),
                     output_token_ids=list(request.output_token_ids),
                     blocks=_blocks(retained), block_size=size,
                     scheduled_prefill_tokens=getattr(request, '_lp_prefill', 0),
                     num_computed_tokens=request.num_computed_tokens,
                     preemptions=request.num_preemptions)
            return result
        return call

    wrap(KVCacheManager, '__init__', init)
    wrap(Scheduler, 'add_request', add)
    wrap(KVCacheManager, 'get_computed_blocks', lookup)
    wrap(Scheduler, '_update_after_schedule', scheduled)
    wrap(KVCacheManager, 'free', free)
