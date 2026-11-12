"""Read-only real-input, tool, delivery, queue and physical-KV audit."""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from leasepath.continuous_projects_v2 import projects
from leasepath.continuous_verify_v1 import verify
from leasepath.continuous_tools_v1 import compact
from leasepath.frozen_distribution_baseline import FrozenEqualCostBaseline,file_digest
from leasepath.predictive_cache import PredictiveCache
from leasepath.prediction_state import validate_features
from leasepath.real_observer import validate_stream
from audit_independent_agent_loop_v4 import parse_native_call
from leasepath.continuous_protocol_v2 import native_delivery
from leasepath.continuous_predictor_v1 import ContinuousGBDT,ContinuousEmpirical
from leasepath.distribution_cache import distribution_scores
from leasepath.cache import block_keys
from copy import deepcopy
from leasepath.predictive_cache import Resident
from check_qwen_cache_stage2 import check_native
from check_qwen_cache_matrix import check_actions
from audit_pressure_live_state import audit_live_state
from evaluate_independent_predictions_v1 import extract_snapshots


def read(path):return json.loads(Path(path).read_text())
def lines(path):return [json.loads(x) for x in Path(path).read_text().splitlines()]
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


def audit(plan_path,output,tokenizer):
    plan=read(plan_path);work=Path(plan['work']);state=read(work/'run-manifest.json')
    assert state['status']=='completed_e2e_arm'
    for source,digest in plan['source_sha256'].items():assert file_digest(source)==digest,source
    for stream in ('controller','backend'):validate_stream(work/'raw'/f'{stream}.jsonl')
    controller=lines(work/'raw/controller.jsonl');backend=lines(work/'raw/backend.jsonl')
    inputs={e['data']['request_id']:{**e['data'],'input_ns':e['ts_ns']} for e in controller if e['kind']=='request_input'}
    requests={e['data']['request_id']:{**e['data'],'observed_ns':e['ts_ns']} for e in backend if e['kind']=='engine_request'}
    assert set(inputs)==set(requests)
    reservations={}
    for path in (work/'ledgers').glob('*.jsonl'):
        assert read(path.with_suffix('.sealed.json'))['sha256']==file_digest(path)
        if path.stem=='probe':continue
        for row in lines(path):
            assert row['id'] not in reservations
            reservations[row['id']]=row
    assert set(reservations)=={rid[1:] for rid in requests}
    by_owner=defaultdict(list)
    for rid,row in requests.items():
        source=inputs[rid]
        assert tokenizer.encode(source['prompt'],add_special_tokens=False)==source['token_ids']==row['tokens']
        assert row['prompt_sha256']==hashlib.sha256(source['prompt'].encode()).hexdigest()
        assert reservations[rid[1:]]['owner']==row['owner']
        check_native(row,plan['capacity_blocks'],128,2048);by_owner[row['owner']].append(row)
    feedback=0;tool_types=Counter();tool_errors=[]
    for project in projects():
        owner=project['id'];path=work/'tools'/owner/'calls.jsonl';stored=lines(path) if path.exists() else []
        ends=[e for e in controller if e['kind']=='tool_end' and e['data']['owner']==owner]
        assert len(stored)==len(ends)
        used=set()
        for call,event in zip(stored,ends):
            native=[r for r in by_owner[owner] if r['observed_ns']<=call['started_ns']][-1]
            assert native['request_id'] not in used;used.add(native['request_id'])
            assert parse_native_call(native['text'])==(call['name'],call['arguments'])
            assert event['data']['tool_name']==call['name'] and event['data']['result']==call['result']
            assert compact(event['data']['result'])==compact(call['result'])
            assert call['result_sha256']==hashlib.sha256(compact(call['result']).encode()).hexdigest()
            following=sorted((v for v in inputs.values() if v['owner']==owner and v['input_ns']>=call['ended_ns']),key=lambda r:r['input_ns'])
            assert following and compact(call['result']) in following[0]['prompt'],'unconsumed feedback'
            feedback+=1;tool_types[call['name']]+=1
            if call['status']!='ok' or call['result'].get('returncode',0)!=0:tool_errors.append(dict(owner=owner,round=call['round'],call_id=call['call_id'],name=call['name'],result=call['result']))
    # Replay controller-visible scheduling, including continuing user turns.
    ready=set();phase={};running={};last_tool={};snapshot=None;dispatch_snapshot={}
    for event in controller:
        kind,data=event['kind'],event['data'];owner=data.get('owner')
        if kind=='request_ready':assert owner not in ready;ready.add(owner);phase[owner]='queued'
        elif kind=='tool_start':phase[owner]='waiting_tool';running[owner]=data['actual_observed_monotonic_s'];last_tool[owner]=data['tool_name']
        elif kind=='tool_end':running.pop(owner,None);phase[owner]='queued'
        elif kind=='user_wait':phase[owner]='waiting_user'
        elif kind=='context_retire':ready.discard(owner);phase[owner]='retired'
        elif kind=='scheduler_snapshot':
            assert set(data['ready_owners'])==ready and len(data['ready_owners'])==len(ready)
            for name,f in data['observed_features'].items():
                validate_features(f);assert f['is_queued']==(name in ready) and f['queue_length']==len(ready)
                assert f['phase']==phase[name] and f['tool_name']==last_tool.get(name)
                if name in running:assert abs(f['tool_elapsed_s']-(data['observed_monotonic_s']-running[name]))<1e-8
                else:assert f['tool_elapsed_s'] is None
            snapshot=data
        elif kind=='request_dispatch':assert snapshot['ready_owners'][0]==owner;ready.remove(owner);phase[owner]='generating'
        elif kind=='request_input':dispatch_snapshot[data['request_id']]=snapshot
    baseline=FrozenEqualCostBaseline(ROOT,ROOT/plan['baseline_manifest']);compatibility=state['runtime_compatibility']
    replica=PredictiveCache(plan['capacity_blocks'],block_size=128,block_bytes=33554432)
    events=[e for e in controller if e['kind']=='context_retire']+[e for e in backend if e['kind']=='engine_request']
    for event in sorted(events,key=lambda r:r['ts_ns']):
        row,now=event['data'],event['ts_ns']/1e9
        if event['kind']=='context_retire':replica.retire(row['owner'],now);continue
        current=row['policy_decision']['distribution_audit'];previous=dispatch_snapshot[row['request_id']]
        for owner,f in current['observed_idle_features'].items():
            initial=previous['observed_features'][owner]
            for key,value in f.items():
                if key in ('elapsed_idle_s','tool_elapsed_s') and initial[key] is not None:
                    assert -1e-8<=value-initial[key]<=current['t0_s']-previous['observed_monotonic_s']+1e-8
                else:assert value==initial[key]
        observed=current['observed_idle_features']
        assert set(observed)==set(replica.idle)-{row['owner']}
        for owner,f in observed.items():replica.idle[owner]=(deepcopy(f),now)
        version=row['predictor_version']
        model_path=Path(plan['initial_model']) if version==0 else work/'training'/f'model-{version:03d}.json'
        cls=ContinuousGBDT if plan['predictor_kind']=='gbdt' else ContinuousEmpirical
        model=cls.load(model_path)
        for owner,score in current['owner_scores'].items():
            prediction=model.predict({**observed[owner],'compatibility_id':plan['training_domain']})
            assert prediction==score['distribution']
            expected=distribution_scores(prediction)
            assert expected['W']==score['W'] and expected['F_U_3s']==score['F_U_3s']
        keys=block_keys(row['tokens'][:((len(row['tokens'])-1)//128)*128],compatibility,128)
        owner=row['owner'];decision=row['policy_decision']
        assert replica.prefix_hits(keys)==decision['cached_blocks']
        replica.idle.pop(owner,None);replica.bindings[owner]=tuple(keys)
        policy=current['policy'];score_field='F_U_3s' if policy=='predicted_reuse' else 'W'
        fallback=False
        for sweep,victim in zip(current['sweeps'],decision['evicted']):
            parents={b.parent for b in replica.blocks.values() if b.parent is not None}
            leaves={k for k,b in replica.blocks.items() if k not in keys and not b.active and k not in parents}
            assert leaves==set(sweep['legal_leaves'])
            live=replica._live_owners();unbound={k for k in leaves if not live.get(k)}
            candidates=unbound or leaves;assert candidates=={c['key'] for c in sweep['candidates']}
            owners={o for k in candidates for o in live.get(k,())}
            if policy!='retired_first_lru':fallback=fallback or any(current['owner_scores'][o][score_field] is None for o in owners)
            assert sweep['distribution_fallback']==fallback and not sweep['cost_fallback']
            ranks={}
            for candidate in sweep['candidates']:
                k=candidate['key'];values=[]
                assert {v['owner'] for v in candidate['owners']}==set(live.get(k,()))
                for value in candidate['owners']:
                    expected_weight=current['owner_scores'][value['owner']][score_field] if policy!='retired_first_lru' and not fallback else 0.
                    assert value['marginal_cost']==1. and value['weight']==expected_weight
                    assert value['weighted_cost']==expected_weight;values.append(expected_weight)
                value=max(values,default=0.)/33554432
                block=replica.blocks[k];ranks[k]=(bool(live.get(k)),value,block.touched,-block.depth,k)
                assert list(ranks[k])==candidate['rank'] and value==candidate['value']
            assert min(ranks,key=ranks.__getitem__)==sweep['victim']==victim['key']
            assert victim['live_owners']==sorted(live.get(victim['key'],()))
            del replica.blocks[victim['key']]
        assert len(current['sweeps'])==len(decision['evicted'])
        for i,key in enumerate(keys):
            replica.clock+=1
            if key not in replica.blocks:replica.blocks[key]=Resident(keys[i-1] if i else None,i,replica.clock)
            replica.blocks[key].touched=replica.clock;replica.blocks[key].active.add(owner)
        replica.advance(now);replica.release(owner,dict(elapsed_idle_s=0.,compatibility_id=compatibility,prefix_tokens=len(keys)*128,tool_name=None,tool_elapsed_s=None,phase=None,retry_count=None,is_queued=None,queue_length=None),now)
        replica.check()
    rounds=[]
    for path in sorted((work/'rounds').glob('*.json')):
        row=read(path);project=next(p for p in projects() if p['id']==row['owner'])
        acceptance=verify(project,row['round'],row['artifact_path'])
        if row['success']:assert acceptance['success']
        for name,digest in row['artifact_sha256'].items():assert file_digest(Path(row['artifact_path'])/name)==digest
        if row['success']:
            native=sorted([r for r in by_owner[row['owner']] if r['release_ns']/1e9<=row['terminal_s']],key=lambda r:r['release_ns'])[-1]
            assert native_delivery(native['text'],inputs[native['request_id']]['prompt'])==row['final']
        assert row['generations']<=12 and row['tool_calls']<=11
        rounds.append(row)
    tasks={p['id']:{**p,'source_family':p['family']} for p in projects()}
    snapshots,exclusions=extract_snapshots(work,tasks)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    write(output/'prediction-snapshots.json',dict(rows=snapshots,exclusions=exclusions))
    result=dict(status='passed',holdout_model_tasks=0,native_requests=len(requests),feedback_links=feedback,
        rounds=len(rounds),successful_rounds=sum(r['success'] for r in rounds),tool_types=dict(tool_types),tool_errors=tool_errors,
        snapshots=len(snapshots),episodes=len({s['episode_id'] for s in snapshots}),excluded_snapshots=len(exclusions),
        phase_counts=dict(Counter(s['features']['phase'] for s in snapshots)),source_plan_sha256=file_digest(plan_path),
        event_counts=dict(Counter(s['event'] for s in snapshots)),natural_JCT_acceptance=False,
        scope='audited six development continuous projects; no holdout or policy timing comparison')
    write(output/'summary.json',result);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained('/home/liuxv/local-models/qwen25-coder-32b-leasepath/hf',local_files_only=True)
    print(json.dumps(audit(args.plan,args.output,tokenizer)))
