# D2：MBPP 派生执行数据

当前跨轮规模、字段和分组使用方式见 [统一数据集说明](dataset_card.md)；本文保留 MBPP 本轮配置与结果。

本轮在同一 Qwen2.5-Coder-7B-Instruct BF16、Ascend 单卡、16K 本地服务上验证简单任务的真实执行。配置为 [`d2_mbpp_round.json`](../configs/prediction/d2_mbpp_round.json)，结果见 [本轮报告](../reports/d2_mbpp_round_acceptance.md)。这些是公开函数基准派生的开发任务，不是原生 GitHub issue，也不是官方 MBPP pass@1。

## 任务与评测

来源为 Google Research MBPP sanitized，固定 revision `f82046ba5aabbbb427dbfd38a254d26bff08b533`，题目 ID 在运行前固定为 `2,3,4,7,8,9,11,12,14,16`。包括交集、非素数、最大的 n 个数、单词筛选、列表平方、字符串旋转、字符删除、矩阵排序、棱柱体积和下划线字符串判断。

每题提供 `solution.py` stub、只读 `README.md` 和一个可见测试。其余公开测试与额外边界测试位于工作区外，共 458 个独立测试，另有 10 个可见测试。新增断言在模型运行前用公开参考实现全部核对；参考代码只在数据准备端使用，不进入 Agent 输入。不同配置从各自干净的 git 工作区开始。

独立评测只复制候选 `solution.py`，重新加入原始可见与独立测试，不使用 Agent 修改过的测试或自报通过结果。原始 stubs 应全部失败。单轮函数生成用于检查题目能力，不作为完整 Agent 执行轨迹。

## 最终执行协议

最终对照配置是 `greedy-guided` 与 `sampled`，使用 mini-swe-agent 固定 commit `04d809ceab9df28f9adaed044884180159172930` 的 `mini_textbased.yaml`，保留非交互文件创建/编辑示例，并显式提供函数签名与可见样例。工作区路径、提交规则和本地依赖说明作了适配。

runner 要求先实际执行 `cat README.md solution.py test_visible.py`，拒绝交互式编辑器，并要求当前源码通过可见 pytest 后才能提交。拒绝只生成客户端协议反馈事件，不伪装成已执行 bash；模型继续决定实际实现和命令。shell 使用 `set -e`，重复命令/结果会得到提示。独立测试不反馈给模型用于修补候选答案。

因此这些是**受流程约束的真实 Agent 执行**；不能将协议合规率说成模型在无约束环境下的自主能力。数据中保留全部模型请求、拒绝反馈、真实工具、修改、测试及失败，源码由模型生成，没有人工修补候选答案。

两组都使用最多 30 步、600 秒/任务、60 秒/工具、2,048 输出 tokens：

| 对照 | temperature | top_p | top_k | repetition_penalty | seed |
|---|---:|---:|---:|---:|---:|
| greedy-guided | 0 | 1 | 后端默认 | 后端默认 | 23 |
| sampled | 0.7 | 0.8 | 20 | 1.1 | 23 |

早期的较长仓库提示、仅补充 API 的尝试，以及没有强制流程的 mini 文本版本，属于诊断过程。其清单、失败、停止原因及轨迹分别保留，不能和最终两组混称同一个冻结配置。

## 新实验的执行顺序

当前本机数据已存在于 `.artifacts/d2-next/`；以下使用新的目录，避免覆盖本轮。模型与 Agent 环境安装见 [首轮部署](d2_first_round.md)。

