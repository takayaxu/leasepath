# 下一阶段执行记录（2026-11-10）

冻结的主基线仍为 `gbdt-distribution-equalcost-H30-tau10-v1`。等成本分布的 H=30 秒、τ=10 秒、128-token/32 MiB block、shared-owner max、合法叶淘汰、retired-first LRU 回退和 FCFS 均未改变。压力实验不重新训练预测器，不加入成本权重，不调整 tau，也不重排请求。

## 压力矩阵

矩阵是 24/32/64 block × 3/6/10 并发，比较 retired-first LRU、GBDT 3 秒点预测和冻结等成本分布。每个 cell 使用完整 6 种策略排列；每个并发档使用前 N 个已知开发题。旧 24/3 结果不与新 24/3 锚点混平均。

已版本化配置：

- `configs/prediction/qwen_distribution_pressure_2026-11-10.json`
- `configs/prediction/pressure_cells_v1/capacity-{24,32,64}-concurrency-{3,6,10}.json`
- `src/leasepath/pressure_matrix.py`
- `scripts/qwen_cache_pressure_batch.py`
- `scripts/run_qwen_distribution_pressure.py`

10 并发使用 NUMA1 的 10 个不同物理核及 SMT 兄弟线程，每个工具数值线程固定为 1；模型线程固定在 NUMA0 的 8 个 CPU。共享服务器上 affinity 不是独占保证，所有 PSI、运行队列、GPU tenant 和内存快照保留。

### 预检结果

CPU/工具预检实际执行 3、6、10 并发，共 38 次真实工具调用，答案 3/3、6/6、10/10 全部正确，父进程 `read_bytes=0`、major fault=0、block input=0，worker 磁盘 `read_bytes=0`。配置里所有实际 prompt 均要求容量门槛检查，超过容量会拒绝并记录。

驻留轮在 10 并发的 `rank_sum_test` 有 1 次 worker lifetime major fault，因此严格零缺页门槛没有通过；没有启动 GPU/JCT，没有替换样本。该轮结果保存在 `/dev/shm/leasepath-pressure-20261110/tool-preflight-v2`，直到归档前只作为失败门证据。

此前一次预检还发现已冻结旧字节码文件的 hash 变化，记录在 `/dev/shm/leasepath-pressure-20261110/tool-preflight/summary.json`，没有生成模型或 JCT。源码和输入 hash 没有因此被改写。

因此本阶段的 GPU/JCT 状态是：**0 个新模型 generation，0 个新 JCT cell**。只有在重新登记并通过完整 controller/worker residency 门后，才能启动第一格 24/3。

## 独立母题与真实循环

已建立 12 个新来源族的本地小实例和独立参考解：6 个 development（job shop、SAT、package resolution、max flow、exact cover、有限域纠错）与 6 个 holdout（bin packing、automata reachability、access control、reaction balance、instant-runoff ballot、edit distance）。它们不复用旧 50 个 parent ID 或 source_group；按 source family 整体切分，旧题不是通过改 seed、规模、措辞或 operation 名称变成新题。

实现文件：

- `src/leasepath/independent_agent_tasks.py`：模型可提交候选，工具只返回真实约束违反和候选目标值。
- `src/leasepath/independent_agent_reference.py`：单独实现参考解；不导入工具 evaluator。
- `src/leasepath/independent_agent_protocol.py`：模型自行选择 inspect、candidate evaluation、修订或停止；调用反馈放入下一次真实 prompt。
- `src/leasepath/independent_agent_worker.py`：候选检查的有界子进程。
- `scripts/qwen_cache_independent_agent_batch.py`：真实 Agent batch 入口；最多 12 generations、11 tool calls、240 秒工具、1800 秒 workflow。

参考解、错误答案、可行但次优答案、独立 evaluator、反馈消费和两次实质修订证据测试针对性测试最终 **41 passed**，此前全量测试 **299 passed、1 deselected**。这只是协议和参考正确性验收，不是 Qwen 模型循环或 JCT 结果。

来源页面和 hash 位于 `.artifacts/independent-agent-v1-20261110/provenance/`。部分站点连接失败或被 403，失败记录保留；它们不被宣称为已核验来源。可取页面仅用于记录问题语义，实例数据由本地固定 fixture 独立生成。

真实模型试跑尚未启动。下一道门是：对新 runner 做完整文件追踪和依赖驻留，固定每个新任务的输入、参考、checker、工具控制、prompt、split 和 ledger；通过后只先跑 development 3 并发/32 block 协议试点。留出组在协议冻结前不进入模型。

## 不可宣称的内容

当前没有新的压力 JCT、容量收益、并发收益或新题泛化结果。共享服务器上的一次 major fault 足以阻止本轮自然 JCT；不能把 38 次正确工具调用当作 GPU 性能验收；行为检查单独标 `behavioral_loop_supported`，只有原生输出链接独立通过后才能给出 `loop_certified`。

最终报告及归档状态见 `reports/pressure_independent_stage_2026-11-10.md` 和 `results/cache_policy/kv_distribution_pressure_2026-11-10/delivery.json`。

原始 tmpfs 已在逐文件校验归档后清理。完整追踪、runtime、输入与失败证据在 `.artifacts/checkpoints/pressure-independent-2026-11-10/raw-evidence.tar.gz`；可读概要在对应 `results/cache_policy/kv_distribution_pressure_2026-11-10/`，原始摘要另外保留于 checkpoint 的 `accessible-summaries/`。
