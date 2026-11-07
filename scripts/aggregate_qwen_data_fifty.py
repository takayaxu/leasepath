"""Combine two preserved development batches without changing run IDs or selecting best retries."""
import argparse,hashlib,json,shutil,tempfile
from collections import Counter
from pathlib import Path
from leasepath.qwen_data_audit import audit
from leasepath.qwen_data_tasks import equal,actual_band
from leasepath.restricted_data_tools import file_hash


def rows(path):return [json.loads(l) for l in path.read_text().splitlines()]

def main():
    p=argparse.ArgumentParser();p.add_argument('--initial',type=Path,required=True);p.add_argument('--extension',type=Path,required=True);p.add_argument('--initial-tasks',type=Path,required=True);p.add_argument('--extension-tasks',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    catalog=[];provenance=[];events=[];episodes=[];features=[];targets=[];computes=[];seen=set();run_ids=set();tool_results=0;model_config=None
    for run,taskdir,expected in [(a.initial,a.initial_tasks,12),(a.extension,a.extension_tasks,38)]:
        summary=json.loads((run/'summary.json').read_text());identity=json.loads((run/'identity.json').read_text());freeze=json.loads((taskdir/'preflight.json').read_text());tasks=json.loads((taskdir/'tasks.private.json').read_text());refs=json.loads((taskdir/'references.private.json').read_text());checked=audit(run)
        assert freeze['tasks_sha256']==file_hash(taskdir/'tasks.private.json')
        assert freeze['references_sha256']==file_hash(taskdir/'references.private.json')
        assert len(tasks)==expected and set(identity['tasks'])=={t['id'] for t in tasks}
        assert checked['status']=='passed' and summary['complete_success']==expected
        assert not checked['tool_results_unconsumed']
        config={k:identity[k] for k in ['model_sha256','model_identity_sha256','library_sha256','tokenizer_sha256','template_sha256','dtype','kv_dtype','context','llama_commit','qwen_agent']}
        if model_config is None:model_config=config
        else:assert config==model_config,'model/backend configuration drift between batches'
        # Rebuild exports from sealed raw streams; compare existing files byte for byte.
        # This also rejects tampered exported labels/features even when raw seals are valid.
        with tempfile.TemporaryDirectory(prefix='leasepath-export-check-') as tmp:
            tmp=Path(tmp);shutil.copyfile(run/'identity.json',tmp/'identity.json');shutil.copytree(run/'raw',tmp/'raw')
            audit(tmp,export=True)
            for name in ['events','episodes','snapshots','coverage']:
                assert (tmp/(name+'.jsonl')).read_bytes()==(run/(name+'.jsonl')).read_bytes(),'export differs from sealed evidence: '+name
        assert identity['run_id'] not in run_ids;run_ids.add(identity['run_id'])
        tool_results+=checked['tool_results_consumed']
        for task in tasks:
            assert task['source_group'] not in seen,'duplicate analysis-intent family'
            seen.add(task['source_group']);session=run/'sessions'/task['id'];q=json.loads((session/'quality.json').read_text())
            assert q['success'] and q['source_group']==task['source_group']
            artifact=session/'artifacts'/(q['final']['artifact_id']+'.json')
            assert equal(json.loads(artifact.read_text()),refs[task['id']]['reference']) and equal(q['final']['answer'],refs[task['id']]['reference'])
            calls=rows(session/'artifacts/calls.jsonl')
            for call in calls:
                if call['name']=='compute':computes.append({'run_id':identity['run_id'],'task':task['id'],'source_group':task['source_group'],'operation':call['arguments'].get('operation'),'status':call['status'],'primary':call['arguments'].get('operation')==task['operation'],'tool_s':(call['ended_ns']-call['started_ns'])/1e9,'actual_band':actual_band((call['ended_ns']-call['started_ns'])/1e9)})
            catalog.append({'run_id':identity['run_id'],'task':task['id'],'source_group':task['source_group'],'intent':task['intent'],'operation':task['operation'],'planned_band':task['planned_band'],'source':task['source'],'input_sha256':task['input_sha256'],'reference_sha256':hashlib.sha256(json.dumps(refs[task['id']]['reference'],sort_keys=True).encode()).hexdigest(),'success':True,'generations':q['generations'],'wall_s':q['wall_s'],'source_run':str(run.resolve()),'split':'development'})
        ev=rows(run/'events.jsonl');ep=rows(run/'episodes.jsonl');fs=rows(run/'snapshots.jsonl');byid={r['idle_episode_id']:r for r in ep}
        for f in fs:
            e=byid[f['idle_episode_id']];remaining=e['end_ns']-f['snapshot_ns'];assert remaining>0
            targets.append({'run_id':f['run_id'],'idle_episode_id':f['idle_episode_id'],'snapshot_ns':f['snapshot_ns'],'outcome':e['outcome'],'remaining_ns':remaining})
        events.extend(ev);episodes.extend(ep);features.extend(fs)
        provenance.append({'run_id':identity['run_id'],'scope':summary['scope'],'run_path':str(run.resolve()),'identity_sha256':file_hash(run/'identity.json'),'summary_sha256':file_hash(run/'summary.json'),'tasks_sha256':freeze['tasks_sha256'],'references_sha256':freeze['references_sha256'],'protected_collection_sources':identity['protected_sha256'],'audit':checked,'budget':summary['budget']})
    assert len(catalog)==len(seen)==50
    assert len({(f['run_id'],f['idle_episode_id'],f['snapshot_ns']) for f in features})==len(features)
    primary=[c for c in computes if c['primary'] and c['status']=='ok'];long={c['source_group'] for c in primary if c['tool_s']>=60}
    card={'scope':'50 original development analysis intents across 12-parent pilot and 38-parent extension','parents':50,'complete_success':50,'source_batches':2,'main_generations':sum(t['generations'] for t in catalog),'consumed_tool_results':tool_results,'episodes':len(episodes),'snapshots':len(features),'labels':dict(Counter(e['outcome'] for e in episodes)),'actual_primary_compute_bands':dict(Counter(c['actual_band'] for c in primary)),'successful_parents_ge60s_primary_tool':len(long),'long_parent_groups':sorted(long),'quality_and_trace_independent_recheck':'passed','splits':{'development':50,'validation':0,'test':0},'independence_unit':'Distinct analysis intent; shared library/data-source families exist, no independent-source generalization claim.','protocol_and_controlled_probes_in_main':False,'selection':'Exactly two full successful frozen batches; no best-of-version task splicing.','training':'not started','online_policy':'not started','JCT_benefit':'not measured','same_host_backups_only':True}
    for name,items in [('catalog',catalog),('events',events),('episodes',episodes),('features',features),('targets',targets),('compute-calls',computes)]:
        with (a.output/(name+'.jsonl')).open('x') as f:
            for item in items:f.write(json.dumps(item,allow_nan=False)+'\n')
    (a.output/'dataset-card.json').write_text(json.dumps(card,indent=2)+'\n')
    (a.output/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    (a.output/'SHA256SUMS').write_text(''.join(file_hash(p)+'  '+p.name+'\n' for p in sorted(a.output.iterdir()) if p.is_file()))
    print(json.dumps(card,indent=2))
if __name__=='__main__':main()
