"""Refresh a human-readable local progress report; never starts experiments."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]


def read(path):return json.loads(Path(path).read_text())


def render(pipeline):
    state=read(pipeline/'supervisor-state.json');config=read(pipeline/'plan.json')
    text='# 持续多工具 Agent 工作流实时进度\n\n'
    text+=f'更新时间：{datetime.now(timezone.utc).isoformat(timespec="seconds")}。流水线状态：**{state["status"]}**。\n\n'
    text+='已按 test_workflow.md 开始执行。六个本地持续开发项目各八轮需求，真实模型、文件／Python／SQL／本地 HTTP／检查工具，累计会话历史。六个既有独立留出族仍未进入模型。\n\n'
    for label,path in [('原始协议试点',ROOT/'results/agent/continuous_pilot_v1_2026-11-11/plan.json'),('自然交付修复版',Path(config['prior_plan'])),('文件读取与稳定反馈修复版',Path(config['repair_plan']))]:
        plan=read(path);ev=Path(plan['evidence']);work=Path(plan['work']);status=read(ev/'state.json') if (ev/'state.json').exists() else {'status':'registered_not_started'}
        rows=[read(p) for p in (work/'rounds').glob('*.json')]
        reservations=sum(len(p.read_text().splitlines()) for p in (work/'ledgers').glob('*.jsonl'))
        text+=f'## {label}\n\n状态 `{status["status"]}`；已观察 {len(rows)}/48 轮，独立验收成功 {sum(r["success"] for r in rows)}；生成预约（含协议检查）{reservations}。\n\n'
        if rows:
            text+='| 项目 | 已观察轮次 | 成功轮次 | 最后观察轮次 |\n|---|---:|---:|---:|\n'
            for owner in sorted({r['owner'] for r in rows}):
                part=[r for r in rows if r['owner']==owner]
                text+=f'| {owner} | {len(part)} | {sum(r["success"] for r in part)} | {max(r["round"] for r in part)+1} |\n'
            failures=[r for r in rows if not r['success']]
            if failures:
                text+='\n保留的失败：\n\n'
                for r in failures:text+=f'- {r["owner"]} 第 {r["round"]+1} 轮：`{r["error"]}`。\n'
        text+=f'\n[状态]({ev/"state.json"}) · [原始运行日志]({ev/"run.log"})\n\n'
    text+='## 已定位的根因及修复\n\n'
    text+='v1 六个首轮产物独立复查均正确，失败由“最终回答必须裸 JSON”的控制器要求造成。v2 改为正常 assistant 交付后，六个首轮已通过；原失败未改写。\n\n'
    text+='v2 日志第二轮把日志全文嵌入 Python 参数，1024-token 输出被截断。CPU 工具复现证明直接读取实际文件可完成同一计算；模型行为修复由独立 v3 运行验证。v3 还将 PID／资源测量留在审计证据，避免这些无关字段改变各策略的模型输入；外部用户返回由单调时钟定时通知记录。\n\n'
    text+='## 后续自动流程及证据边界\n\n'
    text+='修复采集 → 原生／反馈／KV／队列审计 → 能力门（整体至少90%，各项目至少80%，无未解决工具错误） → 目标场景经验分布与 GBDT 训练 → 五个真实开发端到端臂：LRU、动态经验分布、静态 GBDT 分布、动态 GBDT 点预测、动态 GBDT 分布。\n\n'
    text+='等成本评分 H=30s、tau=10s、成本1和合法淘汰／FCFS保持不变。动态更新只用截止时已成熟观测；训练和预测开销纳入实际服务运行。任一门失败保存整轮证据，停止后续性能运行，定位根因后另立版本，不自动重试替换样本。\n\n'
    text+='本次登记范围是六项目能力试点和一轮开发对照，尚不是60项目扩展或完整五组成对重复矩阵；不能提前宣称端到端收益。相关工作流／预测／冻结基线测试21项通过。\n\n'
    if state.get('error'):text+='当前停止原因：`'+state['error']+'`。\n\n'
    text+=f'[流水线状态]({pipeline/"supervisor-state.json"}) · [登记计划]({pipeline/"plan.json"})\n'
    report=ROOT/'reports/continuous_workflow_progress_2026-11-11.md';tmp=report.with_suffix('.tmp');tmp.write_text(text);tmp.replace(report)
    return state


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pipeline',type=Path,required=True);args=parser.parse_args()
    deadline=time.monotonic()+30000
    while True:
        state=render(args.pipeline)
        if state['status'] in ('completed_development_cycle','stopped_requires_review') or time.monotonic()>deadline:break
        time.sleep(20)
