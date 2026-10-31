# D2 秒级任务扩展 v1

当前跨轮规模、字段和分组使用方式见 [统一数据集说明](dataset_card.md)；本文保留 v1 本轮配置与结果。

日期：2026-10-31。此轮是在 [MBPP 轮](d2_mbpp_round.md) 之后新增的六个母题，沿用 mini-swe-agent 和固定 Qwen2.5-Coder-7B 后端。已完成一次小规模实测，供 review；没有重新训练预测器。

任务是本项目设计的文件处理需求，输入数据为确定性生成，工具耗时和 KV lookup 均为真实测量。不得将它称为自然生产分布、公开 benchmark 分数或六个独立来源项目。

| 母题 | 实际测试规模 | Agent 测试工具耗时 |
|---|---|---|
| CSV 账目汇总 | 500,000 行，97 个租户，正负金额 | 2.574 秒 |
| JSONL 状态统计 | 150,000 条记录，11 种状态 | 2.035 秒 |
| SQLite 分组查询 | 500,000 行，53 个 region | 1.874 秒 |
| gzip 解压后 SHA-256 | 32 MiB 确定性伪随机二进制 | 2.061 秒 |
| 整数文件排序 | 1,000,000 行整数 | 2.316 秒 |
| 图最短距离 | 300,000 条链边 | 1.705 秒 |

耗时包含 pytest、真实输入生成、候选函数执行和结果验证；没有单独测出候选函数用时，尤其 gzip 工具时间包含压缩测试输入。没有 sleep、固定等待、空转补时或伪造 U/D/C。每个母题仅运行一个规模，尚不能分辨同一类别中的规模效应。

## 固定配置和执行方式

配置为 [d2_expansion_v1.json](../configs/prediction/d2_expansion_v1.json)：贪心解码、seed 23、2048 输出 token、最多 20 步、每个工具 60 秒、每个任务 420 秒。模型与 mini-swe-agent 的 revision 沿用原配置，runner 会核对。历史 MBPP 准备器和 runner 未修改。

准备器先验证参考实现和独立边界检查，再从预先列出的规模选择第一个参考 pytest wall time ≥1.5 秒的输入（否则取最大规模）。选规模发生在 Agent 执行之前，并保留全部校准记录；空函数负对照必须使大输入测试失败。参考实现和独立测试留在 Agent 工作目录外。

已有结果保存在 `.artifacts/d2-expansion-v1/`。复现到新目录时：

```bash
.venv/bin/python scripts/d2_service.py status
.venv/bin/python scripts/prepare_d2_expansion.py --output .artifacts/d2-expansion-replay
.venv-agent/bin/python scripts/run_d2_expansion.py --tasks .artifacts/d2-expansion-replay/tasks.json
.venv/bin/python scripts/finalize_d2_expansion.py --root .artifacts/d2-expansion-replay --output results/prediction/d2_expansion_replay
```

CPU 环境为 `.venv`，Agent 环境为 `.venv-agent`；工作路径均相对仓库根目录解析。准备和 finalize 拒绝覆盖已有输出。runner 的 `--resume` 只允许完全一致的任务、配置、代码和提示词；不要在已有 run 目录重复启动新运行。

每个 Agent 必须检查工作文件、实际修改 `solution.py`、执行完整的 `python -m pytest -q test_visible.py test_workload.py`，再单独提交。独立复测在新目录复制原始测试和保存的候选源码，执行 visible、workload、independent 三个 pytest 测试函数；每个函数可包含多组断言。

保护方式为运行协议、测试文件哈希和命令复核，**没有操作系统隔离**。命令清单见 [command-audit.json](../results/prediction/d2_expansion_v1/command-audit.json)。此次 24 条命令只涉及读取当前任务、写 `solution.py`、pytest 和提交，未发现访问其他目录、参考答案或改测试。prompt 要求读取 workload，但本轮未强制也未实际观测到独立的 workload 读取命令。

## 数据和状态接口

