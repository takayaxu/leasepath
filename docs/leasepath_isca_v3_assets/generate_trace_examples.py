"""Publish selected real development-trace records, with verified provenance.

One first occurrence of a specified tool per project is chosen deterministically.
No inference or fitting; raw traces and existing analysis exports stay unchanged.
"""
from pathlib import Path
import csv
import hashlib
import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch


ROOT = Path(__file__).resolve().parents[2]
ASSET = Path(__file__).resolve().parent
SOURCE = ROOT / 'results/agent/continuous_pilot_v7_2026-11-11'
RAW = SOURCE / 'raw'
DATA = ROOT / 'results/analysis/agent_workload_v1_2026-11-11'
DEST = ASSET / 'examples'
SELECTION = [
    ('continuous_inventory', 'read_file', 'Inventory maintenance'),
    ('continuous_billing', 'query_sql', 'CSV / SQLite billing'),
    ('continuous_logs', 'search_files', 'Log diagnostics'),
    ('continuous_api', 'http_request', 'HTTP integration'),
    ('continuous_experiment', 'run_experiment', 'Numerical experiment'),
    ('continuous_documents', 'run_checks', 'Release documents'),
]


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def main():
    DEST.mkdir(exist_ok=True)
    archive = json.loads((SOURCE / 'raw-sha256.json').read_text())['files']
    selected, hashes, all_calls = [], {}, []
    for owner, name, title in SELECTION:
        rel = f'tools/{owner}/calls.jsonl'
        path = RAW / rel
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        assert digest == archive[rel], path
        hashes[str(path.relative_to(ROOT))] = digest
        calls = [json.loads(line) for line in payload.splitlines()]
        all_calls.extend(calls)
        index, record = next((i, c) for i, c in enumerate(calls) if c['name'] == name)
        assert record['round'] == 0
        assert record['status'] == 'ok'
        assert record['ended_ns'] >= record['started_ns']
        selected.append(dict(project=owner, title=title, source_line=index+1,
            source_path=str(path.relative_to(ROOT)), native_record=record,
            display=dict(name=record['name'], arguments=record['arguments'],
                duration_ms=round((record['ended_ns']-record['started_ns'])/1e6, 3),
                status=record['status'])))
    assert len(all_calls) == 196
    assert len(set(c['name'] for c in all_calls)) == 9
    dump(DEST / 'selected_tool_records.json', selected)
    (DEST / 'tool_invocations_display.jsonl').write_text(''.join(
        json.dumps(s['display'],ensure_ascii=False)+'\n' for s in selected))

    # A complete user turn connects native tool records with model/KV measures.
    with (DATA / 'requests.csv').open() as stream:
        requests = [r for r in csv.DictReader(stream)
                    if r['owner'] == 'continuous_inventory' and r['round'] == '1']
    requests.sort(key=lambda r:int(r['request_ordinal']))
    for row in requests:
        for k in ['request_ordinal','round','input_tokens','output_tokens',
                  'compatible_prior_tokens','cached_tokens','prefill_tokens']:
            row[k] = int(row[k])
        for k in ['lookup_s','release_s','ready_to_lookup_s','ready_to_dispatch_s',
                  'compatible_fraction']:
            row[k] = float(row[k])
    tool_path = RAW / 'tools/continuous_inventory/calls.jsonl'
    calls = [json.loads(line) for line in tool_path.read_text().splitlines()
             if json.loads(line)['round'] == 0]
    terminal_path = RAW / 'rounds/continuous_inventory-00.json'
    assert hashlib.sha256(terminal_path.read_bytes()).hexdigest() == archive[
        'rounds/continuous_inventory-00.json']
    terminal = json.loads(terminal_path.read_text())
    snapshots = json.loads((SOURCE/'audit/prediction-snapshots.json').read_text())['rows']
    snapshot = next(s for s in snapshots if s['owner']=='continuous_inventory'
                    and s['episode_id'].endswith(':'+requests[0]['request_id']))
    assert snapshot['event'] == 'U'
    assert abs(snapshot['remaining_s']-(snapshot['terminal_s']-snapshot['snapshot_s'])) < 1e-8
    assert terminal['success'] and len(requests) == terminal['generations'] == 5
    assert len(calls) == terminal['tool_calls'] == 4
    chain = dict(project='continuous_inventory', user_turn_display=1,
        native_round=0, tool_sequence=[c['name'] for c in calls],
        requests=requests, native_tools=calls, native_snapshot=snapshot,
        validated_delivery=terminal,
        note='round in native records is zero-based; CSV round is one-based. '
             'The snapshot contains a causal feature dictionary and a future outcome label; '
             'terminal_s, event, and remaining_s are labels, not prediction inputs.')
    dump(DEST/'inventory_turn1_linked_example.json', chain)
    for path in [terminal_path, SOURCE/'audit/prediction-snapshots.json',
                 DATA/'requests.csv', DATA/'summary.json']:
        hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    dump(DEST/'source_manifest.json', dict(
        selection='First specified tool occurrence per project, user turn 1; '
                  'six different tool types out of nine. Not a random sample.',
        scope='Six authored development projects with real model/tool execution; '
              'not natural user conversations or independent holdout.',
        original_sha256=hashes, inference_calls=0, training_runs=0,
        duration_ms_definition='(ended_ns - started_ns) / 1e6, rounded to 3 decimals',
        display_omits=['call_id','round','started_ns','ended_ns','result','result_sha256'],
        original_records_preserved=True))

    plt.rcParams.update({'font.family':'DejaVu Sans','pdf.fonttype':42,'svg.fonttype':'none'})
    fig=plt.figure(figsize=(11.7,2.85),facecolor='white')
    ax=fig.add_axes((.005,.005,.99,.99))
    ax.set(xlim=(0,11.7),ylim=(0,2.85));ax.axis('off')
    # Three columns and two rows reduce height while keeping the same records.
    text_bounds=[]
    for i, item in enumerate(selected):
        x=.04+(i%3)*3.90
        top=2.77-(i//3)*1.39
        ax.text(x+.08,top,item['title'],fontsize=12.7,fontweight='bold',
                color='#172536',va='top')
        width=3.75
        card=FancyBboxPatch((x,top-1.16),width,.91,
            boxstyle='round,pad=0.025,rounding_size=.04',edgecolor='#D8E0E7',
            facecolor='#F7F9FB',linewidth=.8)
        ax.add_patch(card)
        display=item['display']
        lines=['{"name": '+json.dumps(display['name'])+',']
        if display['arguments']:
            assert len(display['arguments'])==1
            key,value=next(iter(display['arguments'].items()))
            lines+=[' "arguments": {'+json.dumps(key)+':',
                    ' '+json.dumps(value,ensure_ascii=False)+'},']
        else:
            lines+=[' "arguments": {},']
        lines += [f' "duration_ms": {display["duration_ms"]:.3f},',
                  ' "status": "ok"}']
        assert json.loads('\n'.join(lines))==display
        for j,line in enumerate(lines):
            artist=ax.text(x+.08,top-.30-j*.168,line,fontsize=12,
                    fontfamily='DejaVu Sans Mono',color='#167C85',va='top')
            text_bounds.append((artist,card))
    fig.canvas.draw()
    for artist,card in text_bounds:
        box=artist.get_window_extent()
        bounds=card.get_window_extent()
        assert bounds.x0<=box.x0 and box.x1<=bounds.x1, artist.get_text()
        assert bounds.y0<=box.y0 and box.y1<=bounds.y1, artist.get_text()
    output=ASSET/'figures/workload_trace_examples'
    for ext,kwargs in [('pdf',{}),('svg',{}),('png',{'dpi':220})]:
        fig.savefig(output.with_suffix('.'+ext),facecolor='white',**kwargs)
    plt.close(fig)

    guide = '''# LeasePath 当前持续 Agent 数据：结构与真实示例

本说明对应论文采用的 `continuous_pilot_v7_2026-11-11` 合格开发采集。包含6个项目、每项目8个用户轮次，共48轮、244次模型请求、196次工具调用、9种工具、1079个预测快照。不是旧50题数据，也不是自然用户日志：需求、初始材料与用户返回计划由实验构造，模型和工具真实执行。快照属于234个观察到的episode，不能当成1079个独立任务；238个完整续接间隔又是另一计数单位。

## 数据以什么形式保存

| 层次 | 实际文件 | 核心字段与用途 |
|---|---|---|
| 会话与模型交互事件 | `raw/raw/controller.jsonl` | 每行一个事件，外层`kind / ts_ns / data`；记录用户到达、request_input、工具提交及反馈、交付与上下文退休。完整request_input保存prompt、token_ids和prompt_sha256；compact导出省略prompt和token_ids。 |
| 原生模型与KV请求 | `raw/raw/backend.jsonl` | engine_request保存owner、request_id、tokens、output_tokens、lookup_ns、release_ns、hit、prefill及prefill_ns/decode_ns，另有策略审计。可核对输入token=hit+prefill。 |
| 工具调用 | `raw/tools/<owner>/calls.jsonl` | 每行一次真实调用：call_id、name、arguments、round、started_ns、ended_ns、status、result、result_sha256。不仅有工具名和耗时，还保留参数与真实反馈。 |
| 预测训练快照 | `audit/prediction-snapshots.json` | episode_id / snapshot_id连接样本；features含工具名、等待阶段、已等时间、prefix_tokens、queue_length等；event / remaining_s是事后标签。 |
| 质量与轮次结果 | `raw/rounds/<owner>-<round>.json` | success、acceptance、final、generations、tool_calls、response_s及产物哈希，可核对每轮是否交付。 |
| 分析导出 | `requests.csv / tools.csv / intervals.csv / rounds.csv / projects.csv` | 从原始记录导出逐请求、逐工具、逐间隔、逐轮与逐项目统计，便于绘图。 |

上表除分析导出外均相对于该次采集目录。原始JSONL是事件日志；不是把每个工具调用当成一道题。工具文件按owner分组，通过owner、原始round和时间顺序与controller事件对齐，不能假设所有事件都有call_id。模型请求用owner/request_id/prompt_sha256等字段连接；snapshot用episode_id关联其起始请求。

## 六条可直接展示的真实工具记录

以下固定选择每个项目中指定工具的第一次调用，均来自第1轮，覆盖9种工具中的6种；未按时长排序或挑选长工具。

![真实工具调用示例](leasepath_isca_v3_assets/figures/workload_trace_examples.png)

展示保留原始name、arguments与status，新增duration_ms=(ended_ns-started_ns)/1e6，四舍五入到0.001毫秒。结果、ID、轮次、原始纳秒时间戳和结果哈希为节省版面省略；完整原记录见[选定记录JSON](leasepath_isca_v3_assets/examples/selected_tool_records.json)。这里duration_ms只计算实际工具执行，不含模型排队、控制器消费延迟或用户等待；它不等于预测目标“下一次兼容KV需求的剩余时间”。

## 一轮任务如何连起来

真实库存项目第一轮要求修复available(stock, sku)对不存在SKU的处理。Agent的实际工具序列为：list_files → read_file → write_file → run_checks；随后最终交付，通过独立验收。这一轮共5次模型调用、4次工具调用。

| 模型调用 | 输入tokens | 与前次输入兼容的tokens | 实际hit | 实际prefill |
|---|---:|---:|---:|---:|
'''
    for r in requests:
        guide+=f'| {r["request_ordinal"]} | {r["input_tokens"]} | {r["compatible_prior_tokens"]} | {r["cached_tokens"]} | {r["prefill_tokens"]} |\n'
    guide+='''
“与前次输入兼容”指先前完整输入的块对齐可复用机会；实际hit还取决于当时缓存状态及其他共享前缀，因此两列定义不同。原始round从0开始，展示中的用户轮次从1开始。

对应第一个idle episode的一条原始快照如下。features是当时可见状态；event、remaining_s、terminal_s属于后续观测标签，训练时不能反向当作输入。

```json
'''+json.dumps({k:snapshot[k] for k in ['owner','features','event','remaining_s','weight']},indent=2)+'''
```

[完整关联示例JSON](leasepath_isca_v3_assets/examples/inventory_turn1_linked_example.json)包含该轮全部5次请求、4次原始工具记录、1条快照及最终验收结果。[来源哈希与选择规则](leasepath_isca_v3_assets/examples/source_manifest.json)记录取样和转换过程。另提供[简化工具JSONL](leasepath_isca_v3_assets/examples/tool_invocations_display.jsonl)，可与CATraces展示形式直接对照。

此例snapshot的waiting_tool是控制器观察阶段，不能据此认定工具在该时刻仍物理运行：本原型在模型调用之间消费工具通知。当前工具大多很短，队列和控制器延迟更长；示例展示真实覆盖，不增加新实验或扩大覆盖声明。
'''
    (ROOT/'docs/leasepath_dataset_examples_2026-11-12.md').write_text(guide)
    section = r'''
\begin{figure*}[t]
\centering
\includegraphics[width=\textwidth]{leasepath_isca_v3_assets/figures/workload_trace_examples.pdf}
\caption{Selected real tool invocations from six development projects; arguments and measured execution times are shown.}
\Description{Six actual development calls show read_file for inventory.py, a SQL invoice count, a health-port search, an HTTP health request, a numerical experiment and document checks. Durations are derived from original start and end timestamps. Results and IDs are omitted only from the display.}
\label{fig:trace-examples}
\end{figure*}
'''
    (ASSET/'generated/trace_examples_section.tex').write_text(section.lstrip())
    print(output.with_suffix('.pdf'))
    print(ROOT/'docs/leasepath_dataset_examples_2026-11-12.md')


if __name__=='__main__':
    main()
