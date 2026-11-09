"""Real calculations with a declared response window, for a separate mechanism test.

Natural tool JCT must be reported separately. An overrun fails the experiment;
results are never repaired, replayed or delivered early to hide extra work.
"""
import json
import time
from .restricted_data_single_thread_tools import SingleThreadSessionTools


class ToolWindowOverrun(RuntimeError):
    pass


class WindowSessionTools(SingleThreadSessionTools):
    def __init__(self, *args, response_windows, delivery_jitter_s=0.1, release_guard=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.response_windows = dict(response_windows)
        self.delivery_jitter_s = delivery_jitter_s
        self.release_guard = release_guard or (lambda: None)
        if any(v <= 0 for v in self.response_windows.values()):
            raise ValueError('response windows must be positive')

    def call(self, name, arguments):
        started = time.monotonic_ns()
        if name not in self.response_windows:
            raise ValueError('unplanned response window')
        deadline = started+round(self.response_windows[name]*1e9)
        answer = super().call(name, arguments)
        self.release_guard()
        ready = time.monotonic_ns()
        if ready < deadline:
            time.sleep((deadline-ready)/1e9)
        delivered = time.monotonic_ns()
        record = self.calls[-1]
        record['response_window'] = {'started_ns': started, 'result_ready_ns': ready,
            'deadline_ns': deadline, 'delivered_ns': delivered,
            'budget_s': self.response_windows[name], 'added_wait_ns': delivered-ready,
            'compute_overrun': ready > deadline, 'delivery_lateness_ns': max(0,delivered-deadline),
            'scope': 'controlled response only; natural computation ends at original ended_ns'}
        # The session protocol permits one call at a time. Rewrite the small log
        # after release so saved calls and quality records retain the same envelope.
        write_started = time.monotonic_ns()
        with (self.output/'calls.jsonl').open('w') as stream:
            for call in self.calls:
                stream.write(json.dumps(call, allow_nan=False)+'\n')
            stream.flush()
        record['window_log_write_ns'] = time.monotonic_ns()-write_started
        returned = time.monotonic_ns()
        record['response_window']['returned_ns'] = returned
        record['response_window']['return_lateness_ns'] = max(0, returned-deadline)
        if ready > deadline or returned-deadline > self.delivery_jitter_s*1e9:
            raise ToolWindowOverrun('real result or delivery missed the fixed tool response window')
        return answer
