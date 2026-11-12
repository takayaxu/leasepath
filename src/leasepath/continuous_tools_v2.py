"""Stable model-facing results; resource measurements remain audit evidence.

PID and measured CPU/fault counters must not alter prompts across policy arms.
They are recorded separately from semantic process results.
"""
import json
import time
from .continuous_tools_v1 import ProjectTools as OriginalProjectTools


class ProjectTools(OriginalProjectTools):
    def process(self,code,*,timeout=240):
        observed=super().process(code,timeout=timeout)
        measurements={key:observed.pop(key) for key in ('child_pid','lifetime') if key in observed}
        with (self.evidence/'process-resources.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(round=self.round,observed_ns=time.monotonic_ns(),**measurements),allow_nan=False)+'\n')
        return observed
