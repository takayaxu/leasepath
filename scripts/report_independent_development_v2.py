"""Render the completed development observations and family validation."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text())


def main():
    pilot = ROOT/'results/agent/independent_loop_pilot_v1_2026-11-10/reaudit-v2/verified-view/batch-0'
    remaining = ROOT/'results/agent/independent_remaining3_v2_2026-11-10'
    pressure = ROOT/'results/agent/independent_pressure6_v2_2026-11-10'
    adaptation = ROOT/'results/prediction/independent_adaptation_v2_2026-11-10/artifacts'
    completed = read(remaining/'delivery.json')
    stressed = read(pressure/'delivery.json')
    learned = read(adaptation/'summary.json')
    first = []
    for directory in (pilot, remaining/'raw/batch-0'):
        loops = {r['task']:r for r in read(directory/'loop-audit.json')['tasks']}
        for path in sorted((directory/'batch').glob('*-quality.json')):
            row = read(path)
            first.append({**row, 'loop_certified':loops[row['task']]['loop_certified']})
    batch = stressed['batches'][0]
    result, physical = batch['summary'], batch['pressure']
    generations = completed['new_generations'] + stressed['new_generations']
    text = '# 独立开发题补齐、因果特征适配与真实 KV 竞争\n\n'
    text += ('本轮两项采集和按来源族的开发验证均已完成。**6/6 个开发来源族已覆盖；'
             '24 块／6 会话产生真实活跃 KV 淘汰；6 个独立留出族模型执行和训练样本数仍为 0。**\n\n')
    text += (f'本轮新增模型生成 {generations} 次，其中 26 次原生检查，两轮各 13 项全部通过。'
             '首次失败与压力轮重复实例分别保留，没有用后一次结果替换失败。\n\n')
    text += '## 开发题首次采集\n\n'
    text += f'六题首次结果：{sum(r["success"] for r in first)}/6 成功，{sum(r["loop_certified"] for r in first)} 个成功修订循环认证。\n\n'
    text += '| 开发题 | 成功 | 生成 | 工具 | 成功循环 | 终止原因 |\n|---|---|---:|---:|---|---|\n'
    for row in first:
        text += f'| {row["task"]} | {row["success"]} | {row["generations"]} | {len(row["tools"])} | {row["loop_certified"]} | {row["error"] or "正确最终答案"} |\n'
    text += ('\n新增三题每题都进行了 10 次不同候选评估，但在 11 次工具调用上限后仍要求继续调用，未提交正确最终答案。'
             '这说明本轮补齐的是轨迹和失败分母，任务完成率仍需要后续独立改进。预算与题目未改。\n\n')
    text += '## 24 块／6 会话压力观察\n\n'
    text += '| 指标 | 实测 |\n|---|---:|\n'
    fields = [('任务成功／总数',f'{result["complete_success"]}/{result["tasks"]}'),
              ('任务生成',result['generations']), ('全部块淘汰',physical['total_evictions']),
              ('有活跃 owner 的块淘汰',physical['live_owner_evictions']),
              ('多个活跃 owner 竞争的淘汰步骤',physical['competing_live_owner_sweeps']),
              ('在线预测调用',physical['prediction_calls']),
              ('同状态下区别于 LRU 的请求数',physical['lru_victim_set_changes']),
              ('同状态下区别于 3 秒点预测的请求数',physical['point_victim_set_changes']),
              ('实际复用 tokens',result['cached_tokens']),
              ('实际 prefill tokens',result['executed_prefill_tokens']),
              ('累计 prefill 秒',round(result['prefill_s'],4))]
    for name,value in fields:
        text += f'| {name} | {value} |\n'
    text += ('\n块淘汰次数可包含同一块重复被重算后再次淘汰，不是不同块的数量。在线调用数统计实际保留策略和其同状态诊断共用的预测快照。'
             '此前两轮 32 块／3 会话均零淘汰、零在线预测调用。\n\n')
    text += ('在线策略仍使用冻结 GBDT 和冻结等成本分布参数。上述同状态比较是受害块排序诊断，'
             '不是分别执行 LRU 后得到的命中或 JCT 差值；本轮不声称端到端加速。\n\n')
    text += ('本轮 147 次在线预测均发生未知状态回退，输出完整窗口仍有统计支持，因此不是支持不足导致的 LRU 回退。'
             '全局回退给出了相同分布，活跃 owner 评分无法区分，最终依赖 LRU 的 tie-break；受害块集合变化数为 0。\n\n')
    text += '## 预测适配与按开发来源族验证\n\n'
    text += (f'汇总 {learned["observed_episodes"]} 个观测 idle episode、{learned["snapshots"]} 个决策快照；'
             f'其中 {learned["original_queue_missing_snapshots"]} 个旧快照缺失队列信息，保留缺失值。'
             '重复会话仍只有六个母题，未当作新增来源族。\n\n')
    text += ('每次将一个开发来源族整体留出，另外五族训练；同一题的首次与压力会话始终同折。'
             '每族等权，族内 episode 等权，episode 内快照等权。只评估各方法共同可观测且有估计的快照。'
             '这属于开发交叉验证，独立留出评估尚未开展。\n\n')
    text += '| 预测器 | 1s Brier | 3s Brier | 10s Brier | 30s Brier | 分布策略可用快照 |\n|---|---:|---:|---:|---:|---:|\n'
    names = {'frozen_empirical':'冻结经验','frozen_gbdt':'冻结 GBDT','refit_legacy_gbdt':'同数据重训旧特征 GBDT',
             'adapted_empirical_v2':'状态经验 V2','adapted_gbdt_v2':'因果特征 GBDT V2'}
    for key,metric in learned['cross_validation'].items():
        scores = ['不可估计' if metric['horizons'][str(h)]['Brier_U'] is None else f'{metric["horizons"][str(h)]["Brier_U"]:.6f}' for h in (1,3,10,30)]
        text += '| '+names[key]+' | '+' | '.join(scores)+f' | {metric["distribution_policy_eligible_snapshots"]}/{metric["snapshots"]} |\n'
    text += ('\n**新增 GBDT 状态特征本轮没有带来额外预测收益。** GBDT V2 与同数据重训的旧特征 GBDT 在所有上述窗口上结果完全相同；'
             '全开发拟合模型也只对风险区间做了分裂，队列／耗时／前缀等状态特征的增益均为 0。'
             '相对于冻结 GBDT 的改善来自本轮数据适配，不能归因于新增特征。'
             '状态经验 V2 在本次按族开发验证中更好，但仍未执行独立留出或在线 JCT 对照。\n\n')
    text += ('\nBrier 越低越好。策略可用要求 H=30 秒内每个区间都有至少 5 个支持单位；'
             '本轮支持单位保守地取不同开发来源族，重复会话不会增加计数。可预测单个窗口不意味着可用于完整分布排序。\n\n')
    text += 'GBDT V2 特征增益：`'+json.dumps(learned['feature_gain'],ensure_ascii=False)+'`。\n\n'
    text += (f'全开发数据拟合的候选，在 {learned["full_candidate_policy_eligible_snapshots"]}/{learned["snapshots"]} 个开发快照上满足完整窗口支持门槛。'
             '候选模型另存，未替换冻结模型，也未自动上线；验证表才是跨开发族比较，不能用全量拟合后的训练内结果宣称收益。\n\n')
    text += '## 审计与限制\n\n'
    text += ('原生 token/请求映射、真实反馈消费、物理 KV、决策分数、FCFS 队列状态均通过审计。'
             '候选 worker 文件追踪未观察到私有参考解访问；这不等于文件系统隔离证明。'
             '模型复用既有全量哈希回执并核对完整 inode 身份，没有再次全量哈希。'
             '模型加载、追踪和共享主机影响均明确保留，本轮没有自然 JCT 因果性能结论。\n\n'
             'H=30s、tau=10s、等成本 1、最少 5 个风险集工作流、128 tokens／块、共享 owner 取最大值、'
             '仅压力下合法叶块淘汰、FCFS 均不变。冻结配置、模型与保护源码字节检查通过。\n\n')
    text += ('下一步可将状态经验 V2 作为开发在线对照候选，保留固定评分参数；'
             'GBDT 需要更多跨实例数据及可区分 owner 的状态覆盖，当前不能主张新特征有效。'
             '上线候选应另立模型身份并预先登记 LRU／点预测／分布成对运行。与此同时单独改善开发任务预算内完成率，'
             '否则不能把失败任务更早结束当成端到端收益。独立留出集继续封存。\n\n')
    text += '证据：\n\n'
    for p in (remaining/'delivery.json', pressure/'delivery.json', adaptation/'summary.json',
              adaptation/'gbdt-candidate.json', ROOT/'docs/independent_development_v2_2026-11-10.md'):
        text += f'- [{p.name}]({p})\n'
    report=ROOT/'reports/independent_development_v2_2026-11-10.md'
    report.write_text(text)
    print(report)


if __name__ == '__main__':
    main()
