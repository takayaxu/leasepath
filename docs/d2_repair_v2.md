# D2 3–10 秒工具与失败后重测 v2

当前跨轮规模、字段和分组使用方式见 [统一数据集说明](dataset_card.md)；本文保留 v2 本轮配置与结果。

日期：2026-10-31。此轮沿用 mini-swe-agent、固定 Qwen2.5-Coder-7B-Instruct、vLLM-Ascend 和真实 KV 插桩，采集受控 test-first 修复执行。结果见 [Review](../reports/d2_repair_v2_review.md)，配置见 [d2_repair_v2.json](../configs/prediction/d2_repair_v2.json)。本轮不训练预测器。

## 任务、规模和失败来源

从 [v1](d2_expansion_v1.md) 的 CSV、JSONL 和图最短路三个母题各取两个规模，形成六条执行。它们不是六个新母题，也不是新增三个独立来源。

| 母题 | 中档输入 | 大档输入 | 初始缺陷来源 |
|---|---:|---:|---|
| CSV 汇总 | 750,000 行 | 1,650,000 行 | 显式植入覆盖累计结果的缺陷：同租户后值覆盖前值 |
| JSONL 状态计数 | 300,000 条 | 720,000 条 | 显式植入重复状态计数一直为 1 的缺陷 |
| 图最短距离 | 750,000 边 | 1,500,000 边 | 原样复用 v1 Agent 的失败源码：起点不存在和空行处理遗漏 |

任务输入为确定性生成，pytest、输入准备、候选执行、结果校验、模型请求及 lookup 均实际运行。工具时间包括 fixture 构建，不代表函数本身全部需要这些秒数。没有 sleep、空转补时、伪造返回码或人工修复候选源码。

规模候选与时长档位在 Agent 运行前固定：参考实现完整三组测试的 wall time 首次进入 `[3.2,5.8]` 或 `[6.8,9.5]` 秒即选择该规模；超上界或候选不足则准备器报错，保留诊断。所有参考检查通过，缺陷实现运行实际 visible/workload pytest 必须以 code 1 失败。校准记录同时保留未达到档位的较小规模。

每次主执行遵守：检查四个任务文件 → 原始缺陷实现跑完整 visible/workload 测试 → Agent 修改 `solution.py` → 同一完整测试重新运行 → 当前源码成功后单独提交。两个规模的工作区、输入测试和初始缺陷分别冻结。独立边界检查只在会话关闭后复测，不反馈给 Agent。

## 执行配置与复现

模型与 Agent revision、temperature=0、seed=23、2048 输出 token 沿用此前配置；本轮最多 24 次模型调用，每工具 60 秒，每任务 420 秒。明确复现初始失败之前，runner 拒绝实际执行源码编辑；拒绝是协议反馈，不计工具失败。

最初 runner 在两个 CSV 上陷入不生效的 sed 替换循环，均达到 24 步；第三条 JSONL 执行被中断。原轨迹、源码、配置、raw 快照和停止说明在 `.artifacts/d2-repair-v2/` 保存。之后使用 `run_d2_repair_guided.py`：明确要求完整文件 heredoc 编辑、测试单独成一条命令；不提供修复算法或答案。新工作区从原始 Git 提交恢复，初始源码、测试、规模哈希完全相同，派生清单记录来源。该调整后的六条是主执行集，不能把它和此前诊断混在一起报告无条件成功率。

已有主结果为 `.artifacts/d2-repair-v2-guided/`。新目录复现：

```bash
.venv/bin/python scripts/d2_service.py status
.venv/bin/python scripts/prepare_d2_repair.py --output .artifacts/d2-repair-replay-prepared
.venv/bin/python scripts/prepare_d2_repair_guided.py --source .artifacts/d2-repair-replay-prepared --output .artifacts/d2-repair-replay-guided
.venv-agent/bin/python scripts/run_d2_repair_guided.py --tasks .artifacts/d2-repair-replay-guided/tasks.json
.venv/bin/python scripts/finalize_d2_repair.py --root .artifacts/d2-repair-replay-guided --output results/prediction/d2_repair_replay
.venv/bin/python scripts/verify_d2_repair.py --root .artifacts/d2-repair-replay-guided --results results/prediction/d2_repair_replay
```

