# 当前 GBDT 的输入与训练流程

对应论文 Figure 6(B)，核对对象是连续 agent 试验部署的 `ContinuousGBDT`，不是早期四字段 GBDT。本次只核对已有代码与模型文件，没有重新训练。

## 实际输入

模型保留工具名。原始观测包含工具名、空闲已过时间、工具已运行时间、是否排队、队列长度、当前前缀 token 数、等待阶段、重试次数；另有 `compatibility_id` 作为兼容性检查，不参与学习。展开风险区间后再加入区间索引。

| 模型列 | 输入含义与编码 |
|---|---|
| `tool_category` | 工具名的类别 ID；未知工具为 0 |
| `log_elapsed_idle` | `log1p(elapsed_idle_s)` |
| `log_tool_elapsed` | `log1p(tool_elapsed_s)`；缺失为 −1 |
| `tool_elapsed_known` | 工具运行时间是否已知 |
| `ready` | `is_queued`，0/1；缺失为 −1 |
| `ready_known` | 排队状态是否已知 |
| `log_queue_length` | `log1p(queue_length)`；缺失为 −1 |
| `queue_known` | 队列长度是否已知 |
| `log_prefix_tokens` | 当前观测前缀长度的 `log1p`，不使用未来输入长度 |
| `waiting_tool` | 当前阶段是否为 `waiting_tool` |
| `phase_unknown` | 阶段是否缺失；queued/user 等非工具阶段的 `waiting_tool` 均为 0 |
| `log_retry_count` | `log1p(retry_count)`；缺失为 −1 |
| `retry_known` | 重试次数是否已知 |
| `interval_index` | 风险区间索引，代码从 0 开始；图内行号仅作示意 |

共 **14 列**，包括缺失标记与区间索引。未使用任务 ID、未来工具耗时、未来输出、未来命中或工具参数大小。训练时按 episode 的 SHA256 固定选约 10% episode，把工具类别映射为 unknown，学习未知工具的行为；不是删除工具名。

初始模型的工具类别为：`http_request`、`list_files`、`query_sql`、`read_file`、`run_checks`、`run_experiment`、`run_python`、`search_files`、`write_file`。输入契约包含这些状态，不等于已证明每列都有预测贡献；该初始 fit 中工具类别的 gain 为 0。

## 快照如何变成训练行

初始合格采集是 6 个已知开发项目、234 个 idle episode、1,079 个决策快照。完整初始拟合展开成 **9,229 个有效风险区间行**。805/274 快照的先后时间划分用于诊断，之后部署的初始模型使用全部 1,079 个快照，不是独立来源留出验证。

区间右端点为 `0.01, 0.03, 0.1, 0.3, 1, 3, 10, 30, 60, 120, 300, 600` 秒。每个快照按仍处于风险集的区间展开，特征附加对应 `interval_index`：

1. 完整经过且没有事件的区间标为 `waiting`。
2. 观测到合法下一次需求的事件区间标为 `U`；观测到退休则标为 `D`。
3. 右删失时，已完整观察的等待区间可用，未完整观察或未来区间屏蔽损失；`C` **不是分类器的第四类**。

快照权重先使项目等总权重，再使项目内 episode 等权、episode 内快照等权，最后归一化到平均快照权重为 1。区间展开沿用快照权重，以加权多分类 log loss 拟合。

当前初始采集的 1,079 个终端快照标签全部是 U；等待区间仍会产生 `waiting` 训练标签，初始数据没有观测 D/C 快照。Figure 6 的 mask 行是通用删失流程示意，不是该初始数据里新增了删失样本。动态 refit 的 as-of 截断另行处理未完成观测。

## 模型配置

| 参数 | 当前值 |
|---|---:|
| `num_boost_round` | 40 |
| `learning_rate` | 0.05 |
| `num_leaves` | 4 |
| `max_depth` | 2 |
| `min_data_in_leaf` | 20 |
| `min_sum_hessian_in_leaf` | 0.1 |
| `lambda_l1` | 0.1 |
| `lambda_l2` | 10.0 |
| `min_gain_to_split` | 0.01 |

实现为 LightGBM 三分类：U / D / waiting。每轮为三个类别各拟合一棵树；存档初始模型实际有 **120 棵树**。不是每棵树都长满四叶，初始无 D 样本时相应类别树可退化成常数。图中的树形只示意逐轮加树，实际 LightGBM 使用叶优先生长，并受最大深度限制。

固定 seed=23，单 CPU 训练线程，不做特征/样本子采样，本轮没有调参。40 轮是一次拟合内部的 boosting 过程。

## 从分类结果到 KV 分数

各风险区间输出条件 hazard：`hU, hD, h0`。先做生存递推：`pU[k] = S[k−1] × hU[k]`，`S[k] = S[k−1] × h0[k]`，再使用无条件需求质量评分；不能直接把所有区间的 `hU` 相加。

评分继续固定等成本、H=30 秒、τ=10 秒：`Wi = Σ exp(−rk/τ) × pU[i,k]`（仅 rk≤H）。共享块按最大 owner 分数除以 payload 字节数。只在容量压力时选择合法、未保护叶节点，先形成完整逻辑删除计划，再统一向 native KV 提交。

## 在线调整与静态权重

静态模型固定权重，但输入随当前状态改变。动态模式累计至少 100 个新增成熟 episode 且覆盖至少 5 个项目后，在一个后台线程用初始数据和截止时刻已观测的 live prefix **从头重新拟合**；600 秒轮询不会绕过样本门槛。未完成结果按截止时刻删失，随后在请求边界原子切换权重，不清空 KV。

在线 refit 不是持续往旧模型追加树；每次 refit 内部再执行上述 40 轮 boosting。

## 可追溯来源

- [输入、训练与推断实现](../src/leasepath/continuous_predictor_v1.py)
- [13 个状态/区间列的编码](../src/leasepath/agent_risk_predictor_v2.py)
- [训练超参数](../src/leasepath/gbdt_predictor.py)
- [区间标签与损失掩码](../src/leasepath/d2_hazards.py)
- [在线观测与 refit](../src/leasepath/continuous_online_v2.py)
- [初始模型存档](../results/agent/continuous_workflow_v5_2026-11-11/prediction/gbdt-initial.json)，SHA256：`bb4b0f85880dc191bdf6eb17ade264ad5849096b6c56eaacabed9a1412753da1`
- [图形生成脚本](leasepath_isca_v3_assets/generate_method_overview.py)
