import argparse
import copy
import json
import os
import shutil
from pathlib import Path
import time

from threadpoolctl import threadpool_limits
from leasepath.qwen_data_tasks import actual_band, equal, prepare, reference
from leasepath.restricted_data_tools import Resource, SessionTools, file_hash

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--reuse-inputs',type=Path)
    p.add_argument('--resize',nargs='*',default=[],help='CPU-only calibration: task_id:rows')
    p.add_argument('--reuse-checked-references',action='store_true')
    p.add_argument('--refresh-definitions',action='store_true')
    args=p.parse_args()
    if args.reuse_inputs:
        args.output.mkdir(parents=True,exist_ok=False)
        (args.output/'inputs').mkdir()
        tasks=json.loads((args.reuse_inputs/'tasks.private.json').read_text())
        if args.refresh_definitions:
            from leasepath.qwen_data_tasks import definitions
            changed=next(t for t in definitions() if t['id']=='logistic_holdout')
            for task in tasks:
                if task['id']=='forest_holdout':
                    task.update(changed)
                    task['dataset_id']='d'+task['id']
                    task['operations']=['logistic','pca']
        for task in tasks:
            source=Path(task['file'])
            destination=(args.output/'inputs'/source.name).resolve()
            if not destination.exists():
                shutil.copyfile(source,destination)
            assert file_hash(destination)==task['input_sha256']
            task['file']=str(destination)
        (args.output/'tasks.private.json').write_text(json.dumps(tasks,indent=2)+'\n')
        shutil.copyfile(args.reuse_inputs/'tasks.public.json',args.output/'tasks.public.json')
    else:
        tasks=prepare(args.output)
    resize=dict(item.split(':') for item in args.resize)
    if resize:
        import numpy as np
        from sklearn.datasets import make_blobs,make_regression,make_classification
        for index,task in enumerate(tasks):
            if task['id'] not in resize:continue
            n=int(resize[task['id']]);d=task['features'];seed=2401+index
            assert task['kind']=='numpy' and 1000<=n<=600000
            if task['generator']=='blobs':
                X,y=make_blobs(n_samples=n,n_features=d,centers=8,cluster_std=4,random_state=seed)
            elif task['generator']=='regression':
                X,y=make_regression(n_samples=n,n_features=d,n_informative=d//2,noise=3,random_state=seed)
            elif task['generator']=='classification':
                X,y=make_classification(n_samples=n,n_features=d,n_informative=d//2,n_redundant=d//4,
                                         class_sep=0.8,flip_y=0.02,random_state=seed)
            else:raise ValueError('unsupported calibration generator')
            np.savez(task['file'],X=X,y=y)
            task['rows']=n
            task['metadata']['shape']=list(X.shape)
            task['metadata']['sample']={'X':X[:3,:6].tolist(),'y':y[:3].tolist()}
            task['input_sha256']=file_hash(task['file'])
        (args.output/'tasks.private.json').write_text(json.dumps(tasks,indent=2)+'\n')
        (args.output/'tasks.public.json').write_text(json.dumps([
            {k:v for k,v in t.items() if k not in ('parameters','file','input_sha256')}
            for t in tasks],indent=2)+'\n')
    refs={}
    old_refs=json.loads((args.reuse_inputs/'references.private.json').read_text()) if args.reuse_checked_references else {}
    # All parameters/data fixed before these measurements; no model requests.
    for task in tasks:
        prior=old_refs.get(task['id'])
        if (prior and prior['input_sha256']==task['input_sha256'] and task['id'] not in resize):
            assert len(prior['reference_s'])==3 and prior['checker_rejects_wrong_value']
            tools=SessionTools(args.output/'preflight'/task['id'],
                {task['dataset_id']:Resource(Path(task['file']),task['input_sha256'],task['kind'],task['metadata'])},
                task['operations'])
            result=tools.call('compute',{'dataset_id':task['dataset_id'],'operation':task['operation'],**task['parameters']})
            assert 'error' not in result and equal(result['result'],prior['reference'])
            refs[task['id']]={**prior,'inherited_unchanged_reference':str(args.reuse_inputs.resolve()),
                'inherited_manifest_sha256':file_hash(args.reuse_inputs/'references.private.json'),
                'tool_s':(tools.calls[-1]['ended_ns']-tools.calls[-1]['started_ns'])/1e9}
            (args.output/'references.private.json').write_text(json.dumps(refs,indent=2)+'\n')
            print(task['id'],'unchanged input: three reference measurements reused; tool rechecked',flush=True)
            continue
        values,timings=[],[]
        for repeat in range(3):
            began=time.monotonic_ns()
            with threadpool_limits(limits=2):
                value=reference(task)
            duration=(time.monotonic_ns()-began)/1e9
            values.append(value); timings.append(duration)
            assert equal(values[0],value)
            print(task['id'],repeat+1,round(duration,3),actual_band(duration),flush=True)
            (args.output/'reference-progress.json').write_text(json.dumps({
                **refs,task['id']:{'reference':values[0],'reference_s':timings}},indent=2)+'\n')
        wrong=copy.deepcopy(value)
        if 'value' in wrong:wrong['value']+=max(1,abs(wrong['value'])*0.1)
        else:wrong['rows']=wrong['rows'][:-1]
        assert not equal(value,wrong)
        tools=SessionTools(args.output/'preflight'/task['id'],
                           {task['dataset_id']:Resource(Path(task['file']),task['input_sha256'],task['kind'],task['metadata'])},
                           task['operations'])
        result=tools.call('compute',{'dataset_id':task['dataset_id'],'operation':task['operation'],**task['parameters']})
        if 'error' in result or not equal(result['result'],value):
            raise ValueError(f"tool/reference differs: {task['id']} {result}")
        refs[task['id']]={'reference':value,'reference_s':timings,
                         'tool_s':(tools.calls[-1]['ended_ns']-tools.calls[-1]['started_ns'])/1e9,
                         'planned_band':task['planned_band'],'measured_band':actual_band(timings[1]),
                         'checker_rejects_wrong_value':True,'input_sha256':task['input_sha256']}
        (args.output/'references.private.json').write_text(json.dumps(refs,indent=2)+'\n')
    freeze={'tasks_sha256':file_hash(args.output/'tasks.private.json'),
            'references_sha256':file_hash(args.output/'references.private.json'),
            'status':'cpu_preflight_passed','reference_repetitions':3,
            'threads':2,'memory_gib':12,'max_tool_s':240,
            'band_note':'planned and observed bands reported separately; no balancing by repetition',
            'references':refs}
    (args.output/'preflight.json').write_text(json.dumps(freeze,indent=2)+'\n')

if __name__=='__main__':
    main()
