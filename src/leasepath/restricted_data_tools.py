"""Capability-limited data operations. Model arguments never select code or paths."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

from jsonschema import Draft202012Validator

def object_schema(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required,
            'additionalProperties': False}

ID = {'type': 'string', 'pattern': '^[a-z][a-z0-9_-]{0,63}$', 'maxLength': 64}
FIELD = {'type': 'string', 'enum': [
    'l_returnflag', 'l_linestatus', 'l_quantity', 'l_extendedprice', 'l_discount',
    'l_shipmode', 'o_orderpriority', 'o_totalprice', 'c_mktsegment',
    'revenue', 'n', 'group', 'sum_quantity', 'total_price']}
COMPUTE = object_schema({
    'dataset_id': ID,
    'operation': {'enum': ['group_sum', 'filtered_count', 'join_sum', 'window_rank',
                           'pca', 'kmeans', 'random_forest', 'logistic', 'ridge_cv',
                           'forest_cv', 'histboost_cv', 'svc_cv', 'extra_trees_cv']},
    'group_by': FIELD,
    'value': FIELD,
    'filter_value': {'type': 'string', 'maxLength': 24, 'pattern': '^[A-Z0-9 _-]*$'},
    'components': {'type': 'integer', 'minimum': 1, 'maximum': 8},
    'clusters': {'type': 'integer', 'minimum': 2, 'maximum': 12},
    'trees': {'type': 'integer', 'minimum': 10, 'maximum': 400},
    'folds': {'type': 'integer', 'minimum': 3, 'maximum': 5},
    'iterations': {'type': 'integer', 'minimum': 50, 'maximum': 300},
    'kernel': {'enum': ['linear', 'rbf']},
    'alpha': {'type': 'number', 'minimum': 0.01, 'maximum': 100.0},
}, required=['dataset_id', 'operation'])
SCHEMAS = {
    'inspect_input': object_schema({'dataset_id': ID}),
    'compute': COMPUTE,
    'view_artifact': object_schema({'artifact_id': ID,
                                    'offset': {'type': 'integer', 'minimum': 0, 'maximum': 100},
                                    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 10}})}
ALLOWED_KEYS = {
    'group_sum': {'group_by', 'value'}, 'filtered_count': {'group_by', 'filter_value'},
    'join_sum': {'group_by', 'value'}, 'window_rank': {'group_by', 'value'},
    'pca': {'components'}, 'kmeans': {'clusters'},
    'random_forest': {'trees'}, 'logistic': {'iterations'}, 'ridge_cv': {'folds', 'alpha'},
    'forest_cv': {'folds', 'trees'}, 'histboost_cv': {'folds', 'iterations'},
    'svc_cv': {'folds', 'kernel'}, 'extra_trees_cv': {'folds', 'trees'}}

def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as fd:
        for chunk in iter(lambda: fd.read(8*1024**2), b''):
            h.update(chunk)
    return h.hexdigest()

def open_resource(path, expected_hash):
    """Reject links at every component, aliases and concurrent input changes."""
    path = Path(path).absolute()
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise ValueError('symlink resources are unsupported')
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags)
    info = os.fstat(fd)
    try:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('resource must be a regular file without aliases')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            h = hashlib.file_digest(stream, 'sha256').hexdigest()
        if h != expected_hash:
            raise ValueError('source hash mismatch')
        os.lseek(fd, 0, os.SEEK_SET)
        return fd, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except BaseException:
        os.close(fd)
        raise

def schemas_for(operations):
    from copy import deepcopy
    schemas = deepcopy(SCHEMAS)
    schemas['compute']['properties']['operation']['enum'] = list(operations)
    needed = set().union(*(ALLOWED_KEYS[op] for op in operations))
    schemas['compute']['properties'] = {k:v for k,v in schemas['compute']['properties'].items()
                                      if k in needed | {'dataset_id','operation'}}
    descriptions = {'group_by':'Column used for grouping, filtering or partitioning.',
                    'value':'Numeric measure column for summation or ranking.',
                    'filter_value':'Exact categorical value to count.',
                    'components':'Number of principal components.',
                    'clusters':'Number of KMeans clusters.',
                    'trees':'Number of ensemble trees.', 'folds':'Number of cross-validation folds.',
                    'iterations':'Number of boosting iterations.', 'kernel':'SVC kernel.',
                    'alpha':'Ridge regularization strength.'}
    for key,value in schemas['compute']['properties'].items():
        if key in descriptions:value['description']=descriptions[key]
    schemas['compute']['oneOf'] = [
        object_schema({k:deepcopy(v) for k,v in schemas['compute']['properties'].items()
                       if k in ALLOWED_KEYS[op] | {'dataset_id','operation'}})
        for op in operations]
    for op,branch in zip(operations,schemas['compute']['oneOf']):
        branch['properties']['operation']={'const':op}
    return [{'name': name, 'description': {
        'inspect_input': 'Read schema, shape and at most 3 input rows from the session dataset.',
        'compute': 'Run one bounded data operation. Supply only parameters relevant to that operation.',
        'view_artifact': 'Read a page of a session result artifact; IDs from other sessions are forbidden.'
    }[name], 'parameters': schema} for name, schema in schemas.items()]

@dataclass
class Resource:
    path: Path
    sha256: str
    kind: str
    metadata: dict

class SessionTools:
    def __init__(self, output, resources, operations, *, timeout=240, threads=2, memory_gib=12):
        self.output = Path(output).absolute()
        self.output.mkdir(parents=True, exist_ok=False)
        if self.output.is_symlink() or any(p.is_symlink() for p in self.output.parents):
            raise ValueError('symlink output root')
        self.resources = dict(resources)
        self.operations = tuple(operations)
        self.timeout, self.threads, self.memory_gib = timeout, threads, memory_gib
        self.artifacts = {}
        self.calls = []
        self.schema = {x['name']: x['parameters'] for x in schemas_for(operations)}

    def call(self, name, arguments):
        started = time.monotonic_ns()
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
                rows = result.get('rows', [])
                answer = {'artifact_id': identifier, 'result': result,
                          'page': rows[arguments['offset']:arguments['offset']+arguments['limit']],
                          'row_count': len(rows)}
            else:
                identifier = arguments['dataset_id']
                if identifier not in self.resources:
                    raise ValueError('unknown session dataset')
                resource = self.resources[identifier]
                check_start = time.monotonic_ns()
                fd, before = open_resource(resource.path, resource.sha256)
                record['input_check_ns'] = time.monotonic_ns()-check_start
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
                               'fd': fd, 'threads': self.threads, 'memory_gib': self.memory_gib}
                    env = {**os.environ, 'OMP_NUM_THREADS': str(self.threads),
                           'OPENBLAS_NUM_THREADS': str(self.threads), 'MKL_NUM_THREADS': str(self.threads),
                           'LEASEPATH_TOOL_TIMING_ENVELOPE':'1'}
                    child_started = time.monotonic_ns()
                    proc = subprocess.Popen([sys.executable, '-m', 'leasepath.restricted_data_worker'],
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
                    after = os.fstat(fd)
                    if before != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                        raise ValueError('concurrent source write')
                    check_start = time.monotonic_ns()
                    post_fd, post_info = open_resource(resource.path, resource.sha256)
                    os.close(post_fd)
                    if post_info != before:
                        raise ValueError('source changed during compute')
                    record['post_input_check_ns'] = time.monotonic_ns()-check_start
                    artifact = 'a'+uuid.uuid4().hex
                    path = self.output/(artifact+'.json')
                    with path.open('x') as stream:
                        json.dump(result, stream, allow_nan=False)
                        stream.write('\n')
                        stream.flush()
                        os.fsync(stream.fileno())
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
        record['result'] = answer
        # No fsync for each metadata row. Each artifact fsynced before returning its ID.
        write_start = time.monotonic_ns()
        with (self.output/'calls.jsonl').open('a') as stream:
            stream.write(json.dumps(record, allow_nan=False)+'\n')
            stream.flush()
        record['call_evidence_write_ns'] = time.monotonic_ns()-write_start
        self.calls.append(record)
        return answer
