"""Event validation and policy-independent idle labels.

v0.1 requires one aligned monotonic clock per run. Multi-host clocks must be
aligned before ingestion. Future labels are written separately from raw events.
"""
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from .cache import block_keys

SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'schemas' / 'trace.schema.json'


def read_events(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _validate_physical_lookup(event, slots):
    """Validate complete synthetic lookup snapshots, not real backend lifecycles."""
    data = event['data']
    if event['evidence_kind'] != 'synthetic':
        raise ValueError('complete synthetic physical snapshots require synthetic evidence')
    tokens, size = data['prompt_token_ids'], data['block_size_tokens']
    if len(tokens) != data['prompt_tokens'] or data['cached_tokens'] % size:
        raise ValueError('physical snapshot token counters disagree')
    keys = block_keys(tokens, data['compatibility_id'], size)
    refs = data['physical_blocks']
    if len(refs) != len(keys):
        raise ValueError('physical block count differs from prompt blocks')
    used = set()
    hit_blocks = data['cached_tokens'] // size
    for index, (key, ref) in enumerate(zip(keys, refs)):
        if ref['block_key'] != key:
            raise ValueError('physical block key differs from compatible token prefix')
        epoch, device, slot, generation = ref['physical_id']
        slot_key = (event['run_id'], epoch, device, slot)
        if slot_key in used:
            raise ValueError('physical slot repeated within one lookup')
        used.add(slot_key)
        previous = slots.get(slot_key)
        if previous is None:
            if generation != 1:
                raise ValueError('first physical allocation must have generation 1')
        elif generation == previous[0]:
            if (key, ref['bytes']) != previous[1:]:
                raise ValueError('same physical generation changed block key or bytes')
        elif generation != previous[0] + 1:
            raise ValueError('physical allocation generation must increase by one')
        if ref['cache_hit'] != (index < hit_blocks):
            raise ValueError('physical hit flags disagree with continuous prefix counter')
        if ref['cache_hit'] and previous != (generation, key, ref['bytes']):
            raise ValueError('physical hit has no previously observed matching allocation')
        slots[slot_key] = (generation, key, ref['bytes'])


def validate_events(events):
    validator = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
    seen = set()
    last = {}
    clocks = {}
    evidence = {}
    ended = set()
    starts = {}
    closed = set()
    request_state = {}
    workflow_start = {}
    completions = {}
    readies = []
    lookups = {}
    demands = []
    tools = {}
    linked_tools = []
    physical_slots = {}
    physical_runs = set()
    lookup_scopes = {}
    order = {'request_ready': 0, 'request_enqueue': 1, 'request_dispatch': 2,
             'cache_lookup': 3, 'request_end': 4}
    for e in events:
        validator.validate(e)
        run, kind, t = e['run_id'], e['event_type'], e['ts_ns']
        key = (run, e['event_id'])
        if key in seen:
            raise ValueError(f'duplicate event: {key}')
        seen.add(key)
        if run in ended:
            raise ValueError('event after observation_end')
        if run in clocks and clocks[run] != e['clock_domain']:
            raise ValueError('unaligned clock domains')
        if run in evidence and evidence[run] != e['evidence_kind']:
            raise ValueError('mixed evidence kinds in one run')
        clocks[run], evidence[run] = e['clock_domain'], e['evidence_kind']
        if t < last.get(run, 0):
            raise ValueError('events must be ordered by monotonic timestamp')
        last[run] = t
        if kind == 'workflow_submit':
            wk = (run, e['workflow_id'])
            if wk in workflow_start:
                raise ValueError('duplicate workflow submit')
            workflow_start[wk] = t
        if kind == 'workflow_end' and (run, e['workflow_id']) not in workflow_start:
            raise ValueError('workflow end without submit')
        if kind in order:
            rq = (run, e['request_id'])
            previous = request_state.get(rq)
            if (previous is None and kind != 'request_ready') or (previous is not None and order[kind] != previous[0]+1):
                raise ValueError(f'missing or out-of-order request stage: {rq} {kind}')
            if previous is not None and previous[1] != e['workflow_id']:
                raise ValueError('request changed workflow identity')
            request_state[rq] = (order[kind], e['workflow_id'])
        if kind == 'cache_lookup':
            d = e['data']
            if d['cached_tokens'] + d['executed_prefill_tokens'] != d['prompt_tokens']:
                raise ValueError('cache counters do not sum to prompt tokens')
            if 'backend_lookup' in d and len(d['backend_lookup']['prompt_token_ids']) != d['prompt_tokens']:
                raise ValueError('measured lookup token count differs from prompt counter')
            lookups[(run, e['request_id'])] = e
            scoped = 'physical_trace_scope' in d
            if run in lookup_scopes and lookup_scopes[run] != scoped:
                raise ValueError('complete physical trace is missing a lookup snapshot')
            lookup_scopes[run] = scoped
            if 'physical_trace_scope' in d:
                physical_runs.add(run)
                _validate_physical_lookup(e, physical_slots)
        if kind in {'tool_start', 'tool_end'}:
            tool = (run, e['data']['tool_id'])
            if kind == 'tool_start':
                if tool in tools:
                    raise ValueError('duplicate tool invocation')
                tools[tool] = (e, None)
                if 'dependency_request_id' in e['data']:
                    linked_tools.append(tool)
            else:
                if tool not in tools or tools[tool][1]:
                    raise ValueError('tool end without open invocation')
                start = tools[tool][0]
                if start['workflow_id'] != e['workflow_id']:
                    raise ValueError('tool changed workflow identity')
                for field in ('dependency_request_id', 'target_request_id'):
                    if start['data'].get(field) != e['data'].get(field):
                        raise ValueError('tool changed dependency or target identity')
                tools[tool] = (start, e)
        if kind == 'request_ready':
            readies.append(e)
        if kind == 'request_end':
            completions[(run, e['request_id'])] = t
        episode = (run, e.get('idle_episode_id'))
        if kind == 'idle_start':
            if episode in starts:
                raise ValueError('duplicate idle episode')
            starts[episode] = e
        if kind in {'legal_demand', 'context_retire'}:
            if episode not in starts or episode in closed:
                raise ValueError('outcome without open idle episode')
            if kind == 'legal_demand':
                demands.append(e)
                prefix = starts[episode]['data']
                request = e['data']
                tokens = prefix['prefix_token_ids']
                if prefix['compatibility_id'] != request['compatibility_id'] or request['request_token_ids'][:len(tokens)] != tokens:
                    raise ValueError('claimed legal demand is not a compatible token prefix')
            closed.add(episode)
        if kind == 'observation_end':
            ended.add(run)
    if set(last) != ended:
        raise ValueError('every run must have observation_end for censoring')
    for e in demands:
        lookup = lookups.get((e['run_id'], e['request_id']))
        if lookup is None or lookup['ts_ns'] != e['ts_ns']:
            raise ValueError('legal demand must reference an observed lookup at the same time')
        if len(e['data']['request_token_ids']) != lookup['data']['prompt_tokens']:
            raise ValueError('legal demand token count differs from referenced lookup')
        if 'prompt_token_ids' in lookup['data'] and (
                e['data']['request_token_ids'] != lookup['data']['prompt_token_ids'] or
                e['data']['compatibility_id'] != lookup['data']['compatibility_id']):
            raise ValueError('legal demand differs from referenced lookup tokens or compatibility')
        backend = lookup['data'].get('backend_lookup')
        if backend is not None and (
                e['data']['request_token_ids'] != backend['prompt_token_ids'] or
                e['data']['compatibility_id'] != backend['compatibility_id']):
            raise ValueError('legal demand differs from measured backend lookup')
    if any('physical_trace_scope' not in e['data'] for (run, _), e in lookups.items() if run in physical_runs):
        raise ValueError('complete physical trace is missing a lookup snapshot')
    ready_by_id = {(e['run_id'], e['request_id']): e for e in readies}
    for e in readies:
        dep_ids = [dep['request_id'] for dep in e['data'].get('dependencies', [])]
        if len(dep_ids) != len(set(dep_ids)):
            raise ValueError('duplicate request dependency')
        for dep in e['data'].get('dependencies', []):
            if type(dep.get('delay_ns')) is not int or dep['delay_ns'] < 0:
                raise ValueError('invalid dependency delay')
            end = completions.get((e['run_id'], dep['request_id']))
            if end is None or end+dep['delay_ns'] > e['ts_ns']+1:
                raise ValueError('request ready before dependency completion')
            if request_state[(e['run_id'], dep['request_id'])][1] != e['workflow_id']:
                raise ValueError('request dependency changed workflow identity')
    for tool in linked_tools:
        start, end = tools[tool]
        run, data = start['run_id'], start['data']
        predecessor = (run, data['dependency_request_id'])
        finished = completions.get(predecessor)
        if finished is None or abs(start['ts_ns'] - finished) > 1:
            raise ValueError('linked tool must start at predecessor completion')
        if request_state[predecessor][1] != start['workflow_id']:
            raise ValueError('linked tool predecessor changed workflow identity')
        target = ready_by_id.get((run, data['target_request_id']))
        # An unfinished observation may precede target readiness.
        if target is None:
            continue
        if target['workflow_id'] != start['workflow_id']:
            raise ValueError('linked tool target changed workflow identity')
        dependencies = {d['request_id']: d['delay_ns'] for d in target['data'].get('dependencies', [])}
        if data['dependency_request_id'] not in dependencies:
            raise ValueError('linked tool is not a target dependency')
        if end is None or abs(end['ts_ns'] - finished - dependencies[data['dependency_request_id']]) > 1:
            raise ValueError('linked tool end differs from dependency delay')
        if end['ts_ns'] > target['ts_ns'] + 1:
            raise ValueError('target ready before linked tool end')
    return {'events': len(events), 'runs': len(last), 'idle_episodes': len(starts)}


def derive_idle_labels(events):
    validate_events(events)
    pending = {}
    labels = []
    for e in events:
        kind = e['event_type']
        episode = (e['run_id'], e.get('idle_episode_id'))
        if kind == 'idle_start':
            pending[episode] = e
        elif kind in {'legal_demand', 'context_retire'}:
            start = pending.pop(episode)
            labels.append(_label(start, e, 'retire' if kind == 'context_retire' else kind))
        elif kind == 'observation_end':
            for key, start in list(pending.items()):
                if key[0] == e['run_id']:
                    labels.append(_label(start, e, 'right_censored'))
                    del pending[key]
    return labels


def _label(start, end, outcome):
    return {'run_id': start['run_id'], 'idle_episode_id': start['idle_episode_id'],
            'evidence_kind': start['evidence_kind'], 'start_ns': start['ts_ns'],
            'end_ns': end['ts_ns'], 'duration_ns': end['ts_ns']-start['ts_ns'],
            'outcome': outcome, 'reason': end['data'].get('reason'),
            'demand_request_id': end.get('request_id') if outcome == 'legal_demand' else None}
