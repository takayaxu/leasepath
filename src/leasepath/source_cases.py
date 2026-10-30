"""Unmodified source task chains and explicitly composed fanout/join workloads."""
from copy import deepcopy
import json

from .bfcl_runtime import render


def assemble(records,native,split):
    if len(records)!=16:raise ValueError('require sixteen preselected source tasks per split')
    serial=[];composed=[];groups={};orchestrators=[]
    for index in range(8):
        left,right=records[2*index:2*index+2]
        group=f'{split}:pair{index}'
        for record in (left,right):
            for source in record['requests']:
                request=deepcopy(source);request['source_group']=group
                request['expected_output']=request['collection_model']['output_tokens']
                request['cold_ms']=request['collection_model']['service_ms']
                request['retire_context']=request['id'].endswith(':final')
                serial.append(request)
            groups[record['id']]=group
        workflow=group+':composite';root=workflow+':root';join=workflow+':join'
        system='Coordinate two independent local benchmark tasks. Summarize their inputs and then their completed results.'
        root_text=render([('system',system),('user','Start both tasks independently:\n'+
                          '\n'.join(r['requests'][0]['prompt_text'].split('<|im_start|>user\n',1)[-1].split('<|im_end|>',1)[0]
                                    for r in (left,right)))])
        final_content=[]
        for record in (left,right):
            final_content.append({'task':record['id'],
                                  'questions_and_tool_results':[[{'call':t['call'],'output':t['output']} for t in r['reference_calls']]
                                                                for r in record['requests']],
                                  'final_model_summary':native.text(record['requests'][-1]['expected_output'])
                                  if 'expected_output' in record['requests'][-1] else
                                  native.text(record['requests'][-1]['collection_model']['output_tokens'])})
        join_text=render([('system',system),('user','Both branches completed. Combine their results:\n'+
                          json.dumps(final_content,ensure_ascii=False,sort_keys=True))])
        endpoints=[]
        for identity,text,dependencies in [(root,root_text,[]),(join,join_text,[[r['requests'][-1]['id'],0] for r in (left,right)])]:
            tokens=native.tokenize(text);native.clear();measured=native.run(tokens)
            endpoints.append({'id':identity,'workflow':workflow,'context':workflow+':coordinator','source_group':group,
                              'tokens':tokens,'prompt_text':text,'dependencies':dependencies,
                              'expected_output':measured['output_tokens'],'cold_ms':measured['service_ms'],
                              'retire_context':identity==join,'orchestrator_collection_model':measured})
            orchestrators.append({'id':identity,'prompt_tokens':len(tokens),'model':measured})
        composed.append(endpoints[0])
        for record in (left,right):
            for source in record['requests']:
                request=deepcopy(source);request['workflow']=workflow;request['source_group']=group
                request['expected_output']=request['collection_model']['output_tokens'];request['cold_ms']=request['collection_model']['service_ms']
                request['retire_context']=request['id'].endswith(':final')
                if not request['dependencies']:request['dependencies']=[[root,0]]
                composed.append(request)
        composed.append(endpoints[1]);groups[workflow]=group
    return [{'id':split+':source_serial','family':'source_serial','split':split,'block_size':128,
             'requests':serial,'workflow_order':[r['id'] for r in records],
             'workflow_groups':{r['id']:groups[r['id']] for r in records}},
            {'id':split+':source_pair_fanout_join','family':'source_pair_fanout_join','split':split,'block_size':128,
             'requests':composed,'workflow_order':[f'{split}:pair{i}:composite' for i in range(8)],
             'workflow_groups':{f'{split}:pair{i}:composite':f'{split}:pair{i}' for i in range(8)}}],orchestrators


def pressure_case(base,interval,multiplier,groups=None):
    case=deepcopy(base)
    if groups is not None:
        allowed=set(groups)
        case['requests']=[r for r in case['requests'] if r['source_group'] in allowed]
        case['workflow_order']=[w for w in case['workflow_order'] if case['workflow_groups'][w] in allowed]
    case['arrivals']={workflow:index*interval*multiplier for index,workflow in enumerate(case['workflow_order'])}
    case['id']+=f':arrival{multiplier}'
    return case
