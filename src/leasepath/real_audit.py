"""Independent stage-0 audits of actual engine observations.

These checks do not promote an open log to complete U/D/C evidence. Labels
require separately validated completed streams and explicit owner boundaries.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path


def load_records(directory):
    records = []
    for path in Path(directory).glob('*.jsonl'):
        previous = -1
        stream_id = None
        for seq, line in enumerate(path.read_text().splitlines()):
            row = json.loads(line)
            if row['seq'] != seq or row['ts_ns'] < previous:
                raise ValueError(f'stream sequence/clock error: {path}')
            if stream_id is not None and stream_id != row['stream_id']:
                raise ValueError('stream identity changed')
            previous, stream_id = row['ts_ns'], row['stream_id']
            records.append(row)
    if len({r['boot_id'] for r in records}) != 1 or len({r['run_id'] for r in records}) != 1:
        raise ValueError('missing or mixed run/boot identity')
    return sorted(records, key=lambda r: (r['ts_ns'], r['stream_id'], r['seq']))


def audit_requests(records):
    requests = defaultdict(list)
    configs, ranks = [], []
    for row in records:
        if row['kind'] == 'cache_config':
            configs.append(row)
        if row['kind'] == 'rank_cache_config':
            ranks.append(row['data'])
        rid = row['data'].get('request_id')
        if rid and row['kind'] in {'request_enqueue', 'cache_lookup', 'prefill_scheduled',
                                  'request_release', 'preemption_release'}:
            requests[rid].append(row)
    if len(configs) != 1:
        raise ValueError('expected one logical scheduler cache config')
    config = configs[0]['data']
    rank_ids = [r['rank'] for r in ranks]
    if len(set(rank_ids)) != len(rank_ids):
        raise ValueError('duplicate rank physical config')
    if any(r['num_blocks'] != config['num_blocks'] for r in ranks):
        raise ValueError('rank block count differs from scheduler')
    results = []
    for rid, rows in requests.items():
        counts = Counter(r['kind'] for r in rows)
        if counts['request_enqueue'] != 1:
            raise ValueError('duplicate/missing enqueue')
        enq = next(r for r in rows if r['kind'] == 'request_enqueue')
        lookups = [r for r in rows if r['kind'] == 'cache_lookup']
        releases = [r for r in rows if r['kind'] == 'request_release']
        if len(releases) != 1 or not lookups:
            results.append({'request_id': rid, 'complete': False, 'counts': dict(counts)})
            continue
        release = releases[0]
        if not enq['ts_ns'] <= lookups[0]['data']['lookup_started_ns'] <= release['ts_ns']:
            raise ValueError('request lifecycle clock disorder')
        prompt = enq['data']['prompt_token_ids']
        prefix = release['data']['prefix_token_ids']
        blocksize = release['data']['block_size']
        if prefix != prompt[:len(prompt) // blocksize * blocksize]:
            raise ValueError('prefix was not chosen from current input at release')
        selected = release['data']['blocks_before_release'][0][:len(prefix) // blocksize]
        normal = release['data']['status'] in {'FINISHED_STOPPED', 'FINISHED_LENGTH_CAPPED'}
        prefix_eligible = (bool(prefix) and len(selected) == len(prefix) // blocksize and
                           all(b['hash'] is not None and not b['is_null'] for b in selected))
        if normal and prefix and not prefix_eligible:
            raise ValueError('selected prefix has no physical hashed block evidence')
        prefill = sum(r['data']['tokens'] for r in rows if r['kind'] == 'prefill_scheduled')
        cached = lookups[0]['data']['cached_tokens']
        preemptions = counts['preemption_release']
        if normal and not preemptions and prefill + cached != len(prompt):
            raise ValueError('prefill+cached count differs from prompt')
        results.append({'request_id': rid, 'complete': True, 'normal_completion': normal,
                        'prompt_tokens': len(prompt), 'cached_tokens_initial': cached,
                        'scheduled_prefill_tokens': prefill, 'lookup_count': len(lookups),
                        'preemptions': preemptions, 'prefix_tokens': len(prefix),
                        'idle_prefix_eligible': normal and prefix_eligible,
                        'enqueue_ns': enq['ts_ns'], 'lookup_ns': lookups[0]['data']['lookup_started_ns'],
                        'release_ns': release['ts_ns']})
    return {'requests': results, 'logical_cache_config': config,
            'rank_ids': sorted(rank_ids), 'physical_kv_bytes_by_rank': {str(r['rank']): r['physical_kv_bytes'] for r in ranks},
            'total_physical_kv_bytes': sum(r['physical_kv_bytes'] for r in ranks),
            'scope': 'actual_backend_consistency; transport completeness and owner semantics separate'}


def audit_blocks(records):
    generations = defaultdict(int)
    states = {}
    counts = Counter()
    evictions = 0
    max_refs = 0
    candidates = None
    candidate_checks = 0
    saw_candidates = False
    for row in records:
        kind, data = row['kind'], row['data']
        if kind == 'allocation_candidates':
            if candidates is not None:
                raise ValueError('allocation candidate record was not consumed')
            queue = data['ordered_candidates']
            if (len({b['slot'] for b in queue}) != len(queue) or
                    any(b['ref_count'] or b['is_null'] for b in queue) or data['requested'] > len(queue)):
                raise ValueError('invalid free allocation candidate queue')
            candidates = data
            saw_candidates = True
        elif kind == 'block_allocate':
            if saw_candidates:
                if (candidates is None or candidates['request_id'] != data['request_id'] or
                        [b['slot'] for b in candidates['ordered_candidates'][:candidates['requested']]] !=
                        [b['slot'] for b in data['blocks']]):
                    raise ValueError('allocation differs from actual candidate queue order')
                candidate_checks += 1
                candidates = None
            for b in data['blocks']:
                slot = b['slot']
                if slot in states and states[slot]['ref_count']:
                    raise ValueError('allocated a previously active slot')
                if (b['generation'] != generations[slot] + 1 or b['ref_count'] != 1 or
                        b['is_null'] or b['hash'] is not None):
                    raise ValueError('invalid new slot generation or allocation refcount')
                generations[slot] = b['generation']
                states[slot] = b
                max_refs = max(max_refs, b['ref_count'])
                counts['allocations'] += 1
        elif kind in {'block_touch', 'block_free', 'block_cache_insert'}:
            if len(data['before']) != len(data['after']):
                raise ValueError('different before/after block cardinality')
            for before, after in zip(data['before'], data['after']):
                slot = before['slot']
                if before['is_null']:
                    continue
                if before['generation'] != generations[slot] or after['generation'] != before['generation']:
                    raise ValueError('block generation changed outside allocation')
                if after['slot'] != slot or after['is_null'] != before['is_null']:
                    raise ValueError('block identity changed within transition')
                if slot in states and before != states[slot]:
                    raise ValueError('block transition does not match previous state')
                change = 1 if kind == 'block_touch' else -1 if kind == 'block_free' else 0
                if after['ref_count'] != before['ref_count'] + change or after['ref_count'] < 0:
                    raise ValueError('invalid block refcount transition')
                if kind != 'block_cache_insert' and after['hash'] != before['hash']:
                    raise ValueError('reference transition changed cached identity')
                states[slot] = after
                max_refs = max(max_refs, before['ref_count'], after['ref_count'])
                counts[kind] += 1
        elif kind == 'block_evict':
            before, after = data['before'], data['after']
            if before['ref_count'] != 0 or after['hash'] is not None:
                raise ValueError('evicted active block or retained old hash')
            if before['generation'] != generations[before['slot']]:
                raise ValueError('eviction generation differs')
            if states.get(before['slot']) != before or after != {**before, 'hash': None}:
                raise ValueError('eviction differs from previous block state')
            states[before['slot']] = after
            evictions += 1
        elif kind == 'cache_reset' and data['success']:
            for b in data['cached_before']:
                if b['ref_count']:
                    raise ValueError('cache reset with active block')
                if states.get(b['slot']) != b:
                    raise ValueError('cache reset differs from previous block state')
                states[b['slot']] = {**b, 'hash': None}
    if candidates is not None:
        raise ValueError('allocation candidate record has no allocation result')
    return {'status': 'passed', 'counts': dict(counts), 'capacity_evictions': evictions,
            'max_refcount': max_refs,
            'allocation_candidate_checks': candidate_checks,
            'active_slots_at_end': sum(b['ref_count'] > 0 for b in states.values()),
            'max_generation': max(generations.values(), default=0),
            'scope': 'logical_slot_lifecycle; rank_physical_bytes_accounted_separately'}
