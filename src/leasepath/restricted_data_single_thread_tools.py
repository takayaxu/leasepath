"""Single numerical thread recipe; two-thread sources and observations remain frozen.

Only the evidence record carries timings. Model-facing answers and capabilities
are identical to SessionTools; resource validation and timeout are unchanged.
"""
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from jsonschema import Draft202012Validator
from .restricted_data_tools import SessionTools, ALLOWED_KEYS, open_resource


class SingleThreadSessionTools(SessionTools):
    def __init__(self, *args, controls, session_key, **kwargs):
        if kwargs.get('threads') != 1:
            raise ValueError('new recipe requires exactly one numerical thread')
        super().__init__(*args, **kwargs)
        self.controls = dict(controls)
        self.session_key = session_key

    def call(self, name, arguments):
        prior_affinity = os.sched_getaffinity(0)
        os.sched_setaffinity(0, self.controls['cpus'])
        try:
            return self._controlled_call(name, arguments)
        finally:
            os.sched_setaffinity(0, prior_affinity)

    def _controlled_call(self, name, arguments):
        started = time.monotonic_ns()
        thread_started = time.thread_time_ns()
        deadline=time.monotonic()+self.timeout
        record = {'call_id': 't'+uuid.uuid4().hex, 'name': name, 'arguments': arguments,
                  'started_ns': started}
        fd = None
        try:
            if name not in self.schema:
                raise ValueError('unknown tool')
            # jsonschema regards bool as non-integer; also reject nonfinite JSON.
            json.dumps(arguments, allow_nan=False)
            Draft202012Validator(self.schema[name]).validate(arguments)
            if name == 'view_artifact':
                identifier = arguments['artifact_id']
                if identifier not in self.artifacts:
                    raise ValueError('unknown session artifact')
                result = self.artifacts[identifier]
                # Non-tabular results are one object, not an empty table. Nested
                # arrays remain complete inside result and are not row pagination.
                rows = result['rows'] if 'rows' in result else [result]
                answer = {'artifact_id': identifier, 'result': result,
                          'page': rows[arguments['offset']:arguments['offset']+arguments['limit']],
                          'row_count': len(rows),'complete_result_returned':True,
                          'pagination_unit':'rows' if 'rows' in result else 'result_object'}
            else:
                identifier = arguments['dataset_id']
                if identifier not in self.resources:
                    raise ValueError('unknown session dataset')
                resource = self.resources[identifier]
                check_start = time.monotonic_ns()
                check_cpu = time.thread_time_ns()
                fd, before = open_resource(resource.path, resource.sha256)
                record['input_check_ns'] = time.monotonic_ns()-check_start
                record['input_check_thread_cpu_ns'] = time.thread_time_ns()-check_cpu
                if name == 'inspect_input':
                    answer = {'dataset_id': identifier, **resource.metadata}
                else:
                    op = arguments['operation']
                    if op not in self.operations:
                        raise ValueError('operation not available in this session')
                    supplied = set(arguments)-{'dataset_id', 'operation'}
                    if supplied != ALLOWED_KEYS[op]:
                        raise ValueError('missing, unknown or irrelevant operation parameters')
                    request = {'arguments': arguments, 'kind': resource.kind,
                               'fd': fd, 'threads': self.threads, 'memory_gib': self.memory_gib,
                               'controls': self.controls}
                    env = {**os.environ, 'OMP_NUM_THREADS': str(self.threads),
                           'OPENBLAS_NUM_THREADS': str(self.threads), 'MKL_NUM_THREADS': str(self.threads),
                           'LEASEPATH_TOOL_TIMING_ENVELOPE':'1'}
                    child_started = time.monotonic_ns()
                    proc = subprocess.Popen([sys.executable, '-m', 'leasepath.restricted_data_thread_probe_worker'],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, pass_fds=(fd,), env=env, start_new_session=True)
                    record['child_pid'] = proc.pid
                    try:
                        stdout, stderr = proc.communicate(json.dumps(request), timeout=max(0,deadline-time.monotonic()))
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.communicate()
                        raise TimeoutError('tool child process group killed and reaped')
                    record['child_wall_ns'] = time.monotonic_ns()-child_started
                    record['child_stderr'] = stderr[-4000:]
                    record['child_returncode'] = proc.returncode
                    if proc.returncode:
                        raise ValueError('compute failed: '+stderr[-1000:])
                    envelope = json.loads(stdout)
                    result = envelope['result']
                    record['worker_execute_ns'] = envelope['worker_execute_ns']
                    record['worker_timing'] = envelope['worker_timing']
                    record['worker_controls'] = envelope['worker_controls']
                    record['thread_diagnostics'] = envelope['thread_diagnostics']
                    record['child_launch_import_exit_ns'] = record['child_wall_ns']-record['worker_execute_ns']
                    after = os.fstat(fd)
                    if before != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError('concurrent source write')
                    check_start = time.monotonic_ns()
                    check_cpu = time.thread_time_ns()
                    post_fd, post_info = open_resource(resource.path, resource.sha256)
                    os.close(post_fd)
                    if post_info != before:
                        raise ValueError('source changed during compute')
                    record['post_input_check_ns'] = time.monotonic_ns()-check_start
                    record['post_input_check_thread_cpu_ns'] = time.thread_time_ns()-check_cpu
                    artifact = 'a'+uuid.uuid5(uuid.UUID('6f61fb66-9b43-4345-b0f4-c736dadc9cc2'),
                                             self.session_key+':'+str(len(self.artifacts))).hex
                    path = self.output/(artifact+'.json')
                    write_started = time.monotonic_ns()
                    with path.open('x') as stream:
                        json.dump(result, stream, allow_nan=False)
                        stream.write('\n')
                        stream.flush()
                        os.fsync(stream.fileno())
                    record['artifact_write_fsync_ns'] = time.monotonic_ns()-write_started
                    self.artifacts[artifact] = result
                    answer = {'artifact_id': artifact, 'result': result}
            record['status'] = 'ok'
        except Exception as exc:
            answer = {'error': type(exc).__name__, 'message': str(exc)[:800]}
            record['status'] = 'rejected' if isinstance(exc, (ValueError, KeyError)) else 'error'
        finally:
            if fd is not None:
                os.close(fd)
        record['ended_ns'] = time.monotonic_ns()
        record['call_thread_cpu_ns'] = time.thread_time_ns()-thread_started
        record['result'] = answer
        # No fsync for each metadata row. Each artifact fsynced before returning its ID.
        write_start = time.monotonic_ns()
        with (self.output/'calls.jsonl').open('a') as stream:
            stream.write(json.dumps(record, allow_nan=False)+'\n')
            stream.flush()
        record['call_evidence_write_ns'] = time.monotonic_ns()-write_start
        self.calls.append(record)
        return answer
