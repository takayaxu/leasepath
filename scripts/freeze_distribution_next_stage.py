"""Freeze the next-stage workload separation and resource/budget contract.

This is a preregistration and inventory check, not a model experiment launcher.
It never labels uncollected tasks, scripted calls, or old parents as holdout data.
"""
import argparse
import itertools
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline, file_digest
from leasepath.qwen_data_ledger import GenerationBudget

ARMS = ['retired_first_lru', 'gbdt_point', 'gbdt_distribution']


def validate(plan):
    if plan['baseline_id'] != 'gbdt-distribution-equalcost-H30-tau10-v1' or plan['policies'] != ARMS:
        raise ValueError('baseline/arms changed')
    matrix = plan['known_parent_matrix']
    if matrix['capacities_blocks'] != [24, 32, 64] or matrix['concurrencies'] != [3, 6, 10]:
        raise ValueError('frozen matrix changed')
    orders = matrix['orders']
    if set(map(tuple, orders)) != set(itertools.permutations(ARMS)) or len(orders) != 6:
        raise ValueError('all six policy orders required')
    expected = {(c,n) for c in matrix['capacities_blocks'] for n in matrix['concurrencies']}
    if {(cell['capacity_blocks'],cell['concurrency']) for cell in matrix['cells']} != expected or len(matrix['cells']) != 9:
        raise ValueError('matrix incomplete/duplicated')
    if len(matrix['nested_task_order']) != len(set(matrix['nested_task_order'])) or len(matrix['nested_task_order']) != 10:
        raise ValueError('known parent order invalid')
    limits = []
    for cell in matrix['cells']:
        n=cell['concurrency'];c=cell['capacity_blocks']
        if cell['task_ids'] != matrix['nested_task_order'][:n]:
            raise ValueError('matrix task subset changed')
        if cell['idle_tokens'] != c*128 or c*128+2048>16384:
            raise ValueError('native geometry exceeds context')
        batches=cell['batches']
        if len(batches)!=18:raise ValueError('incomplete counterbalance')
        for i,batch in enumerate(batches):
            if batch['policy'] != orders[i//3][i%3] or batch['cycle'] != i//3:
                raise ValueError('policy order changed')
            if batch['generation_limits'] != {'probe':1,'pilot':6*n,'total':6*n}:
                raise ValueError('batch generation cap changed')
            limits.append(batch['generation_limits'])
    totals=matrix['counts']
    if totals != {'cells':9,'batches':162,'task_executions':1026,'live_generations_if_three_per_task':3078,
                  'live_generation_hard_cap':6156,'native_probe_cap':39,'full_matrix_hard_cap_excluding_preflights':6195}:
        raise ValueError('matrix total mismatch')
    for limit in limits:
        if limit['total']>252 or limit['pilot']>228 or limit['probe']>72:
            raise ValueError('individual ledger exceeds existing limits')
    independent=plan['new_parent_agent_loops']
    if independent['suite_status'] != 'not_collected' or independent['primary_outcome'] != 'success_gated_natural_JCT':
        raise ValueError('uncollected suite must not imply completed evidence')
    if independent['old_parent_ids_are_ineligible'] is not True or independent['split_unit'] != 'source_family_and_parent':
        raise ValueError('invalid independent split contract')
    if plan['automatic_model_launch'] is not False:
        raise ValueError('preregistration does not bypass workload/resource preflights')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    baseline=FrozenEqualCostBaseline(ROOT,ROOT/'configs/baselines/kv_equal_cost_distribution_v1.json')
    inventory=[];source_hashes={}
    for name in ('qwen-data-tasks-v5','qwen-data-tasks-extension38-v2'):
        path=ROOT/'.artifacts'/name/'tasks.private.json';source_hashes[str(path.relative_to(ROOT))]=file_digest(path)
        for task in json.loads(path.read_text()):
            inventory.append({'parent_id':task['id'],'source_group':task['source_group'],'operation':task['operation'],
                'input_sha256':task['input_sha256'],'status':'previously_seen_development',
                'eligible_as_new_independent_parent':False})
    if len(inventory)!=50 or len({r['parent_id'] for r in inventory})!=50:raise ValueError('old-parent inventory differs')
    known=['logistic_holdout','ridge_validation','temporal_asof','priority_top','missing_median',
           'multi_group_anova','rank_sum_test','dominant_frequency','feature_information','image_regions']
    if not set(known)<={r['parent_id'] for r in inventory}:raise ValueError('missing known development task')
    orders=list(map(list,itertools.permutations(ARMS)))
    cells=[]
    # Fixed progression changes one dimension at a time around the old 24/3 cell.
    for capacity,n in [(24,3),(32,3),(64,3),(24,6),(32,6),(64,6),(24,10),(32,10),(64,10)]:
        cells.append({'capacity_blocks':capacity,'concurrency':n,'idle_tokens':capacity*128,
            'status':'pending_native_input_and_resource_preflight','task_ids':known[:n],
            'batches':[{'cycle':cycle,'position':position,'policy':policy,
                'ledger':f'capacity-{capacity}/concurrency-{n}/cycle-{cycle}/{policy}/ledger.jsonl',
                'generation_limits':{'probe':1,'pilot':6*n,'total':6*n}}
                for cycle,order in enumerate(orders) for position,policy in enumerate(order)]})
    plan={'version':1,'status':'preregistered_pending_workload_preflight','baseline_id':baseline.document['baseline_id'],
        'baseline_manifest':'configs/baselines/kv_equal_cost_distribution_v1.json',
        'baseline_manifest_sha256':baseline.manifest_sha256,'baseline_commit':baseline.document['baseline_commit'],
        'authorization':'用户确认等成本分布为后续基线，固定参数扩大容量/并发，新增独立母题和真实agent循环',
        'policies':ARMS,'automatic_model_launch':False,'known_prior_model_generations':1727,
        'known_parent_matrix':{'purpose':'pressure generalization on known development workload; not new-parent evidence',
            'capacities_blocks':[24,32,64],'concurrencies':[3,6,10],'nested_task_order':known,'orders':orders,'cells':cells,
            'counts':{'cells':9,'batches':162,'task_executions':1026,'live_generations_if_three_per_task':3078,
                'live_generation_hard_cap':6156,'native_probe_cap':39,'full_matrix_hard_cap_excluding_preflights':6195},
            'native_probe_ledgers':{str(c):{'probe':13,'pilot':1,'total':13} for c in [24,32,64]},
            'prefix_guard':'Before reserving any generation validate each actual prompt cached-block count <= cell capacity; no silent truncation, compression, prompt padding or no-cache bypass.',
            'timing_rule':'Fresh complete crossover per cell. Never pool prior legacy matrix or 24/3 means into new averages.',
            'host_recipe':'Fresh real tool CPU/SMT/NUMA plan for 10 concurrent tasks; account aggregate memory and all runnable tool threads. Reusing three CPU groups without documenting contention is forbidden.',
            'failure_rule':'Stop at first native/protocol/quality/resource/source violation, seal all affected ledgers; no replacement batches or posthoc threshold relaxation.'},
        'new_parent_agent_loops':{'suite_status':'not_collected','target_new_parent_groups':12,
            'target_development_groups':6,'target_final_test_groups':6,'split_unit':'source_family_and_parent',
            'old_parent_ids_are_ineligible':True,'old_parent_inventory':'seen-parent-exclusion.json',
            'definition':'New analysis/decision intent and provenance review; a new seed, scale, wording, operation name, or task ID alone is not a new parent.',
            'independence_claim':'Holdout from predictor training and workload/prompt tuning, after family-level exclusion review; no claim of independence from model pretraining or from shared libraries.',
            'freeze_before_model':'Freeze input hashes, schema, parent/family split, checker/reference hashes, prompt/protocol, CPU controls and aggregate/per-ledger generation caps before first model test.',
            'reference_requirement':'Independent solver/reference plus meaningful incorrect-answer and violated-constraint rejection tests; real bounded tool executions, no compute tool returning entire task plan.',
            'agent_choice':'Model selects each next tool and its arguments from actual returned data. Host only validates capabilities, executes tool, preserves full history and checks finish.',
            'loop_certificate':['Model-generated decision before each call, linked to immutable backend request/output.',
                'At least two substantive decisions separated by real feedback; at least one changed candidate/parameter/branch based on observed feedback.',
                'Tool feedback consumed by the next actual tokenized prompt; action-output-call identifiers and hashes agree.',
                'Observed repair/search/refinement trace and model stop decision with independently validated final answer.'],
            'not_loop_evidence':['inspect→one compute→final','repeating identical calls','host-scripted tool order or arguments',
                'sleep/busy-wait/artificial retries','oracle next action or future request length in predictor'],
            'no_forced_errors':'Do not deliberately break correct output to force a loop. One-shot success stays successful, reports no iterative-loop evidence, is never replaced by a cherry-picked failure.',
            'termination':{'model_generations_per_task_max':12,'tool_calls_per_task_max':11,'tool_timeout_s':240,'workflow_timeout_s':1800},
            'initial_agent_pilot':{'concurrency':3,'capacity_blocks':32,'max_generations_per_policy_batch':36,
                'probe_required':'Exact tokenizer and accumulated history must fit 32 cached blocks before launch; if not, preregister another geometry before timing, not after seeing JCT.',
                'post_pilot_matrix':'Freeze exact capacities/concurrency/task lists from reference/protocol/resource feasibility only; do not select by policy JCT.'},
            'admission_rule':'Only development groups used to debug protocol. After first final-test run, retain every failure and do not retrain, retune, replace task or rename it unseen.',
            'primary_outcome':'success_gated_natural_JCT','failure_reporting':'Report all-task success/timeout/invalid answers separately; never compare successful-task-only JCT without success denominators.'},
        'common_controls':{'FCFS':True,'model':'Qwen2.5-Coder-32B-Instruct F16','gpus':[5,6,7],
            'context_tokens':16384,'block_tokens':128,'transient_headroom_tokens':2048,'tool_threads':1,
            'residency':'Verified read-only model/runtime/input/system maps; trace full changed controller and child dependencies outside JCT; own tmpfs only.',
            'parent_read_bytes_max':0,'parent_major_faults_max':0,'parent_block_input_ops_max':0,
            'worker_lifetime_read_bytes_max':0,'worker_lifetime_major_faults_max':0,'worker_lifetime_block_input_ops_max':0,
            'host_scope':'Shared host; affinity is not exclusivity. Observe CPU/IO/memory PSI and GPU tenants; never modify other jobs/global page cache/THP.',
            'uniform_audit':'Same shadow and instrumentation overhead for all policies, included in JCT.'},
        'reporting':['paired cycle and parent/family JCT, makespan, descriptive tail and failure denominators',
            'exact input/output token equality, actual reusable/prefill tokens and native prefill/decode times',
            'prediction/control/shadow cost, pressure and legal-victim differences, shared-prefix subset',
            'GBDT global_fallback_unseen_state vs unsupported-window LRU fallback separately',
            'elapsed>1s resolution limit, loop depth, actual tool branches/feedback, queue/load covariates',
            'all failures, sealed generation ledgers, source hashes, independently audited archive and GPU release'],
        'next_executable_step':'Restore verified runtime; freeze a versioned capacity/concurrency runner and CPU/memory plan; full-controller trace then first 24/3 crossover; new-parent suite construction is a separate preflight gate.'}
    validate(plan)
    (args.output/'plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
    (args.output/'seen-parent-exclusion.json').write_text(json.dumps({'status':'reviewed_as_already_seen',
        'count':50,'source_sha256':source_hashes,'parents':inventory},ensure_ascii=False,indent=2)+'\n')
    resource={'sampled_wall_ns':time.time_ns(),'gpu':subprocess.check_output(['nvidia-smi',
        '--query-gpu=index,uuid,memory.used,memory.total','--format=csv,noheader,nounits'],text=True),
        'meminfo':Path('/proc/meminfo').read_text(),
        'cpu_psi':Path('/proc/pressure/cpu').read_text(),'io_psi':Path('/proc/pressure/io').read_text(),
        'memory_psi':Path('/proc/pressure/memory').read_text(),
        'cpu_topology':subprocess.check_output(['lscpu','-p=CPU,CORE,SOCKET,NODE'],text=True),
        'scope':'read-only inventory, not a stability or exclusive-resource admission'}
    (args.output/'resources.json').write_text(json.dumps(resource,indent=2)+'\n')
    summary={'status':'passed','scope':'baseline integrity and prospective plan validation only',
        'baseline':baseline.evidence(),'old_parents_excluded':50,'matrix_cells':9,
        'matrix_budget_limits_validated':True,'new_parent_suite_collected':False,'real_agent_loop_verified':False,
        'new_model_generations':0,'new_JCT_measurements':0,
        'source_sha256':{str(Path(__file__).relative_to(ROOT)):file_digest(__file__),
                         'src/leasepath/frozen_distribution_baseline.py':file_digest(ROOT/'src/leasepath/frozen_distribution_baseline.py')},
        'plan_sha256':file_digest(args.output/'plan.json')}
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('baseline','source_sha256')},indent=2))


if __name__=='__main__':main()
