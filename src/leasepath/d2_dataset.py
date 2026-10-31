"""Audit measured D2 records, then export causal snapshots and separate labels."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from math import isfinite

from .prediction_state import snapshot
from .trace import derive_idle_labels, validate_events


def client_id(request_id):
    match = re.match(r'chatcmpl-([0-9a-f]{32})(?:$|[-_])', request_id)
    if match is None:
        raise ValueError(f'unrecognized backend request identity: {request_id}')
    return match[1]


def export(raw_path, output, compatibility_id, *, workflow_ids=None,
           elapsed_points_s=None, max_snapshots_per_episode=8):
    points_s = [0, 0.1, 1, 5, 30, 120] if elapsed_points_s is None else list(elapsed_points_s)
    if (not points_s or any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in points_s)
            or type(max_snapshots_per_episode) is not int or max_snapshots_per_episode < 1):
        raise ValueError('invalid snapshot schedule')
    raw_path, output = Path(raw_path), Path(output)
    raw = [json.loads(line) for line in raw_path.read_text().splitlines()]
    sequences = defaultdict(list)
    boots = set()
    for row in raw:
        sequences[row['pid']].append(row['seq'])
        if row['kind'] == 'observer_start':
            boots.add(row['boot_id'])
    if len(boots) != 1:
        raise ValueError('unaligned or missing boot clock identity')
    for sequence in sequences.values():
        if sequence != list(range(1, len(sequence) + 1)):
            raise ValueError('observer sequence gap, reorder, or duplicate')
    raw.sort(key=lambda row: (row['ts_ns'], row['pid'], row['seq']))
    clients = {r['request_id']: r for r in raw if r['kind'] == 'request_ready'}
    submitted = {r['workflow_id']: r for r in raw if r['kind'] == 'workflow_submit'}
    selected = set(workflow_ids or submitted)
    probes = {r['pid']: r['compatibility_id'] for r in raw if r['kind'] == 'probe_installed'}
    for workflow in selected:
        if workflow not in submitted or submitted[workflow].get('compatibility_id') != compatibility_id:
            raise ValueError('workflow compatibility differs from frozen run manifest')
    server = defaultdict(list)
    for row in raw:
        if row['kind'] in {'request_enqueue', 'request_dispatch', 'cache_lookup',
                            'request_release', 'preemption_release', 'prefill_scheduled'}:
            server[client_id(row['request_id'])].append(row)
    releases, lookups = {}, {}
    for rid, ready in clients.items():
        if ready['workflow_id'] not in selected:
            continue
        rows = server[rid]
        counts = Counter(r['kind'] for r in rows)
        for stage in ['request_enqueue', 'request_dispatch', 'cache_lookup', 'request_release']:
            if counts[stage] != 1:
                raise ValueError(f'missing/duplicate backend stage {rid}: {counts}')
        lookup = next(r for r in rows if r['kind'] == 'cache_lookup')
        release = next(r for r in rows if r['kind'] == 'request_release')
        if any(probes.get(r['pid']) != compatibility_id for r in rows):
            raise ValueError('backend probe compatibility differs from frozen run manifest')
        enqueue = next(r for r in rows if r['kind'] == 'request_enqueue')
        dispatch = next(r for r in rows if r['kind'] == 'request_dispatch')
        if not ready['ts_ns'] <= enqueue['ts_ns'] <= dispatch['ts_ns'] <= lookup['ts_ns'] <= release['ts_ns']:
            raise ValueError('backend stages are not in causal order')
        if enqueue['prompt_token_ids'] != lookup['prompt_token_ids']:
            raise ValueError('enqueued tokens differ from actual lookup')
        if counts['preemption_release'] or release['preemptions']:
            raise ValueError('pilot exporter does not support preempted requests')
        if release['status'] not in {'FINISHED_STOPPED', 'FINISHED_LENGTH_CAPPED'}:
            raise ValueError(f'no confirmed normal request completion: {release["status"]}')
        prompt = lookup['prompt_token_ids']
        prefill = sum(r['tokens'] for r in rows if r['kind'] == 'prefill_scheduled')
        if prefill != release['scheduled_prefill_tokens'] or prefill + lookup['cached_tokens'] != len(prompt):
            raise ValueError('engine scheduled prefill and lookup counters disagree')
        if prompt != release['prompt_token_ids']:
            raise ValueError('request tokens changed before release')
        prefix = release['prefix_token_ids']
        if not prefix or prefix != prompt[:len(prefix)] or len(prefix) % release['block_size']:
            raise ValueError('idle prefix was not selected from complete prompt blocks')
        blocks = release['blocks'][0][:len(prefix) // release['block_size']]
        if len(blocks) * release['block_size'] != len(prefix) or any(b['is_null'] or not b['hash'] for b in blocks):
            raise ValueError('idle prefix has no observed hashed cache blocks')
        releases[rid], lookups[rid] = release, lookup
    events, pending, ended = [], {}, set()
    event_number = 0

    def add(kind, row, data, *, rid=None, episode=None):
        nonlocal event_number
        event_number += 1
        workflow = row['workflow_id']
        e = {'schema_version': '0.1', 'run_id': workflow, 'event_id': f'e{event_number:07d}',
             'event_type': kind, 'ts_ns': row['ts_ns'], 'clock_domain': 'monotonic:' + next(iter(boots)),
             'evidence_kind': 'measured', 'workflow_id': workflow, 'data': data}
        if rid:
            e['request_id'] = rid
        if episode:
            e['idle_episode_id'] = episode
        events.append(e)

    tool_links = {}
    for row in raw:
        kind = row['kind']
        if kind in {'request_enqueue', 'request_dispatch', 'cache_lookup', 'request_release'}:
            rid = client_id(row['request_id'])
            if rid not in clients:
                raise ValueError('backend request has no client provenance')
            workflow = clients[rid]['workflow_id']
            row = {**row, 'workflow_id': workflow}
        else:
            workflow = row.get('workflow_id')
            rid = row.get('request_id')
        if workflow not in selected or workflow in ended:
            continue
        if kind == 'workflow_submit':
            add(kind, row, {'source_group': row.get('source_group'), 'task_id': row.get('task_id'),
                            'evidence_scope': row.get('evidence_scope'),
                            **{k: row[k] for k in ['variant', 'generation'] if k in row}})
        elif kind in {'request_ready', 'request_enqueue', 'request_dispatch'}:
            # Link only an episode already known at this time; no future match.
            candidates = [ep for ep, start in pending.items() if start['workflow_id'] == workflow]
            data = {}
            if kind == 'request_enqueue':
                data['prompt_token_ids'] = row['prompt_token_ids']
            add(kind, row, data, rid=rid, episode=candidates[-1] if candidates else None)
        elif kind == 'cache_lookup':
            release = releases[rid]
            add(kind, row, {'prompt_tokens': len(row['prompt_token_ids']),
                           'backend_lookup': {'prompt_token_ids': row['prompt_token_ids'],
                                              'compatibility_id': compatibility_id,
                                              'source_event_id': f"{row['pid']}:{row['seq']}"},
                           'cached_tokens': row['cached_tokens'],
                           'executed_prefill_tokens': release['scheduled_prefill_tokens'],
                           'prefill_counter_scope': 'scheduler_tokens_confirmed_by_normal_request_completion',
                           'backend_blocks': row['blocks'], 'backend_pid': row['pid']}, rid=rid)
            for ep, start in list(pending.items()):
                prefix = start['data']['prefix_token_ids']
                if start['workflow_id'] == workflow and row['prompt_token_ids'][:len(prefix)] == prefix:
                    add('legal_demand', row, {'compatibility_id': compatibility_id,
                                             'request_token_ids': row['prompt_token_ids']}, rid=rid, episode=ep)
                    del pending[ep]
        elif kind == 'request_release':
            add('request_end', row, {'backend_status': row['status']}, rid=rid)
            ep = 'idle-' + rid
            ready = clients[rid]
            add('idle_start', row, {'context_id': ready.get('owner', workflow),
                                   'context_version': ready.get('context_version', rid),
                                   'compatibility_id': compatibility_id,
                                   'prefix_token_ids': row['prefix_token_ids'],
                                   'backend_blocks': row['blocks'][0][:len(row['prefix_token_ids']) // row['block_size']],
                                   'backend_pid': row['pid'], 'block_size_tokens': row['block_size']}, episode=ep)
            pending[ep] = events[-1]
        elif kind in {'tool_start', 'tool_end'}:
            if kind == 'tool_start':
                ep = 'idle-' + rid if 'idle-' + str(rid) in pending else None
                tool_links[row['tool_id']] = ep
            else:
                ep = tool_links.get(row['tool_id'])
            add(kind, row, {'tool_id': row['tool_id'], 'tool_kind': 'bash',
                            'command_category': row.get('command_category'),
                            'returncode': row.get('returncode')}, episode=ep)
        elif kind == 'context_close':
            for ep, start in list(pending.items()):
                if start['workflow_id'] == workflow:
                    add('context_retire', row, {'reason': row['reason'], 'agent_exit': row.get('agent_exit')}, episode=ep)
                    del pending[ep]
        elif kind in {'workflow_end', 'workflow_observation_end'}:
            if kind == 'workflow_end':
                add('workflow_end', row, {})
            add('observation_end', row, {'reason': kind, 'complete': True})
            ended.add(workflow)
    if selected - ended:
        raise ValueError('selected workflows lack explicit observation ends')
    validation = validate_events(events)
    labels = derive_idle_labels(events)
    starts = {e['idle_episode_id']: e for e in events if e['event_type'] == 'idle_start'}
    samples, targets = [], []
    for label in labels:
        start = starts[label['idle_episode_id']]
        # Fixed elapsed points; no future duration is used to choose t0.
        points = {start['ts_ns'] + int(elapsed * 1e9) for elapsed in points_s}
        points.update(e['ts_ns'] for e in events if e.get('idle_episode_id') == label['idle_episode_id']
                      and e['event_type'] in {'tool_start', 'tool_end', 'request_enqueue', 'request_dispatch'})
        episode_samples = []
        for t0 in sorted(points):
            if t0 >= label['end_ns']:
                continue
            if len(episode_samples) == max_snapshots_per_episode:
                break
            sample = snapshot(events, label['run_id'], label['idle_episode_id'], t0,
                              complete_service_events=False)
            key = f"{label['run_id']}:{label['idle_episode_id']}:{t0}"
            sample.update(sample_id=key, source_group=submitted[label['run_id']].get('source_group'),
                          split='pilot_development_only')
            episode_samples.append(sample)
            samples.append(sample)
            targets.append({'sample_id': key, 'outcome': label['outcome'],
                            'remaining_s': (label['end_ns'] - t0) / 1e9})
        for sample in episode_samples:
            sample['sample_weight'] = 1 / len(episode_samples)
    output.mkdir(parents=True, exist_ok=True)
    for name, rows in [('events', events), ('episode_labels', labels), ('snapshots', samples), ('targets', targets)]:
        (output / f'{name}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    lookup_rows = [lookups[rid] for rid, ready in clients.items() if ready['workflow_id'] in selected]
    summary = {'status': 'passed', 'evidence_kind': 'measured', **validation,
               'snapshot_schedule': {'elapsed_points_s': points_s, 'max_per_episode': max_snapshots_per_episode},
               'source_groups': dict(Counter(submitted[w].get('source_group') for w in selected)),
               'evidence_scopes': dict(Counter(submitted[w].get('evidence_scope') for w in selected)),
               'snapshots': len(samples), 'outcomes': dict(Counter(l['outcome'] for l in labels)),
               'requests': len(lookup_rows), 'cache_hit_requests': sum(r['cached_tokens'] > 0 for r in lookup_rows),
               'cached_tokens': sum(r['cached_tokens'] for r in lookup_rows),
               'prompt_tokens': sum(len(r['prompt_token_ids']) for r in lookup_rows),
               'all_request_stages_observed': True, 'observer_sequence_gaps': 0,
               'lookup_duration_ns': [r['lookup_duration_ns'] for r in lookup_rows],
               'raw_sha256': hashlib.sha256(raw_path.read_bytes()).hexdigest(),
               'output_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.jsonl')},
               'limitations': ['development pilot; source grouping and evidence scopes must be retained',
                              'scheduler prefill counts; no device-kernel timing claim',
                              'request-boundary block snapshots; no complete allocation/eviction lifecycle',
                              'queue_length remains unknown; no global empty-queue assertion',
                              'no independent test split or policy benefit claim']}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary
