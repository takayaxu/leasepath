"""Read-only audit of a pinned, sanitized CacheWise release.

No upstream Python is imported or executed. Tool response log spans are proxy
targets, never legal KV demand, retire or a guaranteed observation-end label.
"""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

import numpy as np

REPOSITORY = 'https://github.com/cachewise-project/cachewise-coding-traces'
COMMIT = '181c435a090d328d00bbbee4c8eeb27d32f3abd2'
ARCHIVE_SHA256 = '2ef65261adca87db8d456439d0672be42276a6939c4118dc851783cdbe4c0d0a'


def read_json(path):
    return json.loads(Path(path).read_text())


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return result


def distribution(values):
    if not values:
        return {'count': 0}
    return {
        'count': len(values), 'min': float(min(values)),
        'p50': float(np.percentile(values, 50)),
        'p90': float(np.percentile(values, 90)),
        'p95': float(np.percentile(values, 95)),
        'p99': float(np.percentile(values, 99)), 'max': float(max(values)),
    }


def verify_snapshot(directory):
    """Verify every supplied file, and reject extra files in immutable upstream."""
    directory = Path(directory)
    provenance = read_json(directory / 'provenance.json')
    if provenance['commit'] != COMMIT or provenance['archive_sha256'] != ARCHIVE_SHA256:
        raise ValueError('unexpected CacheWise revision or archive hash')
    upstream = directory / 'upstream'
    actual = {str(p.relative_to(upstream)) for p in upstream.rglob('*') if p.is_file()}
    expected = provenance['files_sha256']
    if actual != set(expected):
        raise ValueError('upstream file inventory changed')
    for name, digest in expected.items():
        if hashlib.sha256((upstream / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'upstream file changed: {name}')
    return provenance


def audit_session(project, session, events, raw):
    llm = [e for e in events if e.get('event_type') == 'llm_call']
    tools = [(i, e) for i, e in enumerate(events) if e.get('event_type') == 'tool_call']
    tool_ids = Counter(e['tool_use_id'] for _, e in tools)
    request_ids = {e.get('request_id') for e in llm if e.get('request_id')}
    message_ids = {e['message_id'] for e in llm}
    raw_uses, raw_results = defaultdict(list), defaultdict(list)
    for line, record in enumerate(raw, 1):
        content = record.get('message', {}).get('content')
        if not isinstance(content, list):
            continue
        for item in content:
            if item.get('type') == 'tool_use':
                raw_uses[item['id']].append((line, record, item))
            if item.get('type') == 'tool_result':
                raw_results[item['tool_use_id']].append((line, record, item))
    features, labels, quality = [], [], []
    durations, positive, candidates = [], [], []
    model_names = {e.get('model') for e in llm}
    counts = Counter({
        'sessions': 1, 'empty_sessions': int(not events), 'raw_rows': len(raw),
        'events': len(events), 'llm_event_rows': len(llm), 'tool_event_rows': len(tools),
        'distinct_message_ids': len(message_ids), 'distinct_nonnull_request_ids': len(request_ids),
        'repeated_llm_event_rows': len(llm) - len(message_ids),
        'duplicate_tool_id_groups': sum(n > 1 for n in tool_ids.values()),
        'duplicate_tool_id_excess_rows': sum(n - 1 for n in tool_ids.values()),
        'raw_sidechain_rows': sum(bool(r.get('isSidechain')) for r in raw),
        'raw_retry_metadata_rows': sum('retryAttempt' in r for r in raw),
        'raw_compaction_rows': sum('compactMetadata' in r for r in raw),
        'raw_duplicate_result_id_groups': sum(len(rows) > 1 for rows in raw_results.values()),
        'synthetic_model_llm_rows': sum(e.get('model') == '<synthetic>' for e in llm),
    })
    for event_index, event in tools:
        identifier = f'{project}/{session}/event-{event_index}'
        use_id = event['tool_use_id']
        flags = []
        uses, results = raw_uses[use_id], raw_results[use_id]
        duration_ms = event.get('execution_time_ms')
        if event.get('tool_input') == {'content': '<redacted>'}:
            counts['redacted_tool_inputs'] += 1
        if event.get('tool_input', {}).get('command'):
            counts['available_commands'] += 1
        if tool_ids[use_id] != 1:
            flags.append('duplicate_tool_id')
        if len(uses) != 1:
            flags.append('raw_tool_use_not_unique')
        if len(results) != 1:
            flags.append('raw_tool_result_not_unique')
        if not event.get('has_result') or not event.get('result_timestamp'):
            flags.append('missing_result')
        if event['tool_name'] == 'HumanPause':
            flags.append('inferred_human_pause')
        if duration_ms is None:
            counts['missing_duration_rows'] += 1
            flags.append('missing_duration')
        elif not math.isfinite(duration_ms) or duration_ms < 0:
            counts['invalid_duration_rows'] += 1
            flags.append('invalid_duration')
        else:
            duration_s = duration_ms / 1000
            durations.append(duration_s)
            if duration_ms == 0:
                counts['zero_duration_rows'] += 1
                flags.append('zero_duration_resolution_or_copy')
            else:
                positive.append(duration_s)
            if event.get('result_timestamp'):
                observed = (timestamp(event['result_timestamp']) -
                            timestamp(event['timestamp'])).total_seconds() * 1000
                if not math.isclose(observed, duration_ms, rel_tol=0, abs_tol=1e-6):
                    counts['duration_timestamp_mismatch_rows'] += 1
                    flags.append('duration_timestamp_mismatch')
        if len(uses) == 1:
            _, record, _ = uses[0]
            if record.get('isSidechain'):
                flags.append('sidechain')
            if record.get('message', {}).get('model') == '<synthetic>':
                flags.append('synthetic_model')
            if timestamp(record['timestamp']) != timestamp(event['timestamp']):
                flags.append('raw_start_mismatch')
        if len(results) == 1 and event.get('result_timestamp'):
            _, record, _ = results[0]
            if timestamp(record['timestamp']) != timestamp(event['result_timestamp']):
                flags.append('raw_result_mismatch')
        eligible = not flags
        if eligible:
            candidates.append(duration_ms / 1000)
        counts['candidate_tool_span_rows'] += eligible
        counts['excluded_tool_span_rows'] += not eligible
        features.append({
            'sample_id': identifier, 'project_id': project, 'session_id': session,
            'source_event_index': event_index, 'tool_use_id': use_id,
            'start_log_timestamp': event['timestamp'],
            'endpoint': 'tool_result_log_span_proxy',
            'features': {'tool_name': event['tool_name'], 'elapsed_s': 0.0},
        })
        labels.append({
            'sample_id': identifier, 'endpoint': 'tool_result_log_span_proxy',
            'source_execution_time_ms': duration_ms,
            'observed_log_span_s': duration_ms / 1000 if duration_ms is not None else None,
            'result_log_timestamp': event.get('result_timestamp'),
            'has_result': event.get('has_result'), 'is_error': event.get('is_error'),
            'exact_kv_outcome': None, 'exact_kv_label_supported': False,
        })
        quality.append({
            'sample_id': identifier, 'candidate_for_tool_span_proxy': eligible,
            'flags': flags,
        })
    session_row = {
        'project_id': project, 'session_id': session, **dict(counts),
        'models': sorted(x for x in model_names if x is not None),
        'first_log_timestamp': min((e['timestamp'] for e in events), default=None),
        'last_log_timestamp': max((e['timestamp'] for e in events), default=None),
    }
    return {
        'session': session_row, 'counts': counts, 'features': features,
        'labels': labels, 'quality': quality, 'durations': durations,
        'positive_durations': positive, 'candidate_durations': candidates,
        'tools': Counter(e['tool_name'] for _, e in tools),
    }


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def write_jsonl(path, rows):
    Path(path).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))


