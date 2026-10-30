"""Plot exported D1 contract evidence; all times are synthetic semantic ns."""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
from matplotlib import font_manager
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = {'legal_demand': 'U', 'retire': 'D', 'right_censored': 'C'}
COLORS = {'U': '#2171b5', 'D': '#d95f0e', 'C': '#737373'}


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, default=ROOT / 'results/prediction/d1_contract')
    args = parser.parse_args()
    source = args.input
    output = source / 'figures'
    output.mkdir(parents=True, exist_ok=True)
    summary = json.loads((source / 'summary.json').read_text())
    for name, expected in summary['output_sha256'].items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'acceptance artifact changed: {name}')
    # Select an installed Chinese font without requiring a network download.
    for family in ('WenQuanYi Zen Hei', 'Noto Sans CJK SC', 'SimHei'):
        if any(f.name == family for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.family'] = family
            break
    plt.rcParams.update({'axes.unicode_minus': False, 'font.size': 12})
    cases = [
        ('legal_demand_physical_miss', '合法需求 · 缓存未命中'),
        ('legal_demand_physical_hit', '合法需求 · 缓存命中'),
        ('tool_end_then_explicit_retire', '工具已结束 · 明确放弃'),
        ('tool_end_without_retire', '工具已结束 · 观察截断'),
        ('observation_ends_request_queued', '请求已入队 · 观察截断'),
    ]
    fig, ax = plt.subplots(figsize=(13, 6.5))
    for y, (name, label) in enumerate(cases):
        events = read_jsonl(source / f'{name}.events.jsonl')
        result = read_jsonl(source / f'{name}.labels.jsonl')[0]
        code = OUTCOMES[result['outcome']]
        left, right = result['start_ns'], result['end_ns']
        ax.plot([left, right], [y, y], color=COLORS[code], lw=7, solid_capstyle='round')
        ax.scatter(left, y, marker='o', color='black', s=45, zorder=4)
        for event in events:
            if event['event_id'] in {'tool-end', 'enqueue'}:
                ax.scatter(event['ts_ns'], y, marker='|' if event['event_id'] == 'tool-end' else '^',
                           s=160 if event['event_id'] == 'tool-end' else 70,
                           color='#111111' if event['event_id'] == 'tool-end' else '#9c6500', zorder=5)
        ax.scatter(right, y, marker='>' if code == 'C' else 'D', color=COLORS[code], s=100, zorder=6)
        ax.text(right + .4, y, f'{code}：{right - left} ns', va='center', color=COLORS[code])
    ax.axvline(9, color='#555555', ls='--', lw=1.5)
    ax.text(9, -.55, '快照时刻 9 ns', ha='center', fontsize=11)
    ax.set_yticks(range(len(cases)), [name for _, name in cases])
    ax.invert_yaxis()
    ax.set_xlim(4, 24)
    ax.set_xticks([5, 9, 10, 12, 14, 16, 20])
    ax.set_xlabel('合成事件时间（ns，仅检验语义，不是实测工具耗时）')
    ax.set_title('D1 契约验收：终点是 lookup，命中与未命中的 U 标签相同', pad=32, fontsize=16)
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='x', alpha=.15)
    ax.legend(handles=[
        Line2D([], [], marker='o', color='none', markerfacecolor='black', label='进入 idle'),
        Line2D([], [], marker='|', color='black', linestyle='none', markersize=14, label='工具结束'),
        Line2D([], [], marker='^', color='none', markerfacecolor='#9c6500', label='请求入队'),
    ], loc='upper center', bbox_to_anchor=(.5, -.16), ncol=3, frameon=False)
    fig.text(.5, .025, 'C 只说明观测期间未看到 U/D，不代表以后永不复用。工具结束、入队均不自动产生 U 或 D。',
             ha='center', fontsize=11)
    fig.tight_layout(rect=[0, .07, 1, 1])
    for suffix in ('png', 'pdf'):
        kwargs = {'metadata': {'CreationDate': None, 'ModDate': None}} if suffix == 'pdf' else {}
        fig.savefig(output / f'timeline.{suffix}', dpi=170, bbox_inches='tight', **kwargs)
    plt.close(fig)

    futures = json.loads((source / 'future_invariance.json').read_text())
    names = {
        'future_U': '未来 U：lookup 在 14 ns',
        'future_D': '未来 D：retire 在 16 ns',
        'future_C': '未来 C：观察在 20 ns 结束',
        'future_tool_and_lookup_delayed': '未来工具/lookup 延后：U 在 19 ns',
        'future_observation_extended': '未来观察延长至 30 ns：U 在 14 ns',
    }
    rows = []
    for row in futures:
        f = row['snapshot']['features']
        rows.append([
            names[row['variant']], f"{f['elapsed_idle_s'] * 1e9:g} ns",
            f"{f['tool_elapsed_s'] * 1e9:g} ns", f['phase'], str(f['queue_length']),
            '未知' if f['retry_count'] is None else str(f['retry_count']),
        ])
    fig, ax = plt.subplots(figsize=(14, 4.8))
    ax.axis('off')
    table = ax.table(
        cellText=rows,
        colLabels=['只改写快照之后的事件', 'idle 已持续', '工具已运行', '当前阶段', '当前队列长度', '重试次数'],
        colWidths=[.37, .11, .11, .17, .13, .11],
        loc='center', cellLoc='center',
    )
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 2.3)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor('white')
        cell.set_facecolor('#e6f4ea' if r and c else '#eef0f4')
        if r == 0:
            cell.set_text_props(weight='bold')
    ax.set_title('改写未来，9 ns 时的模型输入保持完全相同', fontsize=17, pad=25)
    fig.text(.5, .07, '队列长度仅在声明“服务事件完整、初始队列为空”时可计算；缺少该声明或关联时保留未知。',
             ha='center', fontsize=11)
    fig.text(.5, .015, '图表由导出的 future_invariance.json 生成；仅验证快照隔离，不是在线回调隔离或预测准确率实验。',
             ha='center', fontsize=11)
    fig.tight_layout(rect=[0, .13, 1, 1])
    for suffix in ('png', 'pdf'):
        kwargs = {'metadata': {'CreationDate': None, 'ModDate': None}} if suffix == 'pdf' else {}
        fig.savefig(output / f'future_invariance.{suffix}', dpi=170, bbox_inches='tight', **kwargs)
    plt.close(fig)
    metadata = {
        'source_summary_sha256': hashlib.sha256((source / 'summary.json').read_bytes()).hexdigest(),
        'plot_script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'figures_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in sorted(output.glob('*')) if p.suffix in {'.png', '.pdf'}},
    }
    (output / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'已生成 2 张验收图的 PNG/PDF：{output}')


if __name__ == '__main__':
    main()
