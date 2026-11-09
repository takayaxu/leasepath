"""Link raw native output to actual calls and consumed feedback, without model runs."""
import argparse
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from leasepath.frozen_distribution_baseline import file_digest
from leasepath.independent_agent_protocol import certify_loop,check_final,digest
from leasepath.real_observer import validate_stream
from check_qwen_cache_stage2 import check_native


def read(p):return json.loads(Path(p).read_text())
def lines(p):return [json.loads(x) for x in Path(p).read_text().splitlines()]


def parse_native_call(text):
    matches=list(re.finditer(r'✿FUNCTION✿:\s*([^\n]+)\n✿ARGS✿:\s*',text))
    if len(matches)!=1:raise ValueError('native output must contain exactly one function')
    match=matches[0];arguments,end=json.JSONDecoder().raw_decode(text[match.end():])
    if text[match.end()+end:].strip():raise ValueError('unexpected trailing call output')
    return match[1].strip(),arguments


def audit(batch,suite,ledger,capacity,tokenizer):
    manifest=read(suite/'manifest.json')
    for name,sha in manifest['source_sha256'].items():
        if file_digest(ROOT/name)!=sha:raise ValueError('frozen suite source differs')
    for name,sha in manifest['artifact_sha256'].items():
        if file_digest(suite/name)!=sha:raise ValueError('frozen suite artifact differs')
    tasks={t['id']:t for t in read(suite/'tasks.json')};refs=read(suite/'references.private.json')
    for name in ('controller','backend'):validate_stream(batch/'raw'/f'{name}.jsonl')
    controller=lines(batch/'raw/controller.jsonl');backend=lines(batch/'raw/backend.jsonl')
    inputs={};outputs={};reservations={r['id']:r for r in lines(ledger)}
    seal=read(ledger.with_suffix('.sealed.json'))
    if not seal['sealed'] or seal['sha256']!=file_digest(ledger):raise ValueError('ledger is not sealed')
    for event in controller:
        if event['kind']=='request_input':
            r=event['data'];rid=r['request_id']
            if rid in inputs:raise ValueError('duplicate request')
            if tokenizer.encode(r['prompt'],add_special_tokens=False)!=r['token_ids']:raise ValueError('actual tokenizer differs')
            inputs[rid]={**r,'input_ns':event['ts_ns']}
    for event in backend:
        if event['kind']=='engine_request':
            r=event['data'];rid=r['request_id']
            if rid in outputs or rid not in inputs or rid[1:] not in reservations:raise ValueError('native reservation linkage')
            if r['owner']!=inputs[rid]['owner'] or r['tokens']!=inputs[rid]['token_ids']:raise ValueError('native input linkage')
            check_native(r,capacity,128,2048);outputs[rid]={**r,'observed_ns':event['ts_ns']}
    if set(inputs)!=set(outputs) or {x[1:] for x in outputs}!=set(reservations):raise ValueError('unmapped requests/reservations')
    rows=[]
    for quality in sorted(batch.glob('*-quality.json')):
        row=read(quality);owner=row['owner'];task=tasks[row['task']]
        own_outputs=sorted((r for r in outputs.values() if r['owner']==owner),key=lambda r:r['observed_ns'])
        stored=lines(batch/'sessions'/owner/'artifacts/calls.jsonl')
        if row['tools']!=stored:raise ValueError('saved call evidence differs')
        consumed_calls=set();previous_output=None
        for call in stored:
            preceding=[r for r in own_outputs if r['observed_ns']<=call['started_ns']]
            if not preceding:raise ValueError('call without preceding model output')
            native=preceding[-1]
            if native['request_id'] in consumed_calls:raise ValueError('multiple calls from one response')
            consumed_calls.add(native['request_id'])
            name,args=parse_native_call(native['text'])
            if (name,args)!=(call['name'],call['arguments']):raise ValueError('host action differs from actual native output')
            if digest(call['result'])!=call['result_sha256']:raise ValueError('feedback hash differs')
        successful,error=check_final(task,refs[task['id']],row['final'])
        if bool(row['success'])!=successful:raise ValueError('final correctness differs')
        if successful:
            if not own_outputs or json.loads(own_outputs[-1]['text'].strip())!=row['final']:raise ValueError('model did not choose final stop/answer')
            if len(own_outputs)>12 or len(stored)>11:raise ValueError('protocol budget exceeded')
        turns=[r for r in inputs.values() if r['owner']==owner]
        evidence=certify_loop(turns,stored,successful=successful)
        evidence['native_output_link_audit_required']=False
        evidence['loop_certified']=evidence['behavioral_loop_supported']
        evidence['classification']='certified_observable_refinement' if evidence['loop_certified'] else 'no_iterative_evidence'
        rows.append({'task':task['id'],'split':task['split'],'source_family':task['source_family'],'success':successful,'error':error,'generations':len(own_outputs),'tool_calls':len(stored),**evidence})
    return {'status':'passed','model_generations_in_audit':0,'native_requests_checked':len(outputs),'tasks':rows,
        'certified_loops':sum(r['loop_certified'] for r in rows),'one_shot_successes_retained':True,
        'natural_JCT_acceptance':False,'scope':'Native/protocol/feedback behavior only; resource, score and JCT require separate acceptance'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--batch',type=Path,required=True);p.add_argument('--suite',type=Path,required=True);p.add_argument('--ledger',type=Path,required=True);p.add_argument('--capacity',type=int,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf',local_files_only=True)
    result=audit(a.batch,a.suite,a.ledger,a.capacity,tokenizer);a.output.write_text(json.dumps(result,indent=2)+'\n')

if __name__=='__main__':main()
