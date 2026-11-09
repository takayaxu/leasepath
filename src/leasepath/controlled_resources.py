"""Local resource controls and read-only shared-host evidence for one experiment."""
import ctypes
import ctypes.util
import os
from pathlib import Path
import sys
import time


def child_state():
    fields = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    nodes = {}
    for line in Path('/proc/self/numa_maps').read_text().splitlines():
        for item in line.split():
            if item.startswith('N') and '=' in item and item[1:item.index('=')].isdigit():
                key, value = item.split('=')
                nodes[key] = nodes.get(key, 0)+int(value)
    lib = ctypes.CDLL(ctypes.util.find_library('numa'), use_errno=True)
    mode, mask = ctypes.c_int(), ctypes.c_ulong()
    if lib.get_mempolicy(ctypes.byref(mode), ctypes.byref(mask), ctypes.c_ulong(64), None, 0):
        raise OSError(ctypes.get_errno(), 'get_mempolicy')
    return {'cpus': sorted(os.sched_getaffinity(0)), 'thp_enabled': fields.get('THP_enabled', '').strip(),
            'memory_policy': mode.value, 'node_mask': mask.value, 'resident_pages_by_node': nodes,
            'runtime_modules':{name:getattr(sys.modules.get(name),'__file__',None)
                               for name in ('numpy','scipy','sklearn','threadpoolctl','jsonschema')}}


def apply_child_controls(control):
    cpus, node = control['cpus'], control['numa_node']
    if not cpus or node not in (0, 1) or control['disable_thp'] is not True:
        raise ValueError('invalid frozen child controls')
    # Set affinity before numerical imports/allocation; all later BLAS threads inherit it.
    os.sched_setaffinity(0, cpus)
    lib = ctypes.CDLL(ctypes.util.find_library('numa'), use_errno=True)
    mask = ctypes.c_ulong(1 << node)
    if lib.set_mempolicy(2, ctypes.byref(mask), ctypes.c_ulong(64)):
        raise OSError(ctypes.get_errno(), 'set_mempolicy MPOL_BIND')
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    if libc.prctl(41, 1, 0, 0, 0) or libc.prctl(42, 0, 0, 0, 0) != 1:
        raise OSError(ctypes.get_errno(), 'per-process THP disable')
    state = child_state()
    if (state['cpus'] != sorted(cpus) or state['thp_enabled'] != '0' or
            state['memory_policy'] != 2 or state['node_mask'] != mask.value):
        raise ValueError('kernel resource controls differ')
    return state


def host_sample():
    def ints(path):
        return {k.rstrip(':'): int(v.split()[0]) for k,v in
                (line.split(None, 1) for line in Path(path).read_text().splitlines())}
    psi = {}
    for name in ('cpu', 'io', 'memory'):
        psi[name] = {line.split()[0]: {k: float(v) for k,v in (part.split('=') for part in line.split()[1:])}
                     for line in Path('/proc/pressure', name).read_text().splitlines()}
    vm = ints('/proc/vmstat')
    processes = {}
    for directory in Path('/proc').glob('[0-9]*'):
        try:
            uid = directory.stat().st_uid
            raw = (directory/'stat').read_text()
            fields = raw[raw.rindex(')')+2:].split()
            processes[directory.name] = {'uid':uid, 'comm':raw[raw.index('(')+1:raw.rindex(')')],
                'start_ticks':int(fields[19]), 'cpu_ticks':int(fields[11])+int(fields[12]),
                'rss_pages':int(fields[21])}
        except (OSError, ValueError, IndexError):
            continue
    return {'wall_ns': time.monotonic_ns(), 'unix_ns': time.time_ns(), 'psi': psi,
            'meminfo_kib': ints('/proc/meminfo'),
            'vmstat': {k:v for k,v in vm.items() if k.startswith(('compact_', 'allocstall', 'thp_', 'pgscan', 'pgsteal', 'pswp', 'numa_'))},
            'cpu_stat': Path('/proc/stat').read_text(), 'diskstats': Path('/proc/diskstats').read_text(),
            'process_census':processes, 'clock_ticks':os.sysconf('SC_CLK_TCK'),
            'global_settings': {p: Path(p).read_text().strip() for p in (
                '/sys/kernel/mm/transparent_hugepage/enabled', '/sys/kernel/mm/transparent_hugepage/defrag', '/proc/sys/kernel/numa_balancing')}}


def psi_interval(before, after, resource, kind='some'):
    elapsed_us = (after['wall_ns']-before['wall_ns'])/1000
    return 100*(after['psi'][resource][kind]['total']-before['psi'][resource][kind]['total'])/elapsed_us