| 位置 | 内容 |
|---|---|
| `.artifacts/d2-expansion-v1/tasks.json` | 准备时冻结的任务、规模、测试和参考哈希、工作区位置 |
| `.artifacts/d2-expansion-v1/runs/greedy-guided/` | 实际轨迹、命令、源码、patch、run manifest |
| `.artifacts/d2-expansion-v1/evaluation-runs/` | 候选源码的独立测试日志与 JUnit |
| `.artifacts/d2-expansion-v1/frozen-round/` | 完整 journal 快照、后端日志、源码和配置快照 |
| `.artifacts/d2-expansion-v1/datasets/all-observed/` | 6 条完整可审计执行，作为主分析集 |
| `.artifacts/d2-expansion-v1/datasets/qualified/` | 5 条通过预声明质量条件的执行；存在成功筛选偏差 |
| `.artifacts/d2-expansion-v1/hazards/` | 对应风险区间和单独的行政截断视图 |
| `results/prediction/d2_expansion_v1/` | 可审阅的小型摘要、质量清单、来源和哈希 |

原有 `snapshots.jsonl` 的九字段特征白名单不变。`d2_dataset.export` 新增可选采样参数；默认仍为历史设置，本轮显式使用 `0,.01,.03,.1,.3,1,2,3,5,10,30,120` 秒，加工具开始/结束、enqueue/dispatch 事件，每 episode 最多 20 张，仅生成仍开放时刻。不同采样方案的数据不可直接比较未加权快照数。

新增状态保存为单独的 `features-expansion-v1.jsonl`，通过 `sample_id` 对齐原始快照和独立 targets；不会自动进入原 GBDT：

| 新字段 | 信息边界 |
|---|---|
| `command_category` | 当前已开始命令的 inspect/edit/test/shell 类别；不使用事后是否真的改动来判定输入特征 |
| `workload_family/work_units/work_unit` | 当前测试命令可见的预先声明工作量；其他命令保持 unknown |
| `tool_timeout_s` | 当前工具调用开始时已知 timeout |
| `prior_failed_tests` | 本 workflow 截至当前已结束且返回非零的测试次数；不等同于确定发生了 retry |
| `completed_tool_count` | 已结束工具数 |
| `last_completed_tool_s/last_completed_returncode` | 最近已结束工具的历史耗时与结果 |
| `tool_state` | 关联 episode 的工具运行中/已结束；无已观测归属保持 unknown |

投影 [d2_expansion.py](../src/leasepath/d2_expansion.py) 只读快照前事件；未来时长、未来结果、最终任务正确性和任务 ID 均不进入特征。最小接口的 `retry_count` 与服务 `queue_length` 仍保持 unknown。状态增强尚未包括并发依赖、KV 压力或完整服务队列。

## 实际结果与下一步

6 条均通过后端完整性审计，30 次实际请求对应 30 个 idle episode，U=24、D=6、自然 C=0，173 张快照。5 条通过冻结测试和执行质量条件：25 个 episode、U=20、D=5、145 张快照。图任务的候选实现在缺失起点时 `KeyError`，空行处理也遗漏；没有手工修源码或向 Agent 反馈独立答案。

本轮覆盖了短检查/编辑、秒级测试、工具结束后的间隙与短暂请求排队。6 个 episode ≥1 秒，22 张快照的 elapsed ≥1 秒。完整集快照中 `waiting_tool=75`、`queued=24`、其他 phase unknown=74；工具状态 running=75、completed=54、unknown=44。39 张当前 workload 快照有已知规模。queued 状态应单独评分，避免接近 lookup 的容易样本掩盖工具等待上的误差。

Agent 看到的六次测试全部成功，因此失败计数全为 0；独立评测失败发生在会话关闭之后，不能回填为历史失败。另外存在 6 次协议纠正：4 次初始检查顺序不符、2 次尝试交互式编辑器。这些 action 在执行前被拒绝，保存在 raw 和 [反馈清单](../results/prediction/d2_expansion_v1/protocol-feedback.json) 中，不算真实工具失败，也尚未加入增强特征。最长 idle 2.610 秒，没有 ≥3 秒或 ≥5 秒样本。C=0 来自明确关闭上下文；例如 1 秒窗口内 40 张快照仍等待，是 h0，不是自然 C。

下一小轮应优先补真实 bug 修复、失败后重新测试、不同已知规模与 3–10 秒工具，随后再补并发/依赖。变体必须与母题同组。重新训练前，应审阅新特征白名单、合并时的任务分组与 workflow 权重，并冻结验证方案。本轮到数据 review 为止。
