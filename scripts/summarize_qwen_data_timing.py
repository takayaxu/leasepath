"""Summarize measured, nested timings without treating inter-request gaps as savings."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics


def read_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    controller = read_rows(args.run / 'raw/controller.jsonl')
    backend = read_rows(args.run / 'raw/backend.jsonl')
    overhead_path = args.run / 'overhead.json'
    overhead = json.loads(overhead_path.read_text()) if overhead_path.exists() else {}
    measurements = defaultdict(list)

    def add(name, value):
        if value is not None:
            if value < 0:
                raise ValueError('negative duration: ' + name)
            measurements[name].append(value / 1e9)

    requests = [r['data'] for r in backend if r['kind'] == 'engine_request']
    gaps = []
    for record in controller:
        d = record['data']
        if record['kind'] == 'request_input':
            add('model_input_source_check', d.get('input_source_check_ns'))
            add('model_preprocessing_and_reservation', d.get('preprocess_ns'))
        if record['kind'] != 'tool_end':
            continue
        call = d['call']
        add('tool_call_wall', call['ended_ns'] - call['started_ns'])
        for name in ('input_check_ns', 'post_input_check_ns', 'child_wall_ns',
                     'worker_execute_ns', 'call_evidence_write_ns'):
            add('tool_' + name.removesuffix('_ns'), call.get(name))
        add('protected_sources_before_tool', d.get('protected_pre_ns'))
        add('protected_sources_after_tool', d.get('protected_post_ns'))
        if call.get('worker_execute_ns', 0) > call.get('child_wall_ns', 0):
            raise ValueError('worker duration exceeds child wall')
        following = [r for r in requests if r['owner'] == d['owner'] and
                     r['lookup_ns'] > record['ts_ns']]
        gaps.append({'owner': d['owner'], 'tool_id': d['tool_id'],
                     'tool_name': d['tool_name'], 'tool_status': call['status'],
                     'tool_end_to_next_lookup_s': (
                         (following[0]['lookup_ns'] - record['ts_ns']) / 1e9
                         if following else None)})
        if following:
            add('tool_end_to_next_lookup', following[0]['lookup_ns'] - record['ts_ns'])
    for r in requests:
        add('engine_enqueue_to_dispatch', r['dispatch_ns'] - r['enqueue_ns'])
        add('engine_dispatch_to_lookup', r['lookup_ns'] - r['dispatch_ns'])
        add('engine_lookup_to_release', r['release_ns'] - r['lookup_ns'])
        add('engine_result_verification', r.get('verification_ns'))
    for row in overhead.get('observer_writes', []):
        add('observer_serialization_and_write', row['end_ns'] - row['start_ns'])
    add('observation_final_fsync_and_close', overhead.get('final_fsync_and_close_ns'))
    summaries = {}
    for name, values in sorted(measurements.items()):
        summaries[name] = {
            'count': len(values), 'min_s': min(values),
            'median_s': statistics.median(values),
            'p95_s': (statistics.quantiles(values, n=100, method='inclusive')[94]
                      if len(values) > 1 else values[0]),
            'max_s': max(values), 'sum_s': sum(values),
        }
    report = {
        'run': str(args.run.resolve()), 'clock': 'same-host CLOCK_MONOTONIC',
        'metrics': summaries, 'tool_gaps': gaps,
        'observer_overhead_recorded': bool(overhead),
        'missing_measurements': 'Absent fields are omitted, never imputed as zero.',
        'interpretation': [
            'Durations are nested; sums across categories would double-count.',
            'worker_execute is inside child_wall, which is inside tool_call_wall.',
            'Input hashes and artifact persistence are inside tool_call_wall.',
            'Protected source checks bracket tool calls and are reported separately.',
            'Model preprocessing includes the durable generation reservation.',
            'Observer writes include serialization and os.write, not per-row fsync.',
            'Tool-end to lookup includes intervening checks, preprocessing and writes; it is not a cache benefit.',
            'Final observation fsync occurs only after producers drained.',
        ],
        'workspace_hash_1': 'not_applicable', 'workspace_hash_2': 'not_applicable',
    }
    with args.output.open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps(summaries, indent=2))


if __name__ == '__main__':
    main()
