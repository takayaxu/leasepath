"""Freeze a completed run, summarize qualification, and archive raw evidence separately."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(8*1024**2),b''):h.update(data)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--tasks',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    summary=json.loads((a.run/'summary.json').read_text())
    manifest=json.loads((a.tasks/'tasks.private.json').read_text())
    task_map={t['id']:t for t in manifest}
    observations=[]
    independent=set()
    long_success=set()
    for row in summary['quality']:
        task=task_map[row['task']]
        primary=[]
        call_file=a.run/'sessions'/row['task']/'artifacts/calls.jsonl'
        for line in call_file.read_text().splitlines() if call_file.exists() else []:
            call=json.loads(line)
            if call['name']=='compute':
                seconds=(call['ended_ns']-call['started_ns'])/1e9
                child=call.get('child_wall_ns')
                observations.append({'task':row['task'],'source_group':task['source_group'],
                    'status':call['status'],'operation':call['arguments'].get('operation'),
                    'planned_band':task['planned_band'],'tool_s':seconds,
                    'child_s':child/1e9 if child is not None else None})
                if call['status']=='ok' and call['arguments']['operation']==task['operation']:
                    primary.append(seconds)
        if row['success']:
            independent.add(task['source_group'])
            if any(x>=60 for x in primary):long_success.add(task['source_group'])
    card={'scope':summary['scope'],'batch_task_count':summary['tasks'],
          'unique_development_parents':len({task_map[q['task']]['source_group'] for q in summary['quality']}),
          'complete_success':summary['complete_success'],'first_attempt_complete_success':summary['complete_success'],
          'retry_success':'No external task retries in this frozen batch; within-task parameter repairs retained in tool calls.',
          'successful_independent_parents':len(independent),'successful_parents_with_ge60s_tool':len(long_success),
          'audit_status':summary['audit_status'],'pilot_qualified':summary.get('pilot_qualified',False),
          'main_labels':summary.get('audit',{}).get('labels'),
          'success_per_planned_band':summary['success_per_planned_band'],
          'all_compute_calls':observations,'budget':summary['budget'],
          'fee':'local existing hardware; no external inference purchase; no currency cost estimate',
          'elapsed_model_process_s':summary['runtime_s'],
          'training':'not started','online_policy':'not started','JCT_benefit':'not measured',
          'source_tasks_sha256':sha(a.tasks/'tasks.private.json')}
    (a.output/'dataset-card.json').write_text(json.dumps(card,indent=2)+'\n')
    checks={}
    for path in sorted(a.run.rglob('*')):
        if path.is_file():checks[str(path.relative_to(a.run))]=sha(path)
    (a.output/'raw-checksums.json').write_text(json.dumps(checks,indent=2)+'\n')
    with tarfile.open(a.output/'raw-evidence.tar.gz','w:gz') as archive:
        archive.add(a.run,arcname=a.run.name)
        archive.add(a.tasks/'tasks.private.json',arcname='frozen-tasks/tasks.private.json')
        archive.add(a.tasks/'references.private.json',arcname='frozen-tasks/references.private.json')
    (a.output/'raw-evidence.tar.gz.sha256').write_text(sha(a.output/'raw-evidence.tar.gz')+'  raw-evidence.tar.gz\n')
    print(json.dumps({k:v for k,v in card.items() if k!='all_compute_calls'},indent=2))

if __name__=='__main__':main()
