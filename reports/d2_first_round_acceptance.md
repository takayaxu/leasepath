# D2 首轮配置与采集实跑

日期：2026-10-30（Asia/Shanghai）。本机：`/home/ma-user/takaya/leasepath`。

**配置和真实采集链已运行成功；自主任务质量与扩大采集验收未通过。** 5 个真实 SymPy issue 共运行 100 次推理，产生可追溯的 95 个 U、5 个 D 和 456 个因果快照。5 个任务均未修复，不将这批重复循环轨迹作为足够的训练集。D2–D3 整体工作尚未完成。

配置见 [`configs/prediction/d2_first_round.json`](../configs/prediction/d2_first_round.json)，运行步骤见 [D2 部署](../docs/d2_first_round.md)，机器可读结果见 [summary.json](../results/prediction/d2_first_round/summary.json)。

## 实际运行环境

| 项目 | 实测配置 |
|---|---|
| Agent | mini-swe-agent 2.4.6，commit `04d809ceab9df28f9adaed044884180159172930` |
| 模型 | Qwen2.5-Coder-7B-Instruct，BF16，revision `c03e6d358207e414f1eca0bb1891e29f1db0e242` |
| 模型来源核对 | ModelScope 逐文件固定 revision；权重与 HF 固定 commit 的 LFS SHA256 一致，小文件与其 Git blob SHA1 一致 |
| 推理后端 | vLLM `0.23.0+empty`，vLLM-Ascend `0.1.dev4229+g7495e9d69` |
| 运行库 | torch `2.10.0+cpu`、torch-npu `2.10.0.post4`、transformers `5.5.4`、CANN toolkit `8.5.2`、driver package `25.5.1` |
| 卡与缓存 | Ascend 910B2 卡 0，TP=1，block=128 tokens，单 full-attention cache group，28 层；本轮引擎观测到 2,331 个 cache blocks |
| 服务 | `http://127.0.0.1:8201/v1`，`leasepath-qwen25-coder7b`；独立进程和插件目录 |
| 执行 | 16K context、max_num_seqs=4、batch=2048、memory_utilization=0.5；prefix caching、chunked prefill、同步调度、eager |
| 采样与预算 | temperature=0、top_p=1、seed=23、max_tokens=768；20 步/任务、900 秒/任务、60 秒/工具 |

完整参数与哈希见 [运行清单](../results/prediction/d2_first_round/run-manifest.json) 和 [后端来源](../results/prediction/d2_first_round/runtime-provenance.json)。启动时清单也包含当时离线 exporter 的哈希；后续加强了离线审计，实际加载的 `d2_probe.py`、`d2_raw.py` 未改动。导出使用的源码哈希另存于 [export-provenance.json](../results/prediction/d2_first_round/export-provenance.json)，保留原清单。

## 自主任务与质量

任务选自 `princeton-nlp/SWE-bench_Lite` test，五个 issue 在运行前固定，源码使用各自 upstream base commit。全部来自 `sympy/sympy`，在本项目中标记 `pilot_development_only`，不宣称独立测试或泛化结果。来源及源码、公开测试补丁哈希见 [任务来源清单](../results/prediction/d2_first_round/task-provenance.json)。Agent 输入不含官方修复补丁，公开测试补丁应用在独立评测目录中。

| Task | 调用数 | 用时（秒） | 补丁 | 退出 / 公开 issue 测试 |
|---|---:|---:|---:|---|
| sympy__sympy-20590 | 20 | 59.0 | 0 bytes | LimitsExceeded / 未修复 |
| sympy__sympy-21614 | 20 | 290.4 | 0 bytes | LimitsExceeded / 未修复 |
| sympy__sympy-21847 | 20 | 49.8 | 0 bytes | LimitsExceeded / 未修复 |
| sympy__sympy-23262 | 20 | 25.1 | 0 bytes | LimitsExceeded / 未修复 |
| sympy__sympy-24909 | 20 | 230.1 | 0 bytes | LimitsExceeded / 未修复 |

总任务用时约 654.4 秒。共 99 次工具调用，22 次返回非零状态；另一次模型输出没有合法 action。各任务仅有 1–5 条不同命令，存在错误路径、错误 import、重复复现，以及把修改写成注释等行为。五个 workspace 最终均无修改。工具分类是命令字符串启发式，不代表成功完成了检索、测试或构建。

在未修改源码上应用公开测试补丁时，五个目标 FAIL_TO_PASS 测试均可观察且失败；Agent 结束后仍失败。前后所选测试的计数完全一致。23262 还包含原始基线就有的额外失败，未算作本轮新增回归。详见 [baseline](../results/prediction/d2_first_round/baseline-evaluation.json) 和 [agent evaluation](../results/prediction/d2_first_round/agent-evaluation.json)。评测为本机 Python 3.12 + 公开 issue 测试，**不是官方 SWE-bench 容器评分**。

