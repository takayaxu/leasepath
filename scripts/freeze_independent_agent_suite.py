"""Freeze offline candidate suite before any model run; does not accept residency/JCT."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import file_digest
from leasepath.independent_agent_tasks import fixtures,evaluate
from leasepath.independent_agent_reference import solve
from leasepath.independent_agent_protocol import canonical,digest,SYSTEM,schemas

NOTES={
 'job_shop':'Disjunctive machine scheduling; no old scheduling parent. Different intent from old linear assignment.',
 'propositional_sat':'Boolean clause satisfiability; no old SAT parent.',
 'package_resolution':'Discrete dependency/conflict graph version selection; no old package-resolution parent.',
 'max_flow':'Capacity-constrained network flow with conservation and min-cut certificate; adjacent to old assignment/transport but different constraints, input and objective formulation.',
 'exact_cover':'Exact subset partition, not old group aggregation or image components. Shared constraint-solver techniques with SAT kept in development.',
 'error_correction':'Erasure recovery from finite-field polynomial evaluations; not old real-valued interpolation or root finding. GF(17) toy instance is NOT RFC5510 GF(2^m) wire-format conformance.',
 'bin_packing':'Item-to-bin capacity partition and minimum bin count, not job precedence/machine schedules. Same broad operations-research field as development job shop; generalization claim must remain problem-family holdout, not solver-paradigm independence.',
 'automata_reachability':'Finite-state word reachability with BFS optimality certificate; no old state-machine parent. Toy DFA, not a Spin benchmark or full model checker.',
 'access_control':'Explicit-deny policy evaluation; no old security-policy parent. Toy rule set, not full IAM conformance.',
 'reaction_balance':'Integer stoichiometric conservation and primitive coefficients; not old fixed banded real system.',
 'voting_rule':'Ranked ballot choice under instant-runoff elimination; not old SQL top-k rank or rank-sum statistics.',
 'edit_distance':'Sequence edit script optimization; not old signal cross-correlation or timestamp asof. Distinct source publisher from development references.'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    provenance=ROOT/'.artifacts/independent-agent-v1-20261110/provenance'
    candidates=[]
    for f in ('sources.json','alternate-sources.json','mirror-sources.json','resolved-sources.json','semantic-sources.json'):
        candidates.extend(json.loads((provenance/f).read_text()))
    preferred={'job_shop':'job_shop.verified-source','automata_reachability':'automata_reachability.mirror','edit_distance':'edit_distance.semantics'}
    old_path=ROOT/'results/cache_policy/kv_distribution_next_stage_2026-11-09/seen-parent-exclusion.json';old=json.loads(old_path.read_text())
    tasks=fixtures();refs={};review=[]
    for t in tasks:
        name=t['kind'];source=next(r for r in candidates if r['status']=='retrieved' and r['family']==name and Path(r['local_path']).name==preferred.get(name,name+'.source'))
        assert file_digest(source['local_path'])==source['sha256']
        assert t['id'] not in {r['parent_id'] for r in old['parents']}
        assert t['source_family'] not in {r['source_group'] for r in old['parents']}
        t.update(input_sha256=digest(t['problem']),schema_sha256=digest(schemas(t)),source_document=source)
        reference=solve(t);observed=evaluate(t,reference['candidate']);assert observed['valid'] and observed['objective_minimize']==reference['optimum_minimize']
        refs[t['id']]=reference
        review.append({'parent':t['id'],'split':t['split'],'source_family':t['source_family'],'semantic_review':NOTES[name],
            'old_inputs_reused':False,'source_data_copied':False,'review_type':'author review, not independent human or second-agent review'})
    dev={t['source_family'] for t in tasks if t['split']=='development'};held={t['source_family'] for t in tasks if t['split']=='holdout'}
    assert len(dev)==len(held)==6 and not dev&held
    def write(name,value): (a.output/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write('tasks.json',tasks);write('references.private.json',refs);write('family-review.json',review)
    (a.output/'system-prompt.txt').write_text(SYSTEM)
    files=[ROOT/'src/leasepath'/x for x in ('independent_agent_tasks.py','independent_agent_reference.py','independent_agent_protocol.py','independent_agent_worker.py','pressure_data_agent.py')]
    files += [ROOT/'scripts/qwen_cache_independent_agent_batch.py',ROOT/'tests/test_independent_agent_v1.py',Path(__file__).resolve()]
    manifest={'version':1,'status':'offline_candidate_suite_frozen_pending_protocol_resource_acceptance','created_client_date':'2026-11-10 Asia/Shanghai',
        'parents':12,'development':6,'holdout':6,'model_generations':0,'model_validated_loops':0,'final_holdout_opened_to_model':False,
        'exclusion_count':old['count'],'exclusion_sha256':file_digest(old_path),'source_sha256':{str(p.relative_to(ROOT)):file_digest(p) for p in files},
        'artifact_sha256':{p.name:file_digest(p) for p in a.output.iterdir() if p.is_file()},
        'baseline_manifest_sha256':file_digest(ROOT/'configs/baselines/kv_equal_cost_distribution_v1.json'),
        'scope':'Locally authored small synthetic protocol fixtures informed by different problem-domain sources. Not imported public benchmark samples; shared authorship and tool/checker code remain common. No claim of representative heavy-tool latency, model-pretraining independence, or statistical independence of repeated runs.',
        'protocol_limits':{'model_generations_per_task':12,'tool_calls_per_task':11,'tool_timeout_s':240,'workflow_timeout_s':1800},
        'pilot':{'status':'not_launched','capacity_blocks':32,'concurrency':3,'development_batches':[[t['id'] for t in tasks[:3]],[t['id'] for t in tasks[3:6]]],
            'policy':'gbdt_distribution','limits_per_batch':{'probe':1,'pilot':36,'total':36},'aggregate_live_cap':72,'stop_on_first_failure':True,
            'prerequisites':['real tokenized accumulating history feasibility','frozen actual CPU/NUMA/THP/residency recipe','new full controller trace and native protocol acceptance','model-facing tool worker must not access reference files'],
            'not_a_policy_JCT_comparison':True},
        'holdout_rule':'No holdout model execution before development protocol/resource acceptance and final plan freeze. Keep every first-run failure; no replacement/relabeling.',
        'loop_rule':'At least two substantive model decisions separated by consumed real feedback and observable refinement/search/repair. One-shot success stays successful and carries no iterative evidence. Behavioral checks require independent native-output/call linkage before certification.'}
    write('manifest.json',manifest)
    print(json.dumps({k:manifest[k] for k in ('status','parents','development','holdout','model_generations')}))

if __name__=='__main__':main()
