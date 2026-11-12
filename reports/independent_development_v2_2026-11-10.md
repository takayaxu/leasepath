# 独立开发题补齐、因果特征适配与真实 KV 竞争

本轮两项采集和按来源族的开发验证均已完成。**6/6 个开发来源族已覆盖；24 块／6 会话产生真实活跃 KV 淘汰；6 个独立留出族模型执行和训练样本数仍为 0。**

本轮新增模型生成 132 次，其中 26 次原生检查，两轮各 13 项全部通过。首次失败与压力轮重复实例分别保留，没有用后一次结果替换失败。

## 开发题首次采集

六题首次结果：2/6 成功，2 个成功修订循环认证。

| 开发题 | 成功 | 生成 | 工具 | 成功循环 | 终止原因 |
|---|---|---:|---:|---|---|
| independent_job_shop | False | 12 | 11 | False | RuntimeError: eleven-call budget exhausted |
| independent_package_resolution | True | 10 | 9 | True | 正确最终答案 |
| independent_propositional_sat | True | 12 | 11 | True | 正确最终答案 |
| independent_error_correction | False | 12 | 11 | False | RuntimeError: eleven-call budget exhausted |
| independent_exact_cover | False | 12 | 11 | False | RuntimeError: eleven-call budget exhausted |
| independent_max_flow | False | 12 | 11 | False | RuntimeError: eleven-call budget exhausted |

新增三题每题都进行了 10 次不同候选评估，但在 11 次工具调用上限后仍要求继续调用，未提交正确最终答案。这说明本轮补齐的是轨迹和失败分母，任务完成率仍需要后续独立改进。预算与题目未改。

## 24 块／6 会话压力观察

| 指标 | 实测 |
|---|---:|
| 任务成功／总数 | 2/6 |
| 任务生成 | 70 |
| 全部块淘汰 | 250 |
| 有活跃 owner 的块淘汰 | 220 |
| 多个活跃 owner 竞争的淘汰步骤 | 220 |
| 在线预测调用 | 147 |
| 同状态下区别于 LRU 的请求数 | 0 |
| 同状态下区别于 3 秒点预测的请求数 | 0 |
| 实际复用 tokens | 25088 |
| 实际 prefill tokens | 39527 |
| 累计 prefill 秒 | 66.9458 |

块淘汰次数可包含同一块重复被重算后再次淘汰，不是不同块的数量。在线调用数统计实际保留策略和其同状态诊断共用的预测快照。此前两轮 32 块／3 会话均零淘汰、零在线预测调用。

在线策略仍使用冻结 GBDT 和冻结等成本分布参数。上述同状态比较是受害块排序诊断，不是分别执行 LRU 后得到的命中或 JCT 差值；本轮不声称端到端加速。

本轮 147 次在线预测均发生未知状态回退，输出完整窗口仍有统计支持，因此不是支持不足导致的 LRU 回退。全局回退给出了相同分布，活跃 owner 评分无法区分，最终依赖 LRU 的 tie-break；受害块集合变化数为 0。

## 预测适配与按开发来源族验证

汇总 132 个观测 idle episode、443 个决策快照；其中 60 个旧快照缺失队列信息，保留缺失值。重复会话仍只有六个母题，未当作新增来源族。

每次将一个开发来源族整体留出，另外五族训练；同一题的首次与压力会话始终同折。每族等权，族内 episode 等权，episode 内快照等权。只评估各方法共同可观测且有估计的快照。这属于开发交叉验证，独立留出评估尚未开展。

| 预测器 | 1s Brier | 3s Brier | 10s Brier | 30s Brier | 分布策略可用快照 |
|---|---:|---:|---:|---:|---:|
| 冻结经验 | 0.098526 | 0.312145 | 0.288174 | 0.158807 | 443/443 |
| 冻结 GBDT | 0.103283 | 0.310208 | 0.286592 | 0.160403 | 443/443 |
| 同数据重训旧特征 GBDT | 0.079954 | 0.111999 | 0.254385 | 0.150105 | 443/443 |
| 状态经验 V2 | 0.000000 | 0.011995 | 0.216754 | 0.069769 | 443/443 |
| 因果特征 GBDT V2 | 0.079954 | 0.111999 | 0.254385 | 0.150105 | 443/443 |

**新增 GBDT 状态特征本轮没有带来额外预测收益。** GBDT V2 与同数据重训的旧特征 GBDT 在所有上述窗口上结果完全相同；全开发拟合模型也只对风险区间做了分裂，队列／耗时／前缀等状态特征的增益均为 0。相对于冻结 GBDT 的改善来自本轮数据适配，不能归因于新增特征。状态经验 V2 在本次按族开发验证中更好，但仍未执行独立留出或在线 JCT 对照。


Brier 越低越好。策略可用要求 H=30 秒内每个区间都有至少 5 个支持单位；本轮支持单位保守地取不同开发来源族，重复会话不会增加计数。可预测单个窗口不意味着可用于完整分布排序。

GBDT V2 特征增益：`{"log_elapsed_idle": 0.0, "log_tool_elapsed": 0.0, "tool_elapsed_known": 0.0, "ready": 0.0, "ready_known": 0.0, "log_queue_length": 0.0, "queue_known": 0.0, "log_prefix_tokens": 0.0, "waiting_tool": 0.0, "phase_unknown": 0.0, "log_retry_count": 0.0, "retry_known": 0.0, "interval_index": 134.95146888494492}`。

全开发数据拟合的候选，在 443/443 个开发快照上满足完整窗口支持门槛。候选模型另存，未替换冻结模型，也未自动上线；验证表才是跨开发族比较，不能用全量拟合后的训练内结果宣称收益。

## 审计与限制

原生 token/请求映射、真实反馈消费、物理 KV、决策分数、FCFS 队列状态均通过审计。候选 worker 文件追踪未观察到私有参考解访问；这不等于文件系统隔离证明。模型复用既有全量哈希回执并核对完整 inode 身份，没有再次全量哈希。模型加载、追踪和共享主机影响均明确保留，本轮没有自然 JCT 因果性能结论。

H=30s、tau=10s、等成本 1、最少 5 个风险集工作流、128 tokens／块、共享 owner 取最大值、仅压力下合法叶块淘汰、FCFS 均不变。冻结配置、模型与保护源码字节检查通过。

下一步可将状态经验 V2 作为开发在线对照候选，保留固定评分参数；GBDT 需要更多跨实例数据及可区分 owner 的状态覆盖，当前不能主张新特征有效。上线候选应另立模型身份并预先登记 LRU／点预测／分布成对运行。与此同时单独改善开发任务预算内完成率，否则不能把失败任务更早结束当成端到端收益。独立留出集继续封存。

证据：

- [delivery.json](/home/liuxv/LeasePath-new-server-data/results/agent/independent_remaining3_v2_2026-11-10/delivery.json)
- [delivery.json](/home/liuxv/LeasePath-new-server-data/results/agent/independent_pressure6_v2_2026-11-10/delivery.json)
- [summary.json](/home/liuxv/LeasePath-new-server-data/results/prediction/independent_adaptation_v2_2026-11-10/artifacts/summary.json)
- [gbdt-candidate.json](/home/liuxv/LeasePath-new-server-data/results/prediction/independent_adaptation_v2_2026-11-10/artifacts/gbdt-candidate.json)
- [independent_development_v2_2026-11-10.md](/home/liuxv/LeasePath-new-server-data/docs/independent_development_v2_2026-11-10.md)