准备器和 finalize 拒绝覆盖已有目录；guided runner 的 `--resume` 校验配置、任务、提示词和依赖代码哈希。所有资源从仓库根目录或显式参数解析。首个准备器读取 v1 图任务的真实失败候选，因此需要本机 `.artifacts/d2-expansion-v1/` 和相应结果清单；换机器必须恢复这些历史证据，不能替换成伪造初始实现。

## 数据与因果重试接口

| 路径 | 内容 |
|---|---|
| `.artifacts/d2-repair-v2/diagnostic-stop.json` | 两条达到限额和一条中断的诊断说明 |
| `.artifacts/d2-repair-v2/diagnostic-frozen/` | 停止诊断时的 raw 与日志快照 |
| `.artifacts/d2-repair-v2-guided/tasks.json` | 六个工作区、原母题、档位、缺陷来源、源码和测试哈希 |
| `.artifacts/d2-repair-v2-guided/runs/greedy-guided/` | 主执行轨迹、源码、patch、每次工具完整输出及 run manifest |
| `.artifacts/d2-repair-v2-guided/evaluation-runs/` | 原始测试复制至独立目录后的候选复测与 JUnit |
| `.artifacts/d2-repair-v2-guided/frozen-round/` | 完整 journal、源码、配置和后端日志冻结快照 |
| `.artifacts/d2-repair-v2-guided/datasets/all-observed/` | 后端和工具完整性可审计的主执行，不按正确性丢数据 |
| `.artifacts/d2-repair-v2-guided/datasets/qualified/` | 全部独立测试及复现/修改/成功重测条件通过的子集 |
| `.artifacts/d2-repair-v2-guided/hazards/` | 风险区间和单独的行政截断视图 |
| `results/prediction/d2_repair_v2/` | 小型审阅清单、分组、重试证据和结果哈希 |

九字段最小快照与 `expansion-v1` 投影均保持原样。增强文件另命名 `features-repair-v2.jsonl`，在 v1 十个增强字段之上增加以下六个字段：

| 字段 | 定义 |
|---|---|
| `current_suite_attempt` | 当前已开始的完整套件是本会话第几次；非完整测试时为 unknown |
| `current_suite_is_retry` | 当前套件开始前，确实存在同一套件已结束的失败；不使用本次未来结果 |
| `current_source_changed_since_failure` | 当前测试开始时的源码哈希是否不同于所关联失败结束时的源码；没有失败时 unknown |
| `suite_retries_started` | 本会话已经开始的、显式关联此前失败的完整套件重测次数 |
| `failed_full_suites` | 截至当前已结束的完整套件失败数；运行中不提前累加 |
| `last_full_suite_passed` | 最近已经结束的完整套件是否成功；尚无结果时 unknown |

`retry_of_tool_id` 和源码 SHA 仅供审计，不是输入特征。`prior_failed_tests` 是 v1 字段；guided profile 只把实际独立的完整 pytest 命令记为测试，避免将 sed/编辑命令中的 pytest 字符串误计为测试。失败计数在实际 tool end 后更新，当前重测标记在实际 tool start 后更新；未来返回码或总时长不会提前出现。

`retry-audits.json` 逐 workflow 核对 start/end 配对、失败计数、尝试计数、前一失败引用、源码是否改变、期间真实编辑事件和成功时源码稳定。失败后原样重跑也可以计重测，但质量通过还要求一次失败后真实修改源码且重测成功。

固定 elapsed 点为 `0,.01,.03,.1,.3,1,2,3,4,5,6,7,8,9,10,30,120` 秒，额外保留工具开始/结束、enqueue/dispatch，每 episode 最多 24 张；只在仍开放时导出，并保持每 episode 快照权重之和为 1。更多快照不等于更多独立样本。

`workflow-groups.json` 及增强文件外层 `group_id` 将中档、大档和 v1 原执行归到相同的 `source_group:parent_task_id`。原九字段训练入口尚未实现该新数据的合并导入，**不能按新实例名直接切分后重训**。必须先审阅导入、分组、workflow 权重和扩展特征消融方案。

U 仍追溯真实兼容 lookup，不受测试失败影响；D 仍来自明确关闭关系；C 仍来自开放关系下的观测结束。协议拒绝、测试失败、工具结束和重试本身都不自动生成 D。真实 LocalEnvironment 没有操作系统隔离，测试保护依赖协议、哈希和命令复核。
