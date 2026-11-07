"""Three timed references, wrong-answer rejection, real bounded tool comparison, then freeze."""
import argparse,copy,json,os,time
from pathlib import Path
from threadpoolctl import threadpool_limits
from leasepath.qwen_data_extension_tasks import prepare,reference
from leasepath.qwen_data_tasks import equal,actual_band
from leasepath.restricted_data_tools import SessionTools,Resource,file_hash,schemas_for

def mutate(value):
    wrong=copy.deepcopy(value)
    key=next(iter(wrong))
    if isinstance(wrong[key],list):wrong[key]=wrong[key][:-1]
    else:wrong[key]+=max(1,abs(wrong[key])*.1)
    return wrong

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--resume',action='store_true');p.add_argument('--task-ids',nargs='+');a=p.parse_args()
    if a.resume:
        assert not (a.output/'preflight.json').exists(),'sealed task version cannot be changed'
        tasks=json.loads((a.output/'tasks.private.json').read_text())
    else:tasks=prepare(a.output)
    path=a.output/'references.private.json';refs=json.loads(path.read_text()) if path.exists() else {}
    for task in tasks:
        name=task['id']
        if a.task_ids and name not in a.task_ids:continue
        if name in refs:
            assert refs[name]['input_sha256']==task['input_sha256'];print(name,'completed preflight retained',flush=True);continue
        assert file_hash(task['file'])==task['input_sha256']
        values=[];durations=[]
        for repeat in range(3):
            start=time.monotonic_ns()
            with threadpool_limits(limits=2):value=reference(task)
            duration=(time.monotonic_ns()-start)/1e9
            values.append(value);durations.append(duration)
            assert equal(values[0],value),(name,'nondeterministic reference',values)
            assert not equal(value,mutate(value)),(name,'checker negative accepted')
            print(name,'reference',repeat+1,round(duration,3),flush=True)
        preflight=a.output/'preflight-tools'/name
        if preflight.exists():preflight=a.output/'preflight-tools'/(name+'-'+str(time.time_ns()))
        tools=SessionTools(preflight,{task['dataset_id']:Resource(Path(task['file']),task['input_sha256'],task['kind'],task['metadata'])},task['operations'])
        result=tools.call('compute',{'dataset_id':task['dataset_id'],'operation':task['operation'],**task['parameters']})
        if 'error' in result or not equal(result['result'],value):
            (a.output/(name+'-failure-'+str(time.time_ns())+'.json')).write_text(json.dumps({'reference':value,'tool':result,'reference_s':durations},indent=2)+'\n')
            raise ValueError((name,'reference/tool mismatch',result,value))
        elapsed=(tools.calls[-1]['ended_ns']-tools.calls[-1]['started_ns'])/1e9
        refs[name]={'reference':value,'reference_s':durations,'input_sha256':task['input_sha256'],'tool_s':elapsed,'planned_band':task['planned_band'],'measured_band':actual_band(elapsed),'checker_rejects_wrong_value':True,'real_tool_reference_matches':True}
        path.write_text(json.dumps(refs,indent=2)+'\n')
        print(name,'tool',round(elapsed,3),actual_band(elapsed),'PASS',flush=True)
    if len(refs)!=38:print('partial preflight',len(refs),flush=True);return
    schemas={t['id']:schemas_for(t['operations']) for t in tasks}
    (a.output/'tool-schemas.json').write_text(json.dumps(schemas,indent=2)+'\n')
    prior=json.loads(Path('.artifacts/qwen-data-tasks-v5/tasks.private.json').read_text())
    assert len({t['source_group'] for t in tasks+prior})==50
    frozen={'status':'cpu_preflight_passed','reference_repetitions':3,'tasks_sha256':file_hash(a.output/'tasks.private.json'),'references_sha256':file_hash(path),'schemas_sha256':file_hash(a.output/'tool-schemas.json'),'threads':2,'memory_gib':12,'max_tool_s':240,'scope':'38 original additional development intents; no held-out score','baseline_parents':12,'references':refs}
    (a.output/'preflight.json').write_text(json.dumps(frozen,indent=2)+'\n')
    print('38-parent input/reference/schema freeze PASS',flush=True)
if __name__=='__main__':main()
