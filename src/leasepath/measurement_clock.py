"""Exclude immutable trace preparation from the benchmark's arrival window.

source_replay.run asks its clock once before preparing all prompt keys, then
again at its first readiness check. Anchor that second reading at time zero.
Per-request planning, GPU execution and probes remain inside the timed window.
"""
import time


class PreparedClock:
    def __init__(self,clock=time.perf_counter):
        self.clock=clock;self.origin=None;self.excluded_s=None

    def __call__(self):
        value=self.clock()
        if self.origin is None:self.origin=value;return value
        if self.excluded_s is None:self.excluded_s=value-self.origin
        return value-self.excluded_s
