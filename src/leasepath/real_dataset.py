"""Export only explicitly mapped stage-0 owners after complete backend checks.

Uses the established trace contract with separate supervision. No future prefix
selection, substring request-ID joins, simulated lookup, or implicit retire.
"""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

from .real_audit import load_records, audit_requests, audit_blocks
from .real_observer import validate_stream, validate_observation
from .trace import derive_idle_labels, validate_events


def export(run, pilot, output, *, controlled=False):
    run, pilot, output = Path(run), Path(pilot), Path(output)
    manifest = json.loads((run / 'service-manifest.json').read_text())
    raw = load_records(run / 'raw')
    if any(r['kind'] == 'observation_gap' for r in raw):
        raise ValueError('logging gap invalidates complete label evidence')
    # Require a closed, hashed stream for every backend producer, including
    # rank initialization and bootstrap processes; incomplete shutdown fails.
    transport = {p.name: validate_stream(p) for p in (run / 'raw').glob('*.jsonl')}
    expected = json.loads((run / 'expected-streams.json').read_text())
    validate_observation((run / 'raw').glob('*.jsonl'), expected_stream_ids=expected['stream_ids'])
    backend_boot = next(iter(transport.values()))['boot_id']
    backend_audit = audit_requests(raw)
    block_audit = audit_blocks(raw)
    checkpoints = [r for r in raw if r['kind'] == 'engine_checkpoint']
    rank_checkpoints = [r['data']['rank'] for r in raw if r['kind'] == 'rank_checkpoint']
    if (len(checkpoints) != 1 or checkpoints[0]['data']['outstanding_requests'] != 0 or
            sorted(rank_checkpoints) != list(range(len(manifest['devices']))) or
            any(not r['complete'] for r in backend_audit['requests'])):
        raise ValueError('observation has no complete drained engine/rank checkpoint')
    if backend_audit['rank_ids'] != list(range(len(manifest['devices']))):
        raise ValueError('missing TP rank evidence')
    runners, submits, clients = [], {}, {}
    paths = [pilot / 'controller.jsonl'] if controlled else list(pilot.glob('*/runner.jsonl'))
    for path in paths:
        runner_stream = validate_stream(path)
        if runner_stream['boot_id'] != backend_boot:
            raise ValueError('runner and backend boot/clock domains differ')
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if any(r['run_id'] != run.name for r in rows):
            raise ValueError('runner and backend run differ')
        runners.extend(rows)
        for r in rows:
            if r['kind'] == 'workflow_submit':
                submits[r['data']['owner']] = r
            elif r['kind'] == 'request_ready':
                rid = r['data']['request_id']
                if rid in clients:
                    raise ValueError('duplicate client ID')
                clients[rid] = r
    clients = {rid: r for rid, r in clients.items() if r['data']['owner'] in submits}
    if not submits or not clients:
        raise ValueError('no explicitly submitted workflows/requests')
    mapped, by_engine = {}, defaultdict(list)
    for row in raw:
        rid = row['data'].get('request_id')
        if rid and row['kind'] in {'request_enqueue', 'cache_lookup', 'prefill_scheduled',
                                  'request_release', 'preemption_release'}:
            by_engine[rid].append(row)
    for client_id, ready in clients.items():
        prefix = ('cmpl-' + client_id + '-0-') if controlled else ('chatcmpl-' + client_id + '-')
        candidates = [rid for rid in by_engine if rid.startswith(prefix)]
        if len(candidates) != 1:
            raise ValueError('request must have exactly one audited mini-swe engine mapping')
        mapped[candidates[0]] = ready
    audits = {r['request_id']: r for r in backend_audit['requests']}
    for rid in mapped:
        if not audits[rid]['complete'] or not audits[rid]['normal_completion']:
            raise ValueError('selected Agent request has no normal complete backend evidence')
        if not audits[rid]['idle_prefix_eligible']:
            raise ValueError('selected Agent request has no eligible idle prefix')
        if audits[rid]['lookup_count'] != 1 or audits[rid]['preemptions']:
            raise ValueError('v0.1 projection requires one lookup and no preemption; raw evidence retained')
    events, pending, event_number = [], {}, 0
    boot = backend_boot

    def add(kind, row, owner, data, *, rid=None, episode=None, timestamp=None):
        nonlocal event_number
        event_number += 1
        event = {'schema_version': '0.1', 'run_id': owner, 'event_id': f'real-{event_number:08d}',
                 'event_type': kind, 'ts_ns': row['ts_ns'] if timestamp is None else timestamp,
                 'clock_domain': 'monotonic:' + boot, 'evidence_kind': 'measured',
                 'workflow_id': owner, 'data': data}
        if rid:
            event['request_id'] = rid
        if episode:
            event['idle_episode_id'] = episode
        events.append(event)
        return event

    combined = sorted(runners + [r for r in raw if r['data'].get('request_id') in mapped],
                      key=lambda r: (r['ts_ns'], r['stream_id'], r['seq']))
    tool_episodes = {}
    ended_owners = set()
    for row in combined:
        kind, data = row['kind'], row['data']
        backend_id = data.get('request_id')
        ready = mapped.get(backend_id)
        if ready:
            owner = ready['data']['owner']
            rid = ready['data']['request_id']
        else:
            owner, rid = data.get('owner'), data.get('request_id')
        if owner not in submits or owner in ended_owners:
            continue
        if kind == 'workflow_submit':
            if data['compatibility_id'] != manifest['compatibility_id']:
                raise ValueError('runner compatibility differs')
            add(kind, row, owner, {'task_id': data['task_id'], 'source_group': data['source_group'],
                                 'agent': data['agent']})
        elif kind == 'request_ready':
            add(kind, row, owner, {}, rid=rid)
        elif kind == 'request_enqueue':
            if data['compatibility_id'] != manifest['compatibility_id'] or data['cache_salt'] is not None:
                raise ValueError('unsupported request compatibility/cache salt')
            add(kind, row, owner, {'prompt_token_ids': data['prompt_token_ids']}, rid=rid)
        elif kind == 'cache_lookup':
            enq = next(r for r in by_engine[backend_id] if r['kind'] == 'request_enqueue')
            prompt = enq['data']['prompt_token_ids']
            at = data['lookup_started_ns']
            add('request_dispatch', row, owner, {}, rid=rid, timestamp=at)
            add(kind, row, owner, {'backend_lookup': {'prompt_token_ids': prompt,
                'compatibility_id': manifest['compatibility_id'],
                'source_event_id': f"{row['stream_id']}:{row['seq']}"},
                'prompt_tokens': len(prompt), 'cached_tokens': data['cached_tokens'],
                'executed_prefill_tokens': audits[backend_id]['scheduled_prefill_tokens'],
                'backend_blocks': data['blocks']}, rid=rid, timestamp=at)
            for ep, start in list(pending.items()):
                if start['workflow_id'] != owner:
                    continue
                prefix = start['data']['prefix_token_ids']
                if prompt[:len(prefix)] == prefix:
                    add('legal_demand', row, owner, {'compatibility_id': manifest['compatibility_id'],
                        'request_token_ids': prompt}, rid=rid, episode=ep, timestamp=at)
                    del pending[ep]
        elif kind == 'request_release':
            add('request_end', row, owner, {'backend_status': data['status']}, rid=rid)
            ep = 'idle-' + rid
            prefix = data['prefix_token_ids']
            start = add('idle_start', row, owner, {'context_id': owner, 'context_version': 'initial',
                'compatibility_id': manifest['compatibility_id'], 'prefix_token_ids': prefix,
                'backend_blocks': data['blocks_before_release'][0][:len(prefix) // data['block_size']],
                'block_size_tokens': data['block_size']}, episode=ep)
            pending[ep] = start
        elif kind in {'tool_start', 'tool_end'}:
            if kind == 'tool_start':
                ep = 'idle-' + str(rid)
                tool_episodes[data['tool_id']] = ep if ep in pending else None
            ep = tool_episodes.get(data['tool_id'])
            add(kind, row, owner, {'tool_id': data['tool_id'], 'tool_kind': 'bash',
                                  'returncode': data.get('returncode')}, episode=ep)
        elif kind == 'context_retire':
            for ep, start in list(pending.items()):
                if start['workflow_id'] == owner:
                    add(kind, row, owner, {'reason': data['reason']}, episode=ep)
                    del pending[ep]
        elif kind == 'observation_end':
            add(kind, row, owner, {'complete': True, 'reason': 'runner_and_backend_completed_streams'})
            ended_owners.add(owner)
    events.sort(key=lambda e: (e['ts_ns'], int(e['event_id'].split('-')[-1])))
    validate_events(events)
    labels = derive_idle_labels(events)
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in [('events', events), ('episode_labels', labels)]:
        (output / f'{name}.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    summary = {'evidence_kind': 'measured', 'scope': ('controlled_real_inference_not_task_occurrence' if controlled else
                'stage0_mini_swe32B_repository_integration_not_Codex_or_natural_sessions'),
               'owners': len(submits), 'requests': len(clients), 'episodes': len(labels),
               'outcomes': dict(Counter(r['outcome'] for r in labels)),
               'backend_audit': backend_audit, 'block_audit': block_audit,
               'transport': transport, 'files_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.jsonl')}}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary
