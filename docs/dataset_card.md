# KV 复用时间预测数据集说明

核对日期：2026-10-31，Asia/Shanghai。本文是当前数据集的统一入口；各轮实验细节仍由原报告和冻结清单负责。

**当前主数据是本机实际运行 Agent、工具和推理服务采集的开发数据，包含公开题库派生任务与受控文件处理/修复任务。共有 50 个不同母题、65 条后端可审计执行、472 个 idle episode、2,414 张预测快照。完整质量子集有 54 条执行、296 个 episode、1,553 张快照，覆盖 47 个母题。50 是可审计母题数，完整质量通过的母题数为 47。**

四轮源数据分别保存在原目录；[训练 v2](../reports/prediction_v2_50parents_review.md) 已在新目录合并最小九字段，并完成经验预测/GBDT 的固定母题分组评测。增强字段尚未用于学习，仅用于分层评分。源数据的机器清单见 [50 母题清单](../results/prediction/dataset_inventory_50parents_2026-10-31.json)；此前 [16 母题清单](../results/prediction/dataset_inventory_2026-10-31.json) 保留为历史版本。源清单本身仍只清点原轮记录，合并训练另有版本和来源清单。

## 1. 数据在预测什么

预测对象是一个已经 idle 的 **owner–上下文版本–兼容 token 前缀关系**。在关系尚开放的时刻 `t0`，用此前真实可见状态，预测之后各时间区间的合法复用 U、明确终止 D 和继续等待概率。

| 层级 | 含义 | 主数据数量 |
|---|---|---:|
| 母题 `parent_task` | 一个任务定义；不同生成配置、输入规模、修复版本仍归原母题 | 50 |
| 执行 `workflow/run` | 一个 Agent 从开始到明确关闭上下文的完整运行 | 65 |
| `idle_episode` | 一次 owner–版本–前缀关系从实际 idle 开始到首个 U/D/C | 472 |
| 快照 `sample` | 某 episode 仍开放时刻的输入特征，预测起点为当前 `t0` | 2,414 |
| 风险区间行 `risk row` | 一张快照展开成若干尚处风险中的区间标签 | 7,051 |

同一个 workflow 有多次请求及多个 episode；一个 episode 可产生多张快照；一张快照又可展开多条风险行。这些行不是独立任务。当前选定主数据恰好每次请求释放产生一个 episode，因此请求数也为 472，这不是通用格式约束。

## 2. 当前组成与规模

以下仅统计选定主执行。`all-observed` 是本文对“全部后端可审计主执行”的统称：旧 MBPP 轮的实际目录名是 `all-main`，另外三轮为 `all-observed`。

| 采集轮 | 预定主执行 | 可审计执行 | 本轮涉及母题 | episode | 快照 | U / D / 自然 C | 风险行 |
|---|---:|---:|---:|---:|---:|---|---:|
| MBPP 派生轮 | 20 | 19 | 10 | 185 | 729 | 166 / 19 / 0 | 1,899 |
| 文件任务 v1 | 6 | 6 | 6 | 30 | 173 | 24 / 6 / 0 | 528 |
| 修复任务 v2 | 6 | 6 | 3，复用 v1 母题 | 46 | 333 | 40 / 6 / 0 | 1,405 |
| 母题扩展 v3 | 34 | 34 | 34，全部新增 | 211 | 1,179 | 177 / 34 / 0 | 3,219 |
| **逻辑合计** | **66** | **65** | **50，去重后** | **472** | **2,414** | **407 / 65 / 0** | **7,051** |

MBPP 有一条 HTTP 400 执行缺少完整后端证据，整体隔离，原始数据保留。质量差但后端证据完整的执行仍进入 all-observed：任务答案错误不自动使真实 lookup 标签无效。

`qualified` 是同一批执行中通过冻结独立测试、提交/真实编辑/测试、文件保护、循环限制及后端审计等条件的子集；v2 还要求实际复现初始缺陷、修改源码并成功重测。

| 采集轮 | 合格执行 | 涉及母题 | episode | 快照 | U / D / 自然 C | 风险行 |
|---|---:|---:|---:|---:|---|---:|
| MBPP 派生轮 | 14 | 10 | 76 | 285 | 62 / 14 / 0 | 717 |
| 文件任务 v1 | 5 | 5 | 25 | 145 | 20 / 5 / 0 | 444 |
| 修复任务 v2 | 4 | 2 | 32 | 237 | 28 / 4 / 0 | 1,011 |
| 母题扩展 v3 | 31 | 31 | 163 | 886 | 132 / 31 / 0 | 2,388 |
| **逻辑合计** | **54** | **47，去重后** | **296** | **1,553** | **242 / 54 / 0** | **4,560** |

两个表是主集与子集，**不能相加**。v1 图任务未通过质量条件，但 v2 图修复通过，因而此前 16 个母题仍全部有合格执行；v3 新增的 34 个母题中 31 个有合格执行。可审计主执行中 11 条未通过完整质量条件；另外还有 1 条后端不完整执行，不在主集内。

所有样本的 split 都是 `pilot_development_only`。当前有两个任务来源族：`google-research/mbpp` 与 `leasepath/designed-file-tasks-v1`；不代表两个生产项目上的独立测试，也没有预先冻结的独立来源/时间测试集。

## 3. 任务内容、耗时与状态覆盖

**MBPP：** 固定 sanitized MBPP revision `f82046ba5aabbbb427dbfd38a254d26bff08b533`，十个单函数任务。ID 为 `2,3,4,7,8,9,11,12,14,16`，分别涉及共有元素、非素数判断、最大的 n 个数、长单词提取、逐项平方、最小循环移位、移除首末字符出现、按行和排序、三棱柱体积和下划线字符串匹配。每题贪心/采样两种主配置，同题同组。完整主执行最长 idle 约 0.751 秒，主要覆盖短检查、编辑和 pytest。

**文件任务 v1：** 六类设计任务，每类一次主执行。

| 母题 | v1 工作量 | v1 实测测试工具时间 |
|---|---|---:|
| `designed-csv-ledger` | CSV 500,000 行按租户汇总金额 | 2.574 秒 |
| `designed-jsonl-status` | JSONL 150,000 条统计状态 | 2.035 秒 |
| `designed-sqlite-sales` | SQLite 500,000 行分组求和 | 1.874 秒 |
| `designed-gzip-digest` | 32 MiB 二进制 gzip 解压后 SHA-256 | 2.061 秒 |
| `designed-integer-sort` | 1,000,000 行整数文件排序 | 2.316 秒 |
| `designed-graph-distance` | 300,000 边图最短距离 | 1.705 秒 |

**修复任务 v2：** 复用 CSV、JSONL、图三个母题，每题中/大两档规模。初始 CSV/JSONL 是明确植入的累计错误；图是 v1 真实失败候选原样复用。输入规模在 Agent 运行前用参考实现校准，测试耗时为 3.540–8.948 秒，最长 idle 8.986 秒。

v2 共 14 次真实完整套件调用：8 次失败、6 次成功，其中 8 次是关联此前失败的重测。两个图执行都经历了“缺失起点失败 → 修复 → 空行失败 → 再修复 → 成功”。失败计数 0/1/2 在快照中均有覆盖；333 张主快照中 84 张处于实际重测中，76 张 elapsed ≥3 秒，38 张 elapsed ≥5 秒。

六条 v2 在会话内最后一次 visible/workload 测试均成功，随后独立检查发现两个 JSONL 实现漏掉空字符串 status，因此只有 4 条通过完整质量条件。会话关闭后的独立失败不能倒填成输入中的历史失败。

**母题扩展 v3：** 新增 34 个未与旧十题重复的 MBPP 任务，覆盖去重、稳定排序、嵌套结构、字典合并、组合计数、数论和字符串处理等。全部 ID 和语义见 [本轮说明](d2_diversity_v3.md)。每题固定输入域、一个公开例子和六个可见 workload 检查，其余检查独立保存；共新增 1,325 个确定性检查，连同公开例子共 1,428 项。34 个参考实现全部通过实际 pytest，34 个空函数负对照全部被拒绝。

本轮 34 条执行全部后端可审计，31 条独立复测及完整质量条件通过。Bell 数（67）、两位循环模式（92）、PascalCase（102）各执行到 16 步上限，独立检查也失败，原样保留于主集，未进入质量子集。共有 42 次真实 pytest（31 次成功、11 次失败）；所有工具为 0.014–0.554 秒，最长 idle 0.591 秒。因此这轮增加的是母题数和短任务状态，并未增加秒级长工具覆盖；不能以新增快照数量稀释旧秒级分层的评测。

文件及修复任务的输入为确定性生成，工具与 KV 时间线是实际测量；耗时包含 pytest、fixture 构建、函数执行与结果验证。没有用 sleep 或补时制造长调用。当前工具执行载体仍为 bash/pytest，未覆盖真实网络延迟、慢编译或并行 fanout/join。不同命令类别与计算任务不等于已采集多个原生工具系统。

## 4. 标签、时间单位与样本权重

| 存储值 | 简写 | 定义 |
|---|---|---|
| `legal_demand` | U | 后续实际兼容 lookup 使用该 token 前缀；hit 和 miss 都算 |
| `retire` | D | 显式关闭该 owner–版本–前缀的复用关系 |
| `right_censored` | C | 完整观测结束时关系仍开放，尚未见 U/D |

主数据自然 C=0：当前 runner 在结束执行时显式关闭上下文，所以最后一个关系生成 D。测试失败、工具结束和缓存淘汰都不自动是 D。短时间窗口内尚无 U/D 是已知继续等待 `h0`；行政截断另存，不覆盖原 U/D。真实 C 的语义和 loss mask 由独立契约/受控检查验证，当前主分布尚无自然 C 覆盖。

raw/event/episode 的时间为同主机 monotonic 纳秒；`elapsed_idle_s`、`tool_elapsed_s` 和 `remaining_s` 为秒。`snapshot_ns` 不是 Unix 时间，不能按墙上时间直接解释。episode 标签从 `t_idle` 起算；每张快照 target 的 `remaining_s = episode_end - t0`，不能把完整 episode 时长重复当作剩余时间。

快照仅在 episode 开放时刻生成。历史默认 elapsed 点为 `0,.1,1,5,30,120` 秒，最多 8 张；v1 扩展到包含 `.01,.03,.3,2,3,10` 等点、最多 20 张；v2 加上 4–9 秒、最多 24 张；v3 沿用 v1 设置。还采工具开始/结束与请求 enqueue/dispatch 时刻。各轮精确设置见原文档/冻结源码，不按未来结局挑预测时刻。

源 `sample_weight` 在一个 episode 内合计为 1。已有训练器再除以该 workflow 的 episode 数，使每个 workflow 总权重为 1；评分另外报告按母题聚合的指标。合并新数据时应延续这些规则，避免长调用的密集采样或失败循环支配训练。

风险区间右边界为 `0.01,0.03,0.1,0.3,1,3,10,30,60,120,300,600` 秒。逐行标签是 `U/D/waiting`；C 用于屏蔽未知区间，不是第四个预测类别。存在区间定义不等于已有该时长上的实测支持，例如当前没有 ≥10 秒的主 episode。

## 5. 每份文件长什么样

每个 `datasets/<view>/` 至少包含以下文件，JSONL 每行一个对象：

| 文件 | 一行对应什么 | 主要用途 |
|---|---|---|
| `events.jsonl` | 一条标准事件 | 复核实际 idle、lookup、前缀、工具与显式结束关联 |
| `episode_labels.jsonl` | 一个完整 episode | `start_ns/end_ns/duration_ns/outcome` 等最终标签 |
| `snapshots.jsonl` | 一个预测时刻 | 关联 ID、`snapshot_ns`、九字段 `features`、split、权重 |
| `targets.jsonl` | 一个快照的未来结局 | `sample_id/outcome/remaining_s`，与特征分文件隔离 |
| `summary.json` | 整个视图 | 审计状态、计数、raw 与输出哈希等 |
| `features-expansion-v1.jsonl` | v1/v3 增强特征 | 19 字段，通过 `sample_id` 对齐，不是额外样本 |
| `features-repair-v2.jsonl` | v2 增强特征 | 25 字段，通过 `sample_id` 对齐，外层另有母题 `group_id` |

`features-expansion-v1.jsonl` 存在于 v1/v3；v2 用 `features-repair-v2.jsonl` 同时承载基础、v1 和 v2 字段，旧 MBPP 轮不自动拥有新字段。与数据目录并列的 `hazards/<view>/` 中有 `risk_intervals.jsonl`、`administrative_views.jsonl` 和 `summary.json`。行政视图采用单独的 `label_scope=administrative_horizon`，不能追加为另一批自然 C 样本。

实际 raw journal 另外保存实际 token IDs、兼容配置、后端请求阶段、lookup 计数、请求释放时的 KV 块，以及命令/工具结果等。它承担证据追溯，不直接作为预测器输入。每轮 `frozen-round/raw.jsonl` 是共享 journal 在当时的完整快照，可能包含更早轮次，**不能把几个 frozen raw 直接拼接**；应按清单中选择的 workflow 使用。

### 特征版本

| 版本 | 字段 | 当前可用范围 |
|---|---|---|
| 最小快照，9 字段 | `elapsed_idle_s`、`compatibility_id`、`prefix_tokens`、`tool_name`、`tool_elapsed_s`、`phase`、`retry_count`、`is_queued`、`queue_length` | 四轮主数据均有；unknown 保持 null |
| `expansion-v1`，新增 10 字段 | `command_category`、`workload_family`、`work_units`、`work_unit`、`tool_timeout_s`、`prior_failed_tests`、`completed_tool_count`、`last_completed_tool_s`、`last_completed_returncode`、`tool_state` | v1/v3，以及继承它的 v2 |
| `repair-v2`，再新增 6 字段 | `current_suite_attempt`、`current_suite_is_retry`、`current_source_changed_since_failure`、`suite_retries_started`、`failed_full_suites`、`last_full_suite_passed` | v2 |

`retry_count` 在原九字段中仍为 unknown；不能把它悄悄替换成新语义。`prior_failed_tests` 是过去失败次数，不等于当前一定在重试；v2 当前重测还需实际开始事件及已结束失败的引用。`queue_length` 仍未知，单个请求已排队不证明已知全局队列长度。

所有特征只读 `t0` 前已经观测的记录。当前工具最终返回码与完整耗时须等 tool end 后才能作为过去历史；最终任务正确性、未来 U/D/C、未来 lookup、原始 token 内容、任务/请求/owner ID、源码 SHA 和审计引用不进入模型特征。当前 `tool_name` 多为 bash，计算类别由新 `workload_family` 表示。

### 一条真实样本

[完整原始摘录](../results/prediction/dataset_record_example_2026-10-31.json) 来自 CSV 750,000 行修复执行，同一个 `sample_id` 关联以下两侧内容：

| `t0` 时已知输入 | 实测值 |
|---|---|
| 已 idle 时间 / 当前工具运行时间 | 3.000 秒 / 2.994211674 秒 |
| 前缀长度 / phase | 2,688 tokens / `waiting_tool` |
| 当前类别与规模 | CSV 测试 / 750,000 行 |
| 当前尝试 / 是否重测 | 第 2 次 / true |
| 此前完整套件失败数 | 1 |
| 与所关联失败相比源码已改变 | true |

另存的未来 target 为 `outcome=legal_demand, remaining_s=0.617508912`：再过约 0.618 秒发生实际合法 lookup。模型在 `t0` 看不到这个 target。因而从该快照看，0.1 秒窗口的真值是等待，1 秒窗口的真值是 U。

## 6. 目录、来源和质量追溯

所有下列相对路径均相对仓库根目录；本机是 `/home/ma-user/takaya/leasepath`。

| 数据 | 主集目录 | 质量子集目录 |
|---|---|---|
| MBPP | `.artifacts/d2-next/datasets/all-main/` | `.artifacts/d2-next/datasets/qualified/` |
| 文件 v1 | `.artifacts/d2-expansion-v1/datasets/all-observed/` | `.artifacts/d2-expansion-v1/datasets/qualified/` |
| 修复 v2 | `.artifacts/d2-repair-v2-guided/datasets/all-observed/` | `.artifacts/d2-repair-v2-guided/datasets/qualified/` |
| 母题 v3 | `.artifacts/d2-diversity-v3/datasets/all-observed/` | `.artifacts/d2-diversity-v3/datasets/qualified/` |

各轮根目录另有 `tasks.json`、`runs/`、`evaluation/`、`evaluation-runs/`、`frozen-round/` 和 `hazards/`。小型结果分别在 `results/prediction/d2_mbpp_round/`、`d2_expansion_v1/`、`d2_repair_v2/`、`d2_diversity_v3/`，其中 `execution-quality.json` 逐条说明质量条件，`backend-audits.json` 记录证据完整性；v2 另有 `retry-audits.json`，v2/v3 均有 `workflow-groups.json`。v3 的参考/负对照及独立复测日志哈希见 `evaluation-evidence.json`。

大文件、本地源码工作区及模型在被 Git 忽略的 `.artifacts/` 等目录，克隆仓库并不会获得全部样本。恢复或换机器时需按各轮清单恢复这些本地证据，或者在新目录重新采集；Git 中的小型统计不是原始训练数据。

采集后端为 mini-swe-agent `2.4.6`，commit `04d809ceab9df28f9adaed044884180159172930`；Qwen2.5-Coder-7B-Instruct BF16，模型 revision `c03e6d358207e414f1eca0bb1891e29f1db0e242`；vLLM-Ascend。本机兼容身份为 `8c2c272c9e4510c4dae33537bbe75970e56a8e7dfdbfdf213b7283f07c994dfe`。实际配置和提示词由各轮 run manifest 保存。

审计要求包括 observer 序号、同主机时钟、实际 token/prefix、兼容配置、enqueue/dispatch/lookup/release、prefill 计数和已观测 KV 块。任务测试与后端完整性是两个独立维度。测试保护依赖协议、哈希和命令复核，未提供 OS 隔离；“通过冻结测试”不等于证明全部程序语义正确。

KV 块证据限于后端请求边界快照，不包含完整分配/淘汰生命周期；prefill 记录的是调度器实际 token 计数，不是设备 kernel 时间。

以下数据单独保留，不计入上面的 65 条主执行：

| 数据 | 排除原因 / 用途 |
|---|---|
| 最初 5 个 SymPy issue 运行 | 0/5 修复质量通过，主要为无效循环；保留 `.artifacts/d2/` 历史诊断 |
| MBPP direct、greedy-mini 及中途停止变体 | 配置/接口诊断，不是本次选定的贪心/采样主对照 |
| v2 原始 sed 诊断 | 两条限额失败、一条中断，保留 `.artifacts/d2-repair-v2/`，不当作完整主执行 |
| 真实后端受控检查 | 验证 miss 仍为 U、开放关系观测结束生成 C；与主分布分开 |
| D1 fixtures | 人工/合成契约验收，不是真实采集样本 |
| TraceLab、CacheWise 辅助数据 | 缺少主任务所需 owner/实际兼容前缀/lookup/retire 信息，仅有工具时间代理 |

## 7. 分组、训练状态与适用范围

合并时以 `source_group:parent_task_id` 作为母题分组：

```text
leasepath/designed-file-tasks-v1:designed-csv-ledger
  ├─ v1 原 CSV 执行
  ├─ v2 repair-csv-ledger-medium
  └─ v2 repair-csv-ledger-large
```

这三条必须同组；MBPP 同一题的贪心和采样也必须同组。不能随机拆快照、拆 episode，或按 `repair-...-medium/large` 新实例名制造不同组。已有数据可做按母题隔离的开发交叉验证；同一公开来源、相似题型、公开题库预训练暴露和受控任务偏差依然存在，不应称为独立来源泛化验证。

旧模型仅使用 19 条 MBPP 主执行/14 条质量子集，按 10 个母题分 5 折，原结果保持原版本。最新 [训练 v2](../reports/prediction_v2_50parents_review.md) 已使用四轮全部 65 条主执行/54 条质量子集，按 50/47 个母题分组评测。完整集 0.1 秒母题宏平均 Brier 为全局经验 0.4986、条件经验 0.3889、GBDT 0.4317；质量子集分别为 0.5124、0.4027、0.4440，越低越好。条件经验整体更好，GBDT 在已等待 ≥1 秒的状态上较好。

合并入口为 `prediction_merged.py` / `train_prediction_v2.py`，输出位于 `.artifacts/prediction/prediction_v2_50parents/merged/<view>/`：workflow 权重快照为 `snapshots-workflow-weighted.jsonl`，targets 和 episode 标签独立保存；`evaluation-metadata-only.jsonl` 含最终时长/质量等评测信息，禁止作为模型输入。小型结果、全量重拟合模型和来源清单在 `results/prediction/prediction_v2_50parents/`；30 份折内模型和 OOF 概率在本地 artifacts 根目录。原数据权重不变，合并快照每条执行总权重为 1。

三种模型沿用相同 tool/phase/elapsed 桶信息和原参数，新 command/workload/失败/重试特征尚未作为学习输入。旧轮缺失历史保持 unknown，不能补为 0；最终 episode 时长和执行质量仅用于事后分层。质量子集 10 秒窗口有一折缺少 `(3,10]` 训练风险集，该折 516 张快照三模型共同排除，不能把剩余 1,037 张的分数当作完整子集分数。长时段只有少数母题，尚不能据此认定可靠掌握其分布。

后续训练继续按母题绑定完整执行，保持 workflow 权重，并按当前 elapsed、最终 episode 时长、失败与重试状态分别评分。未知新字段不得根据未来结果回填；新模型/数据继续使用新版本目录。执行优先级以第 8 节的新计划为准，先核验真实工作负载和目标价值，再开展增强特征及模型比较。

这批数据可用于验证采集—标签—离线建模流程、短到秒级时间分布、受控失败/重测状态与模型开发。限制包括：50 个母题中 44 个来自同一公开 MBPP 来源，其余为 6 个受控文件任务；成功子集仅覆盖 47 个母题且有筛选偏差；无自然 C；无 ≥10 秒长尾；无真实并发依赖/多种外部工具；无独立生产项目/时间测试；未量化完整探针及在线预测开销。尚不能支持生产准确率、缓存收益或调度效果结论。

## 8. 下一轮采集方向

现有数据是流程与模型开发集。接下来按 [真实开发与决策收益计划](../reports/real_development_lookup_plan_2026-10-31.md) 执行：先验收候选 Codex/32B 本地服务与新版插桩，再分别采集自然开发会话、可重复真实仓库任务，调查工具/依赖完成到真实 lookup 的差异。新数据必须保留未配对、失败、开放关系、共享块和上下文变化，不能只选成功且有明显时间差的任务。

新配置尚未运行；旧 50 母题、数据视图、模型和分组保持原版本。阶段 1 用于探索，模型/指标选择之后另冻结任务/时间留出。只有差异在可决策状态中带来可利用的收益，才扩大复杂状态建模；GBDT 与条件经验获取同样的因果信息。硬件安排和原始数据的新计划路径见 [部署说明](deployment.md)。

## 9. 关联资料与本次清点

- [预测对象与因果契约](prediction_contract.md)
- [MBPP 运行说明](d2_mbpp_round.md) / [验收报告](../reports/d2_mbpp_round_acceptance.md)
- [文件扩展 v1 说明](d2_expansion_v1.md) / [Review](../reports/d2_expansion_v1_review.md)
- [修复扩展 v2 说明](d2_repair_v2.md) / [Review](../reports/d2_repair_v2_review.md)
- [母题扩展 v3 说明](d2_diversity_v3.md) / [Review](../reports/d2_diversity_v3_review.md)
- [已有 GBDT 开发评测](../reports/gbdt_review_2.md)
- [50 母题训练 v2 Review](../reports/prediction_v2_50parents_review.md) / [结果](../results/prediction/prediction_v2_50parents/summary.json)
- [50 母题机器清单](../results/prediction/dataset_inventory_50parents_2026-10-31.json) / [此前 16 母题清单](../results/prediction/dataset_inventory_2026-10-31.json) / [真实样本摘录](../results/prediction/dataset_record_example_2026-10-31.json)

v3 采集阶段完成独立复测及四轮清点；随后训练 v2 在新目录完成合并与建模。跨轮没有重复 workflow/episode/sample，qualified 是主集子集，分组按母题去重；旧数据和旧模型的结果哈希保持原值。训练 v2 的 30 份折内模型、全部折外概率和指标已独立复现，当前回归 121 项通过。