def audit_dataset(source, output):
    source, output = Path(source), Path(output)
    provenance = verify_snapshot(source)
    upstream = source / 'upstream'
    output.mkdir(parents=True, exist_ok=True)
    total, tools = Counter(), Counter()
    features, labels, quality, sessions, durations, positive, candidates = [], [], [], [], [], [], []
    project_counts = defaultdict(Counter)
    paths = sorted((upstream / 'parsed_traces').glob('*/session_*/events.json'))
    for path in paths:
        project, session = path.parent.parent.name, path.parent.name
        raw_path = upstream / 'raw_traces' / project / f'{session}.jsonl'
        raw = [json.loads(line) for line in raw_path.read_text().splitlines() if line.strip()]
        result = audit_session(project, session, read_json(path), raw)
        total.update(result['counts'])
        tools.update(result['tools'])
        project_counts[project].update(result['counts'])
        features.extend(result['features'])
        labels.extend(result['labels'])
        quality.extend(result['quality'])
        sessions.append(result['session'])
        durations.extend(result['durations'])
        positive.extend(result['positive_durations'])
        candidates.extend(result['candidate_durations'])
    manifest = read_json(upstream / 'parsed_traces/manifest.json')
    if (manifest['num_sessions'], manifest['total_llm_calls'], manifest['total_tool_calls']) != (
            len(sessions), total['llm_event_rows'], total['tool_event_rows']):
        raise ValueError('upstream manifest count mismatch')
    if len(list((upstream / 'raw_traces').glob('*/*.jsonl'))) != len(paths):
        raise ValueError('raw/parsed session count mismatch')
    augmented = [
        r for p in sorted((upstream / 'tool_duration_prediction/datasets/events_with_pauses').glob(
            '*/session_*/events.json')) for r in read_json(p)
    ]
    derived = read_json(upstream / 'tool_duration_prediction/datasets/all_tool_calls.json')
    train = set(read_json(upstream / 'tool_duration_prediction/datasets/splits/train_sessions.json')[
        'train_sessions'])
    train_projects = {r['project_id'] for r in sessions if r['session_id'] in train}
    complement_projects = {r['project_id'] for r in sessions if r['session_id'] not in train}
    license_files = [str(p.relative_to(upstream)) for p in upstream.rglob('*')
                     if p.is_file() and p.name.lower().startswith(('license', 'copying', 'copyright'))]
    write_jsonl(output / 'features.jsonl', features)
    write_jsonl(output / 'labels.jsonl', labels)
    write_jsonl(output / 'quality.jsonl', quality)
    write_jsonl(output / 'sessions.jsonl', sessions)
    write_json(output / 'projects.json', dict(sorted(project_counts.items())))
    flag_counts = Counter(flag for row in quality for flag in row['flags'])
    summary = {
        'repository': REPOSITORY, 'commit': provenance['commit'],
        'source_archive_sha256': provenance['archive_sha256'],
        'snapshot_files': provenance['files'], 'snapshot_bytes': provenance['bytes'],
        'counts': dict(total), 'project_count': len(project_counts),
        'nonempty_sessions': sum(r['events'] > 0 for r in sessions),
        'tool_names': dict(tools), 'quality_flags': dict(flag_counts),
        'tool_log_span_s': distribution(durations),
        'positive_tool_log_span_s': distribution(positive),
        'candidate_tool_log_span_s': distribution(candidates),
        'source_span_ge_60s': sum(v >= 60 for v in durations),
        'source_span_ge_3600s': sum(v >= 3600 for v in durations),
        'derived_human_pause_events': sum(r.get('tool_name') == 'HumanPause' for r in augmented),
        'derived_all_tool_rows': len(derived),
        'derived_session_progress_rows': sum('session_progress' in r for r in derived),
        'upstream_train_session_count': len(train),
        'upstream_train_vs_complement_project_overlap': sorted(train_projects & complement_projects),
        'license_files': license_files, 'license_status': 'not_found' if not license_files else 'requires_review',
        'exact_kv_labels_supported': False, 'performance_claim_allowed': False,
        'target': 'tool_result_log_span_proxy',
        'supported_uses': [
            'Tool-family log-span distributions and elapsed-conditioned proxy prediction.',
            'Reference baseline code audit; inputs are redacted so parameter TF-IDF is unavailable.',
        ],
        'unsupported_uses': [
            'Exact compatible-prefix next cache lookup U/D/C labels.',
            'Natural cross-session arrival/load inference from shifted/sanitized timestamps.',
            'Server queue, true tool execution instrumentation, KV pressure or native JCT gain.',
            'Full-parameter CacheWise reproduction from redacted commands.',
        ],
        'selection_policy': {
            'preserve_all_source_rows': True,
            'candidate_definition': 'unique raw/parsed tool use and result; matching endpoints; positive span; not sidechain or synthetic',
            'duration_upper_cutoff': None,
            'zero_durations': 'retained in source audit, excluded from positive-span proxy candidates; not imputed',
            'missing_results': 'unknown; not converted into exact D or C',
            'split_status': 'No new train/test split frozen or model fitted; project grouping exported.',
            'time_note': 'Published 2020 log dates are not original calendar proof; within-session deltas are proxies.',
        },
        'input_sha256': {
            'provenance.json': hashlib.sha256((source / 'provenance.json').read_bytes()).hexdigest(),
            'audit_module': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        'output_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(output.iterdir())
                          if p.name in {'features.jsonl', 'labels.jsonl', 'quality.jsonl', 'sessions.jsonl', 'projects.json'}},
    }
    write_json(output / 'summary.json', summary)
    return summary
