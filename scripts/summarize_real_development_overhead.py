"""Audit the counterbalanced two-device-pair crossover and logging microcheck."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from leasepath.real_audit import load_records, audit_requests, audit_blocks
from leasepath.real_observer import Observer, validate_stream


def quantile(values, q):
    values = sorted(values)
    position = (len(values) - 1) * q
    lower = int(position)
    return values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (position - lower)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runs = [args.root / ('stage0-20261101-overhead-' + suffix)
            for suffix in ['off', 'on', 'on-cross', 'off-cross']]
    manifests = [json.loads((r / 'service-manifest.json').read_text()) for r in runs]
    if len({json.dumps(m['probe_sources_sha256'], sort_keys=True) for m in manifests}) != 1:
        raise ValueError('probe source differs between crossover configurations')
    if len({json.dumps(m['runtime_versions'], sort_keys=True) for m in manifests}) != 1:
        raise ValueError('runtime version differs between crossover configurations')
    if any('--num-gpu-blocks-override' not in m['command'] or
           m['command'][m['command'].index('--num-gpu-blocks-override') + 1] != '192' for m in manifests):
        raise ValueError('unexpected capacity')
    paired, logging = {}, []
    sources = {}
    for run, manifest in zip(runs, manifests):
        path = run / 'overhead-workload.json'
        workload = json.loads(path.read_text())
        mode = manifest['probe_mode']
        raw = list((run / 'raw').glob('*.jsonl'))
        if mode == 'off' and raw:
            raise ValueError('disabled service unexpectedly installed an observer')
        if mode == 'on':
            for f in raw:
                validate_stream(f)
            records = load_records(run / 'raw')
            audit_requests(records)
            audit_blocks(records)
            logging.extend(r for r in records if r['kind'] not in {'observer_start', 'observer_end'})
        sources[run.name] = {'mode': mode, 'devices': manifest['devices'],
                            'workload_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                            'raw_log_bytes': sum(f.stat().st_size for f in raw)}
        for row in workload['records']:
            if row['phase'] == 'warmup_excluded':
                continue
            key = (tuple(manifest['devices']), row['size'], row['phase'], row['repeat'])
            paired.setdefault(key, {})[mode] = row
    rows = []
    for key, pair in paired.items():
        if set(pair) != {'on', 'off'}:
            raise ValueError('incomplete crossover pair')
        a, b = pair['on'], pair['off']
        if a['input_sha256'] != b['input_sha256'] or a['output_sha256'] != b['output_sha256'] or a['usage'] != b['usage']:
            raise ValueError('paired workload or actual output differs')
        rows.append({'devices': list(key[0]), 'size': key[1], 'phase': key[2], 'repeat': key[3],
                     'on_s': a['elapsed_s'], 'off_s': b['elapsed_s'],
                     'increase_fraction': a['elapsed_s'] / b['elapsed_s'] - 1})
    args.output.mkdir(parents=True, exist_ok=False)
    emit_times, fsync_times = [], []
    bench = args.output / 'serialization-disk'
    for repeat in range(5):
        observer = Observer(bench / f'{repeat}.jsonl', run_id='logging-microcheck', source='microcheck')
        for record in logging:
            start = time.perf_counter_ns()
            observer.emit(record['kind'], **record['data'])
            emit_times.append(time.perf_counter_ns() - start)
        start = time.perf_counter_ns()
        observer.finish()
        fsync_times.append(time.perf_counter_ns() - start)
    ratios = [r['increase_fraction'] for r in rows]
    p95_ratio = quantile([r['on_s'] for r in rows], .95) / quantile([r['off_s'] for r in rows], .95) - 1
    summary = {'scope': 'controlled_steady_state_inference_not_Agent_JCT',
               'order': {'0,1': ['off', 'on'], '4,5': ['on', 'off']},
               'paired_requests': len(rows), 'warmups_excluded': 4,
               'paired_median_increase_fraction': statistics.median(ratios),
               'paired_p95_increase_fraction': quantile(ratios, .95),
               'p95_service_time_increase_fraction': p95_ratio,
               'proposed_median_2percent_met_descriptively': statistics.median(ratios) <= .02,
               'proposed_p95_5percent_met_descriptively': p95_ratio <= .05,
               'Agent_JCT_noninferiority': 'not_measured_not_accepted',
               'by_devices': {str(list(devices)): statistics.median([r['increase_fraction'] for r in rows if r['devices'] == list(devices)])
                              for devices in [(0, 1), (4, 5)]},
               'logging_microcheck': {'events': len(emit_times), 'emit_median_us': statistics.median(emit_times) / 1000,
                   'emit_p95_us': quantile(emit_times, .95) / 1000, 'finish_fsync_median_us': statistics.median(fsync_times) / 1000,
                   'event_mix': dict(Counter(r['kind'] for r in logging)),
                   'scope': 'decoded_payload_JSON_serialization_and_os_write; excludes_snapshot_computation'},
               'sources': sources, 'pairs': rows,
               'limitations': ['Only two device-pair lanes with opposite fixed orders; short workload, temporal drift and host competition remain.',
                              'Request repeats are not independent tasks or days; no task-level confidence claim.',
                              'Warmups/startup are excluded; output hashes and token counts exactly paired.',
                              'No threshold tuning or predictor inference measured.']}
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({k: v for k, v in summary.items() if k not in {'pairs', 'sources', 'logging_microcheck'}}))


if __name__ == '__main__':
    main()
