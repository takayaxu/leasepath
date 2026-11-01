"""Versioned general lifecycle projection; retains every real lookup and gap."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator

from .real_audit import load_records, audit_requests, audit_blocks
from .real_observer import validate_stream, validate_observation

VERSION = 'real-relations-v2.1'
ATTEMPT_FIELDS = ('owner', 'context_version', 'logical_request_id', 'attempt_id', 'purpose', 'wire_api', 'retry_of')


def reference(row):
    return {'stream_id': row['stream_id'], 'seq': row['seq'], 'ts_ns': row['ts_ns']}


def project(raw, client_rows, compatibility, *, legacy=False):
    """Pure projection after separate transport/rank/block audit.

    Unsupported mappings remain in coverage. Never invent context versions or
    attempt chains for historical records. legacy allows only existing explicit
    initial-version mini-swe demand records, retaining missing-field warnings.
    """
    validator = Draft202012Validator(json.loads((Path(__file__).resolve().parents[2]/'schemas/real_attempt_v2.schema.json').read_text()))
    by_backend = defaultdict(list)
    for row in raw:
        if row['kind'] in {'request_enqueue', 'cache_lookup', 'prefill_scheduled', 'preemption_release', 'request_release'}:
            by_backend[row['data']['request_id']].append(row)
    attempts, owners, closed_versions = {}, set(), set()
    for row in client_rows:
        data = row['data']
        if row['kind'] in {'workflow_submit', 'context_open'} and 'context_version' in data:
            owners.add((data['owner'], data['context_version']))
        if row['kind'] == 'request_ready':
            rid = data['request_id']
            if rid in attempts:
                raise ValueError('duplicate client attempt')
            attempts[rid] = row
    mapped, coverage, all_attempt_ids = {}, [], {}
    for rid, row in sorted(attempts.items(), key=lambda item: item[1]['ts_ns']):
        data = row['data']
        missing = [key for key in ATTEMPT_FIELDS if key not in data]
        old_mini = (legacy and row['source'] == 'mini_swe_runner' and
                    all(key in data for key in ['owner', 'context_version']))
        api = data.get('wire_api', 'chat' if old_mini else None)
        prefixes = ([f'chatcmpl-{rid}-'] if api == 'chat' else [f'cmpl-{rid}-0-'] if api == 'completion'
                    else [f'chatcmpl-{rid}-', f'cmpl-{rid}-0-'])
        matches = [key for key in by_backend if any(key.startswith(prefix) and len(key) > len(prefix) for prefix in prefixes)]
        reasons = []
        if len(matches) != 1:
            reasons.append('missing_or_ambiguous_backend_mapping')
        if missing and not old_mini:
            reasons.append('missing_attempt_fields:' + ','.join(missing))
        if (data.get('owner'), data.get('context_version')) not in owners:
            reasons.append('missing_explicit_context_open')
        if not missing:
            validator.validate(data)
            aid = data['attempt_id']
            if aid in all_attempt_ids:
                raise ValueError('duplicate attempt identity')
            retry = data['retry_of']
            if retry is not None:
                predecessor = all_attempt_ids.get(retry)
                if predecessor is None or any(predecessor[k] != data[k] for k in
                        ['owner', 'context_version', 'logical_request_id', 'purpose']):
                    raise ValueError('invalid retry chain')
            elif any(previous['logical_request_id'] == data['logical_request_id'] for previous in all_attempt_ids.values()):
                raise ValueError('repeated logical request without retry_of')
            if data['purpose'] not in {'demand', 'auxiliary', 'auxiliary_no_demand'} or api not in {'chat', 'completion'}:
                reasons.append('unsupported_purpose_or_wire_api')
            all_attempt_ids[aid] = data
        item = {'client_request_id': rid, 'backend_request_ids': matches, 'missing_fields': missing,
                'owner': data.get('owner'), 'context_version': data.get('context_version'),
                'purpose': data.get('purpose', 'legacy_demand' if old_mini else None),
                'logical_request_id': data.get('logical_request_id'), 'attempt_id': data.get('attempt_id'),
                'retry_of': data.get('retry_of'), 'reasons': reasons, 'mapping_source': reference(row),
                'status': 'unsupported' if reasons else 'projected_legacy' if old_mini else 'projected'}
        if len(matches) == 1:
            backend = matches[0]
            if backend in mapped:
                raise ValueError('backend mapped twice')
            rows = by_backend[backend]
            enqueue = [r for r in rows if r['kind'] == 'request_enqueue']
            releases = [r for r in rows if r['kind'] == 'request_release']
            lookups = [r for r in rows if r['kind'] == 'cache_lookup']
            if len(enqueue) != 1 or len(releases) != 1 or not lookups:
                raise ValueError('incomplete backend lifecycle')
            enq = enqueue[0]
            if enq['data']['compatibility_id'] != compatibility:
                raise ValueError('backend compatibility differs')
            if row['ts_ns'] > enq['ts_ns']:
                raise ValueError('enqueue precedes client readiness')
            link = [r for r in client_rows if r['kind'] == 'backend_link' and r['data']['request_id'] == rid]
            if link and (len(link) != 1 or link[0]['data']['backend_request_id'] != backend):
                raise ValueError('explicit backend link disagrees with wire mapping')
            item.update(lookup_count=len(lookups), preemptions=sum(r['kind'] == 'preemption_release' for r in rows),
                        terminal_status=releases[0]['data']['status'], enqueue_source=reference(enq))
            mapped[backend] = (row, item)
        coverage.append(item)
    for backend, rows in by_backend.items():
        if backend not in mapped:
            coverage.append({'backend_request_ids': [backend], 'client_request_id': None,
                             'status': 'unmapped', 'purpose': None,
                             'reasons': ['no_client_attempt_evidence'], 'lookup_count': sum(r['kind']=='cache_lookup' for r in rows)})
    events, labels, protection, pending = [], [], [], {}
    active, retired, ended, opened = {}, set(), set(), set()
    timeline = sorted(raw + client_rows, key=lambda r: (r['data'].get('lookup_started_ns', r['ts_ns'])
                     if r['kind'] == 'cache_lookup' else r['ts_ns'], r['stream_id'], r['seq']))

    def finish(ep, row, outcome, *, backend=None, at=None):
        start = pending.pop(ep)
        timestamp = row['data'].get('lookup_started_ns', row['ts_ns']) if at is None else at
        if timestamp < start['start_ns']:
            raise ValueError('negative relationship duration')
        labels.append({**start, 'outcome': outcome, 'end_ns': timestamp, 'duration_ns': timestamp-start['start_ns'],
                       'end_source': reference(row), 'demand_backend_request_id': backend})

    for row in timeline:
        kind, data = row['kind'], row['data']
        key = (data.get('owner'), data.get('context_version'))
        if kind in {'workflow_submit', 'context_open'} and key in owners:
            if key in opened or key in retired:
                raise ValueError('context version reopened')
            opened.add(key)
            events.append({'version': VERSION, 'kind': kind, 'data': data, 'source': reference(row)})
        elif kind == 'context_restore':
            if key not in opened or key in retired or key[0] in ended:
                raise ValueError('restored nonexistent or retired context')
            events.append({'version': VERSION, 'kind': kind, 'data': data, 'source': reference(row)})
        elif kind in {'context_fork', 'context_compact'}:
            events.append({'version': VERSION, 'kind': kind, 'data': data, 'source': reference(row)})
        elif kind == 'context_retire' and key in opened:
            if key in retired:
                raise ValueError('duplicate context retirement')
            if key[0] in ended:
                continue  # Cleanup after observation is not a retrospectively observed D.
            for ep, start in list(pending.items()):
                if (start['owner'], start['context_version']) == key:
                    finish(ep, row, 'D')
            retired.add(key)
            events.append({'version': VERSION, 'kind': kind, 'data': data, 'source': reference(row)})
        elif kind == 'observation_end' and data.get('owner') is not None:
            owner = data['owner']
            if owner in ended:
                raise ValueError('duplicate owner observation end')
            if any(value['owner'] == owner for value in active.values()):
                raise ValueError('owner observation ended with pending backend request')
            for ep, start in list(pending.items()):
                if start['owner'] == owner:
                    finish(ep, row, 'C')
            ended.add(owner)
            events.append({'version': VERSION, 'kind': kind, 'data': data, 'source': reference(row)})
        backend = data.get('request_id')
        if backend not in mapped or kind not in {'request_enqueue','cache_lookup','preemption_release','request_release','prefill_scheduled'}:
            continue
        ready, covered = mapped[backend]
        if covered['status'] not in {'projected', 'projected_legacy'}:
            continue
        attempt = ready['data']
        key = (attempt['owner'], attempt['context_version'])
        if key not in opened or key[0] in ended or (key in retired and backend not in active):
            raise ValueError('backend uses unopened, retired or unobserved context')
        enqueue = next(r for r in by_backend[backend] if r['kind'] == 'request_enqueue')
        prompt = enqueue['data']['prompt_token_ids']
        salt = enqueue['data'].get('cache_salt')
        events.append({'version': VERSION, 'kind': kind, 'backend_request_id': backend,
                       'owner': key[0], 'context_version': key[1], 'source': reference(row), 'data': data})
        if kind == 'request_enqueue':
            active[backend] = {'owner': key[0], 'context_version': key[1], 'backend_request_id': backend,
                               'start_ns': row['ts_ns'], 'start_source': reference(row), 'preemptions': []}
        elif kind == 'preemption_release':
            if backend not in active:
                raise ValueError('preemption without pending request')
            active[backend]['preemptions'].append(reference(row))
        elif kind == 'cache_lookup':
            if backend not in active:
                raise ValueError('lookup without pending request')
            at = data['lookup_started_ns']
            if not enqueue['ts_ns'] <= at <= row['ts_ns']:
                raise ValueError('invalid actual lookup timestamp')
            if key in retired:
                continue
            for ep, start in list(pending.items()):
                if (start['owner'], start['context_version']) == key and start['cache_salt'] == salt and prompt[:len(start['prefix_token_ids'])] == start['prefix_token_ids']:
                    finish(ep, row, 'U', backend=backend)
        elif kind == 'request_release':
            if backend not in active:
                raise ValueError('terminal release without pending request')
            protection.append({**active.pop(backend), 'end_ns': row['ts_ns'], 'end_source': reference(row),
                               'rule': 'pending_and_active_protected_including_preemption_wait'})
            normal = data['status'] in {'FINISHED_STOPPED','FINISHED_LENGTH_CAPPED'}
            if not normal or key in retired:
                continue
            size = data['block_size']
            prefix = prompt[:len(prompt)//size*size]
            blocks = data['blocks_before_release'][0][:len(prefix)//size]
            if data['prefix_token_ids'] != prefix or not prefix or len(blocks) != len(prefix)//size or any(b['is_null'] or b['hash'] is None for b in blocks):
                raise ValueError('idle prefix has no current-input full-block evidence')
            ep = f'{backend}:release:{row["seq"]}'
            pending[ep] = {'version': VERSION, 'episode_id': ep, 'owner': key[0], 'context_version': key[1],
                'compatibility_id': compatibility, 'cache_salt': salt, 'prefix_token_ids': prefix,
                'backend_blocks': blocks, 'start_ns': row['ts_ns'], 'start_source': reference(row)}
    if active:
        raise ValueError('pending backend requests at complete stream end')
    # Incomplete owner semantics do not become administrative C.
    unresolved = [{**p, 'reason': 'missing_explicit_owner_observation_end'} for p in pending.values()]
    # An unsupported request may conceal an earlier compatible demand. Its
    # absence from projection must never manufacture a first U, D or C.
    accepted = []
    opaque = [item for item in coverage if item['status'] not in {'projected','projected_legacy'}]
    for label in labels:
        ambiguous = []
        for item in opaque:
            if item.get('owner') not in {None, label['owner']}:
                continue
            if item.get('context_version') not in {None, label['context_version']}:
                continue
            for bid in item['backend_request_ids']:
                enq = next(r for r in by_backend[bid] if r['kind']=='request_enqueue')
                if (enq['data'].get('cache_salt') != label['cache_salt'] or
                    enq['data']['prompt_token_ids'][:len(label['prefix_token_ids'])] != label['prefix_token_ids']):
                    continue
                if any(r['kind']=='cache_lookup' and label['start_ns'] <= r['data']['lookup_started_ns'] <= label['end_ns']
                       for r in by_backend[bid]):
                    ambiguous.append(bid)
        if ambiguous:
            unresolved.append({**label, 'outcome': None, 'reason':'unprojected_compatible_lookup_before_endpoint',
                               'ambiguous_backend_requests':ambiguous})
        else:
            accepted.append(label)
    labels = accepted
    return {'events': events, 'labels': labels, 'coverage': coverage, 'protection': protection,
            'unresolved': unresolved, 'summary': {'version': VERSION, 'backend_requests': len(by_backend),
                'coverage_status': dict(Counter(r['status'] for r in coverage)),
                'outcomes': dict(Counter(r['outcome'] for r in labels)), 'unresolved_relations': len(unresolved),
                'all_lookup_events_retained': sum(e['kind']=='cache_lookup' for e in events)}}


def export_v2(run, client_paths, output, *, legacy=False):
    run, output = Path(run), Path(output)
    raw = load_records(run / 'raw')
    expected = json.loads((run / 'expected-streams.json').read_text())['stream_ids']
    transport = validate_observation((run / 'raw').glob('*.jsonl'), expected_stream_ids=expected)
    if any(r['kind'] == 'observation_gap' for r in raw):
        raise ValueError('observation gap')
    backend = audit_requests(raw)
    blocks = audit_blocks(raw)
    manifest = json.loads((run / 'service-manifest.json').read_text())
    ranks = list(range(len(manifest['devices'])))
    checkpoint = [r for r in raw if r['kind']=='engine_checkpoint']
    if (len(checkpoint)!=1 or checkpoint[0]['data']['outstanding_requests'] or
        sorted(r['data']['rank'] for r in raw if r['kind']=='rank_checkpoint') != ranks or
        backend['rank_ids'] != ranks or any(not r['complete'] for r in backend['requests'])):
        raise ValueError('missing drained TP checkpoint')
    clients = []
    for path in client_paths:
        info = validate_stream(path)
        if info['boot_id'] != transport['streams'][0]['boot_id'] or info['run_id'] != run.name:
            raise ValueError('client boot/run differs')
        clients.extend(json.loads(s) for s in Path(path).read_text().splitlines())
    result = project(raw, clients, manifest['compatibility_id'], legacy=legacy)
    output.mkdir(parents=True, exist_ok=False)
    for key in ['events','labels','coverage','protection','unresolved']:
        (output/(key+'.v2.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in result[key]))
    summary = {**result['summary'], 'backend_audit': backend, 'block_audit': blocks,
               'transport': transport, 'semantics_sha256': hashlib.sha256((Path(__file__).resolve().parents[2]/'docs/real_projection_v2.md').read_bytes()).hexdigest(),
               'files_sha256': {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.glob('*.jsonl')}}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary
