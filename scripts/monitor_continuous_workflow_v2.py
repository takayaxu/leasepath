"""Monitor all retained attempts and the active repair pipeline, read-only."""
import argparse
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]


def read(path):return json.loads(Path(path).read_text())


def render(pipeline):
    current=read(pipeline/'supervisor-state.json')
    text='# 持续多工具 Agent 工作流实时进度\n\n'
    text+=f'北京时间：{datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")}。当前流水线：**{current["status"]}**。\n\n'
    text+='六个持续开发项目各八轮用户需求；真实文件、Python、SQL、本地 HTTP、数值实验和检查工具。六个独立留出族未使用。\n\n'
    text+='## 逐版保留结果\n\n| 版本 | 状态 | 实际轮次 | 通过轮次 | 预约生成（含gate） |\n|---|---|---:|---:|---:|\n'
    for version in (1,2,3,4,5):
        ev=ROOT/f'results/agent/continuous_pilot_v{version}_2026-11-11';path=ev/'plan.json'
        if not path.exists():continue
        plan=read(path);work=Path(plan['work']);state=read(ev/'state.json') if (ev/'state.json').exists() else {'status':'registered_not_started'}
        rows=[read(p) for p in (work/'rounds').glob('*.json')]
        reservations=sum(len(p.read_text().splitlines()) for p in (work/'ledgers').glob('*.jsonl'))
        text+=f'| v{version} | {state["status"]} | {len(rows)}/48 | {sum(r["success"] for r in rows)} | {reservations} |\n'
        if version==4:text+='\n<!-- v4 was registered but cancelled before model execution after more complete repair became necessary. -->\n'
    text+='\n各版本使用同一批项目，不能相加当作新增独立实例。失败后停止该项目，因此未执行轮次也属于48轮计划覆盖分母。v4登记后在模型执行前撤下，新增模型调用0。\n\n'
    text+='## 根因与处理\n\n'
    text+='- v1：产物正确，但控制器要求最终回答必须裸JSON。v2已改为自然交付后独立验收，原错误保留。\n'
    text+='- v2：复制日志为代码字面量导致输出截断，实际文件读取指导缺失；v3已验证日志第二轮通过。容量从128增到192块，保留全历史。\n'
    text+='- v3：日志目标文件未写明、库存配送参数类型未写明；下一版只补充规格，不改变正确答案。\n'
    text+='- v3：账单查得正确数据却保存默认零值、API写出非法JSON。下一版工具在写入前验证JSON，公开检查验证目标文件、排序、代码行为及金额一致性；返回真实错误供Agent修订，独立最终验收器不变。\n\n'
    if current.get('error'):text+='当前停止原因：`'+current['error']+'`。\n\n'
    text+='## 当前版分项目结果\n\n'
    plan=read(pipeline/'plan.json');repair=read(plan['repair_plan']);work=Path(repair['work'])
    rounds=[read(p) for p in (work/'rounds').glob('*.json')]
    text+='| 项目 | 已执行 | 通过 | 原因 |\n|---|---:|---:|---|\n'
    for owner in ['continuous_inventory','continuous_billing','continuous_logs','continuous_api','continuous_experiment','continuous_documents']:
        rows=[r for r in rounds if r['owner']==owner]
        reasons='；'.join(r['error'] for r in rows if r['error'])
        text+=f'| {owner} | {len(rows)}/8 | {sum(r["success"] for r in rows)} | {reasons} |\n'
    text+='\n## 训练及端到端\n\n'
    if (pipeline/'prediction/summary.json').exists():
        learned=read(pipeline/'prediction/summary.json')
        text+=f'目标场景预测器已训练：{learned["projects"]}项目、{learned["episodes"]}个观测episode、{learned["snapshots"]}个决策快照。时间前后验证与全量初始模型分别保存。\n\n'
    else:text+='尚未进入本次目标场景训练，须先通过修复采集与快照审计。\n\n'
    for arm in plan['e2e_arms']:
        ev=pipeline/'e2e'/arm['name'];state=read(ev/'state.json') if (ev/'state.json').exists() else {'status':'not_started'}
        text+=f'- {arm["name"]}：`{state["status"]}`，{state.get("successful_rounds",0)}/{state.get("completed_rounds",0)}已观察轮次通过。\n'
    text+='\n等成本评分H30/tau10、成本1、支持门槛及FCFS保持冻结。动态训练只用截止时已成熟的标签，开销计入服务观察。此次登记范围是六项目修复及一轮开发对照，60项目扩展和多轮平衡重复仍属后续工作，不能提前宣称端到端收益。\n\n'
    text+=f'[活动流水线状态]({pipeline/"supervisor-state.json"}) · [活动计划]({pipeline/"plan.json"}) · [当前采集日志]({Path(repair["evidence"])/"run.log"})\n'
    report=ROOT/'reports/continuous_workflow_progress_2026-11-11.md';temp=report.with_suffix('.tmp');temp.write_text(text);temp.replace(report)
    return current


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pipeline',type=Path,required=True);parser.add_argument('--wait-prior',type=Path);args=parser.parse_args()
    deadline=time.monotonic()+30000
    if args.wait_prior:
        while read(args.wait_prior)['status'] not in ('completed_development_cycle','stopped_requires_review'):
            if time.monotonic()>deadline:raise TimeoutError('prior monitor handover exceeded budget')
            time.sleep(15)
        time.sleep(25)
    while True:
        state=render(args.pipeline)
        if state['status'] in ('completed_development_cycle','stopped_requires_review') or time.monotonic()>deadline:break
        time.sleep(20)