```bash
cd /home/ma-user/takaya/leasepath
PYTHONPATH= .venv/bin/python scripts/d2_service.py status
# 未运行时才执行 d2_service.py start，等待 health 成功。

PYTHONPATH= .venv/bin/python scripts/prepare_d2_mbpp.py --output .artifacts/d2-mbpp-replay
PYTHONPATH= .venv/bin/python scripts/evaluate_d2_mbpp.py \
  --variant baseline --tasks .artifacts/d2-mbpp-replay/tasks.json

PYTHONPATH= .venv-agent/bin/python scripts/run_d2_mbpp.py \
  --variant direct --tasks .artifacts/d2-mbpp-replay/tasks.json
PYTHONPATH= .venv/bin/python scripts/evaluate_d2_mbpp.py \
  --variant direct --tasks .artifacts/d2-mbpp-replay/tasks.json

PYTHONPATH= .venv-agent/bin/python scripts/run_d2_mbpp.py \
  --variant greedy-guided --tasks .artifacts/d2-mbpp-replay/tasks.json
PYTHONPATH= .venv/bin/python scripts/evaluate_d2_mbpp.py \
  --variant greedy-guided --tasks .artifacts/d2-mbpp-replay/tasks.json

PYTHONPATH= .venv-agent/bin/python scripts/run_d2_mbpp.py \
  --variant sampled --tasks .artifacts/d2-mbpp-replay/tasks.json
PYTHONPATH= .venv/bin/python scripts/evaluate_d2_mbpp.py \
  --variant sampled --tasks .artifacts/d2-mbpp-replay/tasks.json

PYTHONPATH= .venv/bin/python scripts/finalize_d2_mbpp.py \
  --root .artifacts/d2-mbpp-replay --output .artifacts/d2-mbpp-replay/summary
```

`--task-id` 可做单题检查；已完成单题后，在相同源码和冻结配置下使用 `--resume` 继续剩余任务。任务进行中断且没有完整 result 时不会自动从修改过的 workspace 继续，以免冒充干净初始状态。评测和 finalize 输出目录均应选择新路径。

## 数据与质量条件

在 `.artifacts/d2-next/` 中：

| 路径 | 内容 |
|---|---|
| `runs/<variant>/<task>/` | 模型轨迹、候选源码、补丁、工具统计与退出状态 |
| `evaluation-runs/<variant>/` | 独立 pytest 输出、JUnit 和结果 |
| `frozen-round/` | 冻结的 raw、后端配置、任务清单、源码快照 |
| `datasets/all-main/` | 最终两组通过后端完整性审计的执行事件、标签、快照及 targets，包含任务失败；后端证据不足的整条 workflow 隔离 |
| `datasets/qualified/` | 通过预先声明质量条件的完整 workflows |
| `datasets/<variant>/` | 单独保存的诊断或配置组，不混合冒充主任务 |
| `hazards/{all-main,qualified}/` | 风险区间 targets、窗口统计和单独的行政删失视图 |

质量条件为：正常提交、真实源码修改、保护文件未变、实际检查/编辑/成功 pytest、独立测试全部观察且通过、无过量相同命令循环，以及后端事件审计通过。Git 中的小型质量清单保留所有执行及未通过原因，不只列成功样本。

本轮最终两组共 20 条执行，14 条通过全部质量条件，覆盖 10 个不同题目。贪心组为 8/10，采样组为 6/10；采样组另有一题独立测试通过但流程不合格。`mbpp-0014-sampled` 最后一次请求返回 HTTP 400，缺少后端完整阶段证据；全部原始轨迹保留，该 workflow 整体不进入派生标签。因此 `all-main` 有 19 条可审计执行，完整 20 条仍可从冻结 raw、运行清单与 `execution-quality.json` 追溯。逐条审计结果见 `results/prediction/d2_mbpp_round/backend-audits.json`。

合格子集按任务最终成功筛选，存在选择偏差；不能把其等待时间分布当成无条件工作负载分布。主任务完整数据与各配置分层统计必须同时保留。同一题目的所有配置运行与全部快照属于同组，本轮统一为 `pilot_development_only`，没有独立测试切分。

`risk_intervals.jsonl` 的 label 为 `U`、`D` 或 `waiting`；真实观测提前结束的部分区间设 `loss_mask=false`，不会补造等待。`administrative_views.jsonl` 明确设置 `label_scope=administrative_horizon`，只提供固定 H 截断的派生视图，不能算作自然 C 或覆盖原始 U/D。

短函数题不覆盖大型仓库、真实慢构建或并行依赖，也不提供公开题目未进训练集的保证。完整插桩开销与在线缓存策略收益仍需后续实验。
