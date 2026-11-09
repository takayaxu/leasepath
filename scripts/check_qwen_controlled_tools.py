"""Read-only audit of all warmup and measured calls, including failed noise gates."""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.prediction_training import read_json,read_jsonl,sha256
from leasepath.qwen_data_tasks import equal
from probe_qwen_controlled_tools import tasks_and_refs,qualify
from probe_qwen_cache_native import write


def require(value,message):
    if not value: raise ValueError(message)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    plan=read_json(args.run/'frozen-plan.json')
    for name,digest in plan['source_sha256'].items():
        require(sha256(name)==digest,'frozen source changed: '+name)
    tasks,refs=tasks_and_refs(plan)
    tasks={t['id']:t for t in tasks}
    evidence=args.run/'raw-ram-evidence'
    for row in read_json(args.run/'input-copies.json'):
        require(sha256(evidence/'inputs'/(row['task']+'.npz'))==row['sha256']==tasks[row['task']]['input_sha256'],
                'saved input hash differs')
        require(row['mlock'] is True,'input lock not recorded')
    groups=[]
    counts={'measured':0,'warmup':0,'correct':0,'rejected':0}
    for phase,repeats in [('warmup',plan['preflight']['warmups']),('measured',plan['preflight']['repeats'])]:
        for repeat in range(repeats):
            directory=evidence/f'{phase}-{repeat}'
            group=read_json(directory/'summary.json')
            require({r['task'] for r in group['rows']}==set(tasks),'task group differs')
            for row in group['rows']:
                task=tasks[row['task']]
                call=row['call']
                counts[phase]+=1
                require(read_jsonl(directory/row['task']/'calls.jsonl')==[
                    {k:v for k,v in call.items() if k!='call_evidence_write_ns'}], 'saved call differs')
                require(call['arguments']==dict(dataset_id=task['dataset_id'],operation=task['operation'],**task['parameters']),
                        'actual compute parameters differ')
                require(call['ended_ns']>call['started_ns'],'time reversed')
                correct=call['status']=='ok' and equal(call['result']['result'],refs[row['task']]['reference'])
                require(correct==row['correct'],'reported quality differs')
                counts['correct']+=correct
                if not correct:
                    counts['rejected']+=1
                    continue
                identifier='a'+uuid.uuid5(uuid.UUID('6f61fb66-9b43-4345-b0f4-c736dadc9cc2'),row['task']+':0').hex
                require(identifier==call['result']['artifact_id'],'paired opaque ID differs')
                require(equal(read_json(directory/row['task']/(identifier+'.json')),refs[row['task']]['reference']),
                        'saved artifact differs')
                timing=call['worker_timing']
                require(call['child_wall_ns']>=call['worker_execute_ns']==timing['overall']['wall_ns']>0,'worker boundary differs')
                require(sum(v['wall_ns'] for v in timing['phases'].values())<=call['worker_execute_ns'],'phase accounting differs')
                require(all(v>=0 for phase_values in timing['phases'].values() for v in phase_values.values()),'negative timing counter')
            if phase=='measured': groups.append(group)
    summary=read_json(args.run/'summary.json')
    require(qualify(groups,plan)==summary,'noise qualification differs on replay')
    ridge=[(r['call']['ended_ns']-r['call']['started_ns'])/1e9 for g in groups for r in g['rows'] if r['task']=='ridge_validation']
    require(math.isclose(statistics.pstdev(ridge)/statistics.mean(ridge),summary['ridge_cv'],rel_tol=1e-12),'ridge CV differs')
    result={'status':'passed','experiment_status':summary['status'],**counts,
            'new_model_generations':0,'no_missing_or_excluded_measured_calls':True,
            'summary_sha256':sha256(args.run/'summary.json'),
            'scope':'source hashes, three input hashes, all real parameters, independent references, artifacts, saved calls, phase times and frozen noise gate checked'}
    write(args.output,result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
