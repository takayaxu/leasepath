"""Controlled real inference with explicit v2 attempts and context scopes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
from pathlib import Path
import socket
import sys
import urllib.request
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from leasepath.real_observer import Observer

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
args.output.mkdir(parents=True,exist_ok=False)
manifest=json.loads((args.run/'service-manifest.json').read_text());port=manifest['port']
base=f'http://127.0.0.1:{port}'
log=Observer(args.output/'controller.jsonl',run_id=args.run.name,source='controlled_v2')
results=[]

def post(path,payload):
    req=urllib.request.Request(base+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=300) as response:
        body=response.read();return json.loads(body) if body else {}

def open_context(owner,version):
    log.emit('context_open',owner=owner,context_version=version,compatibility_id=manifest['compatibility_id'])

def attempt(owner,version,case,logical=None,retry=None,purpose='demand'):
    rid=uuid.uuid4().hex
    log.emit('request_ready',owner=owner,context_version=version,request_id=rid,
             logical_request_id=logical or rid,attempt_id=rid,retry_of=retry,purpose=purpose,
             wire_api='completion',case=case)
    return rid

def complete(owner,version,tokens,case,generated=16,logical=None,retry=None,purpose='demand'):
    rid=attempt(owner,version,case,logical,retry,purpose)
    response=post('/v1/completions',{'model':'leasepath-qwen25-coder32b','prompt':tokens,
        'request_id':rid,'max_tokens':generated,'ignore_eos':True,'temperature':0,'seed':23})
    (args.output/(rid+'.json')).write_text(json.dumps(response)+'\n')
    result={'request_id':rid,'owner':owner,'context_version':version,'case':case,'usage':response['usage']}
    log.emit('client_response',**result);results.append(result);print(json.dumps(result),flush=True)
    return rid

def close(owner,version):log.emit('context_retire',owner=owner,context_version=version,reason='explicit_controller_close')
def end(owner):log.emit('observation_end',owner=owner,client_complete=True)

try:
    post('/reset_prefix_cache',{})
    for owner in ['pressure-a','pressure-b']:open_context(owner,'long-v1')
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda n:complete(['pressure-a','pressure-b'][n],'long-v1',
                        [1800+n]+[1900]*12031,'decode-pressure-'+str(n),256),range(2)))
    for owner in ['pressure-a','pressure-b']:close(owner,'long-v1');end(owner)
    open_context('shared-a','main');open_context('shared-b','branch')
    common=[2100]*1024
    complete('shared-a','main',common+[2101],'parent')
    log.emit('context_fork',parent_owner='shared-a',parent_context_version='main',
             child_owner='shared-b',child_context_version='branch',reason='explicit_fork')
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda n:complete(['shared-a','shared-b'][n],['main','branch'][n],
                         common+[2101,2102+n],'shared-'+str(n),64),range(2)))
    close('shared-a','main');end('shared-a')
    complete('shared-b','branch',common+[2101,2103],'child-after-parent-close')
    # Explicit auxiliary inference is covered without consuming demand labels.
    complete('shared-b','branch',common+[2101,2103],'auxiliary',purpose='auxiliary_no_demand')
    logical=uuid.uuid4().hex;rid=attempt('shared-b','branch','cancel',logical)
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=300)
    payload={'model':'leasepath-qwen25-coder32b','prompt':common+[2101,2103], 'request_id':rid,
             'max_tokens':512,'ignore_eos':True,'temperature':0,'stream':True}
    conn.request('POST','/v1/completions',body=json.dumps(payload),headers={'Content-Type':'application/json'})
    response=conn.getresponse();first=response.readline()
    log.emit('cancel_requested',owner='shared-b',context_version='branch',request_id=rid,status=response.status,
             first_stream_line=first.decode(errors='replace'))
    if conn.sock:conn.sock.shutdown(socket.SHUT_RDWR)
    response.close();conn.close()
    complete('shared-b','branch',common+[2101,2103],'retry',logical=logical,retry=rid)
    log.emit('context_compact',owner='shared-b',old_context_version='branch',new_context_version='compact-v2')
    close('shared-b','branch');open_context('shared-b','compact-v2')
    complete('shared-b','compact-v2',[2200]*512,'compact')
    log.emit('context_restore',owner='shared-b',context_version='compact-v2',reason='restore_still_open_version')
    complete('shared-b','compact-v2',[2200]*512,'restored-open')
    end('shared-b')
finally:
    log.finish()
    (args.output/'summary.json').write_text(json.dumps({'scope':'controlled_actual_inference_not_task_occurrence',
                         'results':results,'requires_independent_audit':True},indent=2)+'\n')
