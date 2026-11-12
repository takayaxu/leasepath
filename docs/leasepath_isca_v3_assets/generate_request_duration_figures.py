"""Plot measured tool and request-duration CDFs from verified trace exports.

No model execution or fitting. Quantiles use NumPy's linear interpolation;
the plotted curves are unsmoothed empirical distributions.
"""
from collections import defaultdict
from pathlib import Path
import csv
import hashlib
import json
import math

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
ASSET = Path(__file__).resolve().parent
DATA = ROOT / 'results/analysis/agent_workload_v1_2026-11-11'


def read_csv(name):
    path = DATA / name
    expected = json.loads((ASSET / 'manifest.json').read_text())['sha256']
    key = str(path.relative_to(ROOT))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected[key], key
    with path.open() as stream:
        return list(csv.DictReader(stream))


def describe(values):
    values = np.asarray(values, dtype=float)
    assert len(values) and np.isfinite(values).all() and (values > 0).all()
    return dict(n=len(values), p50_s=float(np.quantile(values, .5)),
                p95_s=float(np.quantile(values, .95)),
                p99_s=float(np.quantile(values, .99)),
                min_s=float(min(values)), max_s=float(max(values)))


def display_time(seconds):
    return f'{seconds * 1000:.1f} ms' if seconds < 1 else f'{seconds:.1f} s'


def draw_cdf(ax, values, heading, xlabel, color):
    values = np.sort(np.asarray(values, dtype=float))
    quantiles = describe(values)
    # Explicit endpoints show the zero baseline and the full observed tail.
    left = 10 ** (math.floor(math.log10(values[0])) - .05)
    right = 10 ** (math.ceil(math.log10(values[-1])) + .05)
    ax.step(np.r_[left, values, right],
            np.r_[0, np.arange(1, len(values) + 1) / len(values), 1],
            where='post', linewidth=1.9, color=color)
    for probability, key, text_position in [
        (.5, 'p50_s', (.06, .30)), (.99, 'p99_s', (.43, .77))
    ]:
        value = quantiles[key]
        ax.scatter([value], [probability], s=15, color=color, zorder=3)
        ax.annotate(f'P{int(probability * 100)}: {display_time(value)}',
                    xy=(value, probability), xycoords='data',
                    xytext=text_position, textcoords='axes fraction',
                    arrowprops=dict(arrowstyle='->', color='#555555', lw=.8),
                    fontsize=9,
                    bbox=dict(facecolor='white', edgecolor='none', alpha=.85, pad=1.5))
    ax.set_xscale('log')
    ax.set_xlim(left, right)
    ax.set_ylim(0, 1.045)
    ax.set_yticks(np.arange(0, 1.01, .2))
    ax.set_xlabel(xlabel)
    ax.set_ylabel('CDF')
    ax.set_title(heading, fontsize=10, pad=8)
    ax.grid(alpha=.3)
    ax.spines[['top', 'right']].set_visible(False)


