"""Recheck a sealed main run against fixed references and separate quality subset."""
import argparse
from collections import Counter
import json
import re
from pathlib import Path

from leasepath.qwen_data_audit import audit
from leasepath.qwen_data_tasks import equal,actual_band
from leasepath.restricted_data_tools import file_hash

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--run',type=Path,required=True)
    p.add_argument('--tasks',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    summary=json.loads((a.run/'summary.json').read_text())
    tasks=json.loads((a.tasks/'tasks.private.json').read_text())
    refs=json.loads((a.tasks/'references.private.json').read_text())
    preflight=json.loads((a.tasks/'preflight.json').read_text())
    assert preflight['tasks_sha256']==file_hash(a.tasks/'tasks.private.json')
    assert preflight['references_sha256']==file_hash(a.tasks/'references.private.json')
    audited=audit(a.run)
    seen=set();qualities=[];tools=[]
    for task in tasks:
        if task['id'] not in json.loads((a.run/'identity.json').read_text())['tasks']:continue
        assert task['source_group'] not in seen,'near-duplicate source group'
        seen.add(task['source_group'])
        q=json.loads((a.run/'sessions'/task['id']/'quality.json').read_text())
        if q['success']:
            final=q['final']
            assert re.fullmatch(r'a[0-9a-f]{32}',final['artifact_id'])
            artifact=a.run/'sessions'/task['id']/'artifacts'/(final['artifact_id']+'.json')
            # Artifact name is checked against actual registry-generated identifier in original logs.
            assert equal(json.loads(artifact.read_text()),refs[task['id']]['reference'])
            assert equal(final['answer'],refs[task['id']]['reference'])
        qualities.append(q)
        call_path=a.run/'sessions'/task['id']/'artifacts/calls.jsonl'
        for line in call_path.read_text().splitlines() if call_path.exists() else []:
            c=json.loads(line)
            duration=(c['ended_ns']-c['started_ns'])/1e9
            tools.append({'task':task['id'],'name':c['name'],'status':c['status'],
                          'wall_s':duration,'actual_band':actual_band(duration),
                          'planned_band':task['planned_band'],'source_group':task['source_group']})
    result={'scope':'independent sealed evidence and checker revalidation','status':'passed',
            'audit':audited,'unique_parents':len(seen),'complete_success':sum(q['success'] for q in qualities),
            'tool_calls':tools,'actual_tool_bands':dict(Counter(x['actual_band'] for x in tools)),
            'quality_success_trace_audited':True,
            'frozen_tasks_sha256':preflight['tasks_sha256'],'frozen_references_sha256':preflight['references_sha256']}
    a.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('tool_calls','audit')},indent=2))

if __name__=='__main__':main()