该结果只说明当前模型、提示词、预算和本机环境组合的任务质量不足，不能据此断言所有 7B/9B 模型都不适合插桩。下一轮先核对多轮行为和命令反馈，再比较同系列 14B 或调整 Agent 提示词；每个新组合单独固定配置和记录，保留本轮失败结果。

## 事件、标签与快照

| 指标 | 自主任务数据 |
|---|---:|
| 统一事件 / workflows | 913 / 5 |
| 实际后端请求 / idle episodes | 100 / 100 |
| U / D / 自然 C | 95 / 5 / 0 |
| 快照 / 独立 targets | 456 / 456 |
| 物理缓存命中请求 | 95 / 100 |
| cached / prompt tokens | 213,632 / 230,285 |
| 观测序号缺口 / 缺少请求阶段 | 0 / 0 |

U 均追到该 owner 后续实际 lookup，输入 token 前缀和兼容配置逐条比较。每个 idle 对象由当前输入内最长完整 block 对齐前缀选定，与当时实际 hash/slot/refcount 相联，不通过未来请求倒选。每个 D 均追到 runner 在预算退出后**显式丢弃上下文**的 `context_close`，而不是把步数耗尽、工具错误或缓存淘汰本身当作终止。

自然任务没有 C；runner 在任务退出时明确关闭上下文，因此最后的开放关系成为 D。受控检查另产生 U=2、C=1，不混入上表。各结局的原事件关联见 [标签示例](../results/prediction/d2_first_round/label-examples.json)。高命中率包含大量重复循环，不能当作真实目标工作负载的收益估计。

小预测窗口内无事件是已知等待 `h0`，不要求完整 episode 改标为 C。100 个 episode 在 idle 后 0.01 秒全部仍等待，0.1 秒有 41 个仍等待；456 个快照在各自 t0 后 0.1 秒有 145 个仍等待。详见 [窗口摘要](../results/prediction/d2_first_round/horizon-summary.json) 和 [后续复核](d2_quality_and_censoring_review_2026-10-30.md)。C=0 本身不构成质量失败；本轮实际限制是任务循环、工具/时长分布及明确关闭政策的代表性。

快照仅含当前白名单特征；标签和剩余时间在独立 targets 文件中，同 episode 的快照权重合计 1。456 个快照中，queue_length、retry_count 均有 456 个 unknown，is_queued 有 361 个 unknown，phase 有 199 个 unknown；未填零或从未来反推。命令类别保存在事件中，尚未进入预测特征白名单。完整缺失统计在 summary 中。

导出拒绝进程序号缺口、运行配置不一致、缺少后端阶段、enqueue/lookup tokens 不同、调度 prefill 计数不一致、抢占及异常完成；未来请求变化不影响此前快照的测试通过。全部实际请求满足 cached tokens + scheduled prefill tokens = prompt tokens。prefill 为调度计数与正常完成的联合证据，未声称设备 kernel 已独立计时。

## 受控后端检查与回归

独立受控真实推理依次观测到：cold cached=0、重复输入 warm cached=1,024、清缓存后 cached=0；最后一次 miss 仍生成 U。完整观测结束时仍开放的最后一个关系生成 C，随后 controller 才关闭上下文。结果见 [backend-checks.json](../results/prediction/d2_first_round/backend-checks.json) 和 [backend-summary.json](../results/prediction/d2_first_round/backend-summary.json)。这是机制检查，不是自然任务分布。

项目全套 **76 项 pytest 通过**，CPU/Agent 环境 `pip check` 通过；D1 50 项检查继续通过，事件契约 8 个正例接受、16 个反例拒绝。D1 和事件契约仍属于合成证据，与本次实测数据分别报告。

日志 Writer 的局部微基准为 1,000 条、每条 4,096 token 的记录：emit P50 3.14 μs、P95 4.731 μs；提交并完整 flush 总计约 1.256 秒，输出约 19.5 MB。后台首遍编码 P50 约 601 μs。此基准排除实际探针 token/block 拷贝、后端包装逻辑、设备和端到端影响；首遍编码也不包含第二遍编码与写盘，不能用这些局部数字宣称完整探针开销达标。原始数字见 [observer-benchmark.json](../results/prediction/d2_first_round/observer-benchmark.json)。

## 本地数据与未完成范围

完整自主数据：`.artifacts/d2/pilot-dataset/{events,episode_labels,snapshots,targets}.jsonl`。受控数据：`.artifacts/d2/backend-dataset/`。冻结日志及配置：`.artifacts/d2/frozen-first-round/`；冻结 raw SHA256 为 `b7199087f3071d64b678daec5e9db0b23e4f16c8fda593fb2897b08239464460`。完整轨迹、评测日志和模型权重保存在本机忽略目录；Git 保留小型结果与来源清单。

尚未覆盖有效编辑/测试/构建的足够多样性、多来源独立切分、真实并行依赖、全量块分配/淘汰生命周期、完整采集开销对照、预测器训练和在线策略收益。自然观测截断场景也未覆盖，但不要求每批数据必须含 C。**暂不扩大到 60–100 workflows，也不将本轮视为 D2–D3 整体完成。**
