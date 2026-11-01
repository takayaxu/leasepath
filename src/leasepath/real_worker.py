"""Per-rank physical memory/config evidence, separate from logical demand."""
import os

from vllm_ascend.worker.worker import NPUWorker

from .real_probe import observer, close_observer


class ObservedNPUWorker(NPUWorker):
    def initialize_from_config(self, kv_cache_config):
        result = super().initialize_from_config(kv_cache_config)
        if os.environ.get('LEASEPATH_REAL_RAW') and os.environ.get('LEASEPATH_REAL_PROBE_MODE') != 'off':
            import torch
            free, total = torch.npu.mem_get_info()
            observer('rank', self.rank).emit(
                'rank_cache_config', rank=self.rank, local_rank=self.local_rank,
                num_blocks=kv_cache_config.num_blocks,
                kv_tensors=[{'size_bytes': t.size, 'shared_by': t.shared_by}
                            for t in kv_cache_config.kv_cache_tensors],
                physical_kv_bytes=sum(t.size for t in kv_cache_config.kv_cache_tensors),
                npu_free_bytes=free, npu_total_bytes=total,
                npu_allocated_bytes=torch.npu.memory_allocated(),
                npu_reserved_bytes=torch.npu.memory_reserved())
        return result

    def leasepath_checkpoint(self):
        if os.environ.get('LEASEPATH_REAL_RAW') and os.environ.get('LEASEPATH_REAL_PROBE_MODE') != 'off':
            log = observer('rank', self.rank)
            log.emit('rank_checkpoint', rank=self.rank)
            close_observer()
        return {'rank': self.rank, 'closed': True}

    def shutdown(self):
        close_observer()
        return super().shutdown()
