"""Real bounded tools for project-local development workspaces.

Paths are scoped and subprocesses receive a small environment and resource
limits. Arbitrary Python is a capability pilot, not a security sandbox.
No reference/checker modules or future user requests are given to subprocesses.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid

from .resident_tree_worker_process import UsagePopen


def schema(name, description, properties, required=()):
    return dict(name=name,description=description,parameters=dict(type='object',properties=properties,
                required=list(required),additionalProperties=False))


STRING={'type':'string'}
SCHEMAS={s['name']:s for s in [
    schema('list_files','List project files and sizes.',{}),
    schema('read_file','Read a UTF-8 project file. Large files return a bounded excerpt.',{'path':STRING},['path']),
    schema('search_files','Search text in project files; returns bounded matching lines.',{'query':STRING},['query']),
    schema('write_file','Replace one project text artifact using exact UTF-8 content.',{'path':STRING,'content':STRING},['path','content']),
    schema('run_python','Execute standard-library Python in the project directory; print a concise result. This runs real code.',{'code':STRING},['code']),
    schema('query_sql','Run a read-only SELECT against the actual billing.sqlite database.',{'sql':STRING},['sql']),
    schema('http_request','Perform a real GET to this project local HTTP service using a relative path.',{'path':STRING},['path']),
    schema('run_experiment','Execute analysis.py in the workspace and return saved metrics plus process output.',{}),
    schema('run_checks','Check JSON/Python artifact integrity and current-turn public requirements. Does not return reference solutions.',{}),
]}


def compact(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)


class ProjectTools:
    def __init__(self, workspace, evidence, project, cpus, api_url=None):
        self.root=Path(workspace).resolve();self.evidence=Path(evidence);self.evidence.mkdir(parents=True,exist_ok=False)
        self.project=project;self.cpus=cpus;self.api_url=api_url;self.calls=[];self.round=0;self.round_calls=0
        self.writable={'inventory.py','report.json','config.json','incident.json','integration.json','analysis.py','metrics.json','handoff.json'}

    def path(self,name):
        if not isinstance(name,str) or Path(name).is_absolute():raise ValueError('relative project path required')
        path=(self.root/name).resolve()
        if not path.is_relative_to(self.root) or path==self.root:raise ValueError('path outside project')
        return path

    def process(self,code,*,timeout=240):
        launcher = "import os,resource;os.sched_setaffinity(0,"+repr(self.cpus)+");resource.setrlimit(resource.RLIMIT_AS,(2147483648,2147483648));resource.setrlimit(resource.RLIMIT_CPU,(240,240));exec(compile("+repr(code)+",'<project-tool>','exec'))"
        env={k:os.environ[k] for k in ('PATH','PYTHONHOME','LD_LIBRARY_PATH') if k in os.environ}
        env.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',HOME=str(self.root),TMPDIR=str(self.root))
        child=UsagePopen([sys.executable,'-c',launcher],cwd=self.root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        try:
            stdout,stderr=child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL);stdout,stderr=child.communicate()
            return dict(returncode=child.returncode,stdout=stdout[:6000],stderr=stderr[:3000],timeout=True,child_pid=child.pid,lifetime=child.lifetime_usage)
        return dict(returncode=child.returncode,stdout=stdout[:6000],stderr=stderr[:3000],
                    output_truncated=len(stdout)>6000 or len(stderr)>3000,child_pid=child.pid,lifetime=child.lifetime_usage)

    def call(self,name,args):
        if self.round_calls>=11:raise RuntimeError('per-turn eleven-tool budget exhausted')
        self.round_calls+=1
        start=time.monotonic_ns();row=dict(call_id='t'+uuid.uuid4().hex,name=name,arguments=args,round=self.round,started_ns=start)
        try:
            if name not in self.project['tools']:raise ValueError('tool unavailable for project')
            contract=SCHEMAS[name]['parameters']
            if not isinstance(args,dict) or set(args)-set(contract['properties']) or not set(contract['required'])<=set(args):raise ValueError('tool arguments violate contract')
            if any(not isinstance(value,str) for value in args.values()):raise ValueError('string tool arguments required')
            if name=='list_files':
                result={'files':[dict(path=str(p.relative_to(self.root)),bytes=p.stat().st_size) for p in sorted(self.root.glob('*')) if p.is_file()]}
            elif name=='read_file':
                path=self.path(args['path'])
                with path.open() as stream:text=stream.read(6001)
                result={'content':text[:6000],'truncated':len(text)>6000}
            elif name=='search_files':
                matches=[]
                for path in sorted(self.root.glob('*')):
                    if not path.is_file() or path.suffix in ('.sqlite','.pyc'):continue
                    with path.open(errors='replace') as stream:
                        for number,line in enumerate(stream,1):
                            if args['query'].lower() in line.lower():matches.append(dict(path=path.name,line=number,text=line.rstrip()[:200]))
                            if len(matches)>=24:break
                    if len(matches)>=24:break
                result={'matches':matches,'limit':24}
            elif name=='write_file':
                if args['path'] not in self.writable:raise ValueError('only deliverable files are writable')
                if len(args['content'].encode())>24000:raise ValueError('artifact too large')
                path=self.path(args['path']);path.write_text(args['content'])
                result={'written':args['path'],'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            elif name=='run_python':result=self.process(args['code'])
            elif name=='query_sql':
                db=sqlite3.connect('file:'+str(self.path('billing.sqlite'))+'?mode=ro',uri=True)
                try:
                    db.set_progress_handler(lambda: 1 if time.monotonic_ns()-start>5_000_000_000 else 0,1000)
                    cursor=db.execute(args['sql']);rows=cursor.fetchmany(101)
                    result={'columns':[d[0] for d in cursor.description or []],'rows':rows[:100],'truncated':len(rows)>100}
                finally:db.close()
            elif name=='http_request':
                if not self.api_url or not args['path'].startswith('/') or '://' in args['path'] or args['path'].startswith('//'):raise ValueError('relative local route required')
                with urllib.request.urlopen(self.api_url+args['path'],timeout=10) as response:
                    result={'status':response.status,'body':json.loads(response.read(32000))}
            elif name=='run_experiment':
                result=self.process("import runpy;runpy.run_path('analysis.py',run_name='__main__')")
                if self.path('metrics.json').exists():result['metrics']=json.loads(self.path('metrics.json').read_text())
            elif name=='run_checks':
                spec=self.project['turns'][self.round]
                code="import ast,json,pathlib\np=pathlib.Path("+repr(spec['artifact'])+")\nassert p.is_file(),'requested artifact absent'\n"
                if spec['artifact'].endswith('.py'):
                    code+="ast.parse(p.read_text());print('Python syntax valid; verify behavior using run_python')"
                else:
                    code+="v=json.loads(p.read_text());assert isinstance(v,dict),'JSON object required';assert "+repr(spec['key'])+" in v,'current requested field absent';print('JSON and requested field valid; independent value checks run at delivery')"
                result=self.process(code)
            else:raise ValueError('unknown tool')
            row['status']='ok'
        except Exception as exc:
            result={'error':type(exc).__name__+': '+str(exc)};row['status']='error'
        encoded=compact(result)
        if len(encoded)>14000:result={'error':'tool result exceeds bounded output','result_bytes':len(encoded)}
        row.update(result=result,ended_ns=time.monotonic_ns(),result_sha256=hashlib.sha256(compact(result).encode()).hexdigest())
        self.calls.append(row)
        with (self.evidence/'calls.jsonl').open('a') as stream:stream.write(compact(row)+'\n')
        return result
