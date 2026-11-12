"""Transactional JSON writes and useful public checks, with real error feedback.

Checks use public schemas, cross-field identities and small behavior probes.
The private final verifier remains separate. No reference answer is returned.
"""
import hashlib
import json
import time
import uuid

from .continuous_tools_v1 import compact,SCHEMAS
from .continuous_tools_v2 import ProjectTools as StableProjectTools


class ProjectTools(StableProjectTools):
    def _record(self,name,args,start,result,status):
        row=dict(call_id='t'+uuid.uuid4().hex,name=name,arguments=args,round=self.round,
                 started_ns=start,ended_ns=time.monotonic_ns(),result=result,status=status,
                 result_sha256=hashlib.sha256(compact(result).encode()).hexdigest())
        self.calls.append(row)
        with (self.evidence/'calls.jsonl').open('a') as stream:stream.write(compact(row)+'\n')
        return result

    def call(self,name,args):
        if name=='write_file' and isinstance(args,dict) and isinstance(args.get('path'),str) and args['path'].endswith('.json'):
            try:
                value=json.loads(args.get('content',''))
                if not isinstance(value,dict):raise ValueError('project JSON deliverables must be objects')
            except (ValueError,TypeError) as exc:
                if self.round_calls>=11:raise RuntimeError('per-turn eleven-tool budget exhausted')
                self.round_calls+=1
                return self._record(name,args,time.monotonic_ns(),dict(error='JSON validation failed before write: '+str(exc),file_unchanged=True,hint='Use json.dumps/json.dump to serialize actual computed values; JSON object keys must be quoted strings.'),'rejected')
        if name!='run_checks':return super().call(name,args)
        if self.round_calls>=11:raise RuntimeError('per-turn eleven-tool budget exhausted')
        self.round_calls+=1;start=time.monotonic_ns()
        try:
            if args!={}:raise ValueError('run_checks takes no arguments')
            spec=self.project['turns'][self.round]
            code="import ast,json,pathlib,math\np=pathlib.Path("+repr(spec['artifact'])+")\nassert p.is_file(),'requested target artifact absent'\n"
            if spec['artifact'].endswith('.py'):
                code+="ast.parse(p.read_text())\n"
                if self.project['id']=='continuous_inventory':
                    code+="import importlib.util\ns=importlib.util.spec_from_file_location('candidate','inventory.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
                    probes=[
                        "assert m.available({'item':9},'missing')==0",
                        "stock={'item':9};assert m.reserve(stock,'item',3)=={'item':6};assert stock=={'item':9}",
                        "stock={'item':9};assert m.release(stock,'other',2)=={'item':9,'other':2};assert stock=={'item':9}",
                        "assert m.low_stock({'b':2,'a':0},3)==['a','b']",
                        "assert m.stock_value({'item':9},{'item':2})==18",
                        "stock={'item':9};assert m.merge_deliveries(stock,[{'sku':'item','qty':3}])=={'item':12};assert stock=={'item':9}",
                        "stock={'item':9};assert m.fulfill_order(stock,{'item':3})=={'item':6};assert stock=={'item':9}",
                        "assert m.summarize({'item':9},{'item':2},10)=={'units':9,'value':18,'low':['item']}"
                    ]
                    code+='\n'.join(probes[:self.round+1])+'\n'
            else:
                code+="v=json.loads(p.read_text());assert isinstance(v,dict),'JSON object required'\nassert "+repr(spec['key'])+" in v,'current requested field absent in target artifact'\n"
                # Generic invariants, independent of private numeric references.
                code+="\nfor key,value in v.items():\n if key.endswith('_ids') or key=='required_checks':\n  assert isinstance(value,list) and value==sorted(value),key+' must be a sorted list'\n"
                if self.project['id']=='continuous_billing':
                    code+="\nif 'paid_by_region_cents' in v:\n assert set(v['paid_by_region_cents'])==set(v['by_region_cents']),'paid regional keys must cover every region'\n assert sum(v['paid_by_region_cents'].values())==v['paid_cents'],'paid regional amounts must sum to paid_cents'\nif 'by_region_cents' in v:assert sum(v['by_region_cents'].values())==v['gross_cents'],'regional amounts must sum to gross_cents'\n"
                elif self.project['id']=='continuous_api':
                    code+="\nif 'customer_names' in v:assert all(isinstance(k,str) and isinstance(value,str) for k,value in v['customer_names'].items()),'customer_names must map string IDs to names'\n"
                elif self.project['id']=='continuous_experiment':
                    code+="\nfor key in ('mean_y','variance_y','slope','intercept','mse'):\n if key in v:assert isinstance(v[key],(int,float)) and math.isfinite(v[key]),key+' must be finite'\nfor key in ('variance_y','mse'):\n if key in v:assert v[key]>=0,key+' must be nonnegative'\nif 'valid_count' in v:assert 0<=v['valid_count']<=v['count'],'valid_count outside total count'\n"
            code+="print('Public artifact schema, behavior and consistency checks passed; independent final verification remains separate.')\n"
            result=self.process(code);status='ok'
        except Exception as exc:result=dict(error=type(exc).__name__+': '+str(exc));status='error'
        return self._record(name,args,start,result,status)