def main():
    requests = read_csv('requests.csv')
    tools = read_csv('tools.csv')
    idle = {r['next_request_id']: r for r in read_csv('intervals.csv')}
    assert len(requests) == 244 and len(tools) == 196 and len(idle) == 238
    by_owner = defaultdict(list)
    for row in requests:
        by_owner[row['owner']].append(row)
    assert len(by_owner) == 6
    records = []
    gaps = []
    for owner, rows in sorted(by_owner.items()):
        rows.sort(key=lambda r: int(r['request_ordinal']))
        assert [int(r['request_ordinal']) for r in rows] == list(range(1, len(rows) + 1))
        previous = None
        for row in rows:
            lookup, release = float(row['lookup_s']), float(row['release_s'])
            service = release - lookup
            ready_delay = float(row['ready_to_lookup_s'])
            assert service > 0 and ready_delay >= 0
            gap = lookup - float(previous['lookup_s']) if previous else None
            if previous:
                old = idle[row['request_id']]
                assert old['origin_request_id'] == previous['request_id']
                previous_service = float(previous['release_s']) - float(previous['lookup_s'])
                assert abs(gap - previous_service - float(old['release_to_lookup_s'])) < 1e-6
                gaps.append(gap)
            records.append(dict(owner=owner, request_id=row['request_id'],
                                request_ordinal=int(row['request_ordinal']),
                                lookup_to_lookup_s=gap,
                                native_service_s=service,
                                ready_to_release_s=ready_delay + service))
            previous = row
    assert len(gaps) == 238
    tool_values = [float(row['duration_s']) for row in tools]
    response_values = [r['ready_to_release_s'] for r in records]
    stats = dict(
        scope='six authored development projects, one accepted collection; correlated observations',
        quantile_method='linear interpolation; CDF is empirical and unsmoothed',
        tool_execution=describe(tool_values),
        same_owner_lookup_interval=describe(gaps),
        model_request_latency=describe(response_values),
        native_service=describe([r['native_service_s'] for r in records]),
        per_tool={name: describe([float(r['duration_s']) for r in tools if r['tool'] == name])
                  for name in sorted({r['tool'] for r in tools})},
        definitions=dict(
            tool_execution='recorded tool end minus start',
            same_owner_lookup_interval='next native lookup start minus previous native lookup start, same owner',
            model_request_latency='actual recorded request readiness to native release; includes waiting, preparation and model service',
            native_service='native lookup to release, excluding prior queue delay'),
        source_sha256={str((DATA / name).relative_to(ROOT)):
                       hashlib.sha256((DATA / name).read_bytes()).hexdigest()
                       for name in ['requests.csv', 'tools.csv', 'intervals.csv']},
        new_model_generations=0,
    )
    (DATA / 'request_duration_summary.json').write_text(json.dumps(stats, indent=2) + '\n')
    with (DATA / 'request_durations.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    plt.rcParams.update({'font.size': 9, 'pdf.fonttype': 42, 'ps.fonttype': 42})
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.05), layout='constrained')
    draw_cdf(axes[0], tool_values, '(a) Tool execution (n=196)',
             'Measured tool-call duration (s)', '#406c9b')
    draw_cdf(axes[1], gaps, '(b) Same-session request intervals (n=238)',
             'Time between LLM lookups (s)', '#406c9b')
    draw_cdf(axes[2], response_values, '(c) Model-request latency (n=244)',
             'Readiness to native release (s)', '#406c9b')
    for suffix in ['pdf', 'png']:
        fig.savefig(ASSET / 'figures' / f'workload_request_durations.{suffix}', dpi=190)
    plt.close(fig)
    tool, gap, latency = (stats[k] for k in ['tool_execution', 'same_owner_lookup_interval', 'model_request_latency'])
    fragment = r'''
\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth]{leasepath_isca_v3_assets/figures/workload_request_durations.pdf}
\caption{Tool execution is brief, while request intervals and latency span much longer timescales; markers show empirical P50/P99.}
\Description{Three logarithmic CDFs separately show actual tool execution,
same-session lookup-to-lookup intervals, and request readiness-to-release
latency, with median and 99th-percentile annotations.}
\label{fig:request-durations}
\end{figure*}

Figure~\ref{fig:request-durations} summarizes tool and request timing
with logarithmic empirical CDFs. Measured tool execution has P50
TOOL50 ms and P99 TOOL99 ms (196 calls). For each project,
the interval between successive native lookup starts has P50
GAP50 seconds and P99 GAP99 seconds (238 pairs). This interval
includes the preceding model execution and the subsequent tool/user,
controller, and queue delays; it differs from the release-to-lookup
idle interval discussed below.

Per-model-request latency is measured from recorded readiness to native
release: P50 LAT50 seconds and P99 LAT99 seconds (244 requests).
It includes notification handling, FCFS waiting, preparation, and model
service, rather than only GPU execution. It also differs from the
validated user-turn response, which can span multiple model requests
and tools. These descriptive percentiles concern six correlated
development histories, not independent tail-latency estimates.
'''
    for key, value in {'TOOL50': f"{tool['p50_s'] * 1000:.1f}",
                       'TOOL99': f"{tool['p99_s'] * 1000:.1f}",
                       'GAP50': f"{gap['p50_s']:.3f}",
                       'GAP99': f"{gap['p99_s']:.3f}",
                       'LAT50': f"{latency['p50_s']:.3f}",
                       'LAT99': f"{latency['p99_s']:.3f}"}.items():
        fragment = fragment.replace(key, value)
    (ASSET / 'generated/request_duration_section.tex').write_text(fragment)
    print(json.dumps({k: stats[k] for k in ['tool_execution', 'same_owner_lookup_interval', 'model_request_latency']}, indent=2))


if __name__ == '__main__':
    main()
