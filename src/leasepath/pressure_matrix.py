"""Versioned pressure runner helpers. All comparison arms use the frozen factory."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path

from .distribution_cache_backend import DistributionEngine
from .qwen_data_tasks import equal
from .restricted_data_tools import Resource
from .restricted_data_resident_tree_tools import ResidentTreeSessionTools

ARMS = ('retired_first_lru', 'gbdt_point', 'gbdt_distribution')


class PressureEngine(DistributionEngine):
    def __init__(self, *args, baseline, **kwargs):
        self.baseline, self.arm = baseline, ARMS[0]
        if set(kwargs) & {'policy', 'predictor', 'cost_table', 'block_size', 'audit_shadows'}:
            raise ValueError('frozen settings cannot be overridden')
        super().__init__(*args, block_size=128, **kwargs)

    def _reset_controller(self):
        self.cache = self.baseline.cache(self.capacity, runtime_compatibility=self.compatibility, arm=self.arm)
        self.slots, self.expected = {}, {}

    def select_arm(self, arm):
        if arm not in ARMS:
            raise ValueError('unknown comparison arm')
        self.baseline.verify()
        self.arm = arm
        self.clear()


def check_topology(plan, root=Path('/sys/devices/system/cpu')):
    groups = [plan['model_cpus']] + [v['cpus'] for v in plan['tool_controls'].values()]
    physical = []
    allowed = os.sched_getaffinity(0)
    for index, cpus in enumerate(groups):
        if not set(cpus) <= allowed:
            raise ValueError('CPU affinity is not available')
        cores = set()
        for cpu in cpus:
            p = root / f'cpu{cpu}' / 'topology'
            cores.add((int((p/'physical_package_id').read_text()), int((p/'core_id').read_text())))
            if index:
                siblings = {int(x) for x in (p/'thread_siblings_list').read_text().strip().split(',')}
                if not siblings <= set(cpus):
                    raise ValueError('tool SMT sibling missing')
                node = plan['tool_controls'][list(plan['tool_controls'])[index-1]]['numa_node']
                if not (root/f'cpu{cpu}'/f'node{node}').exists():
                    raise ValueError('NUMA assignment differs')
        if any(cores & prior for prior in physical):
            raise ValueError('experiment CPU groups overlap physical cores')
        physical.append(cores)
    return {'status':'passed', 'groups':groups, 'exclusive_host_claim':False}


def run_tool_group(tasks, refs, plan, output):
    """Exactly len(tasks) workers, not the legacy hardcoded three."""
    output.mkdir(parents=True, exist_ok=False)
    def run(task):
        session = ResidentTreeSessionTools(output/task['id'],
            {task['dataset_id']:Resource(Path(task['file']),task['input_sha256'],task['kind'],task['metadata'])},
            task['operations'], timeout=240, threads=1, memory_gib=12,
            controls=plan['tool_controls'][task['id']],session_key=task['id'])
        answer = session.call('compute',dict(dataset_id=task['dataset_id'],operation=task['operation'],**task['parameters']))
        return {'task':task['id'],'correct':'result' in answer and equal(answer['result'],refs[task['id']]['reference']), 'call':session.calls[0]}
    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        rows = list(pool.map(run,tasks))
    return {'concurrency':len(tasks),'rows':rows}
