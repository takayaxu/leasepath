# TraceLab 数据采集与适配审计

日期：2026-10-29（UTC）。

## 结论

TraceLab v0.0.2 已完整下载并固定，但**对 LeasePath 主任务不可用**。它缺少兼容前缀身份、服务端 cache lookup 和显式 retire；既不能生成精确 U/D/C，也不能检验真实 KV 缓存收益。适配器的工具等待样本只可留作辅助分析。此前把它描述成“项目可用训练集”不准确；主任务可用样本数仍为 0，D2–D3 未验收。

## 来源与复现

- 上游：[uw-syfi/TraceLab](https://github.com/uw-syfi/TraceLab)，tag v0.0.2，commit 61fea8f97277d0aa247cc2e4c7e31f90ed2bea1c。
- 官方发布文件：syfi_coding_trace.jsonl.gz，100,939,722 字节，SHA-256 为 11ce51ec0a25e3d1d95b025bca2f7d1647e47571eb7cc968acd5fc64d4b4fb65。
- 数据许可：[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。引用 [TraceLab（SyFI Lab, University of Washington）](https://tracelab.cs.washington.edu)，注明本项目的派生处理。
- 上游脚本只作为参考材料保存和阅读，没有执行。

在项目根目录重建：

~~~bash
python3 scripts/prepare_tracelab.py --offline
~~~

删掉 --offline 时，若固定压缩文件不存在，命令会从官方 GitHub release asset API 下载；先验证固定字节数和 SHA-256，再适配数据。已有文件只验证，不会静默覆盖。

## 数据规模

全量扫描共 665,453 个 Agent 轮次、743,819 个工具调用、8,058 个不同 session ID；按用户、provider、project、session 作用域为 8,059 组，52 个匿名用户，356 个非空 project 值。Claude 与 Codex 分别有 305,445 和 360,008 个轮次。

769 个调用缺少结果时间，12,245 个日志跨度为零；内部 runner latency 有 398,296 个缺失、136,408 个零值。上游累计 token 恒等式在 665,453 行均成立。切分行数和快照数以 [summary.json](../data/processed/tracelab/v0.0.2/summary.json) 为准。

tool_wall_latency_ms 与 result_at - emitted_at 来自同一套归一日志字段，二者相符只能验证算术一致，不能证明它是 runner 独立测得的执行时长。tool_internal_latency_ms 缺失率高、语义不同，只留在标签归档中，不能与 wall span 混成一个目标。

## 预测单位与字段

归档中的一条基本样本是一次工具调用。训练读取器把有效、正时长调用展开为固定等待时间快照：0、1、5、30、120 秒，仅保留拍快照时仍在等待的点。

例如调用日志跨度为 8 秒，就生成 elapsed 为 0、1、5 秒的三个样本。X 是 provider、工具名、轮次/调用序号、脱敏输入字符数、上游命令解析类别和 skeleton、当前已等待秒数。Y 是该快照之后到日志结果时间的剩余秒数：8、7、3 秒。三个样本总权重为 1。时间点预先固定，没有依据测试集调参。

每个切分有三个按 sample_id 逐行对齐的压缩文件：

| 文件 | 内容 |
|---|---|
| train.features.jsonl.gz（validation/test 同理） | 输入白名单与匿名关联元数据。仅内层 features 是 X。 |
| train.labels.jsonl.gz | 结果跨度、错误、runner 状态等事后结果。 |
| train.quality.jsonl.gz | 缺失、不一致、零值和代理资格标记。 |
| sessions.jsonl | 用户/provider/project/session 作用域及切分。 |
| summary.json | 全量计数、切分组、特征白名单、checkpoint 和来源/输出哈希。 |

训练读取示例（在项目已安装或 PYTHONPATH=src 时运行）：

~~~python
from leasepath.tracelab import iter_training_samples

for sample in iter_training_samples("data/processed/tracelab/v0.0.2", "train"):
    X = sample["features"]       # 只把这个对象交给模型
    Y = sample["label"]["remaining_s"]  # 未来的剩余日志跨度（秒）
    weight = sample["weight"]    # 每个调用的快照权重合计为 1
~~~

缺少结果时间或时间差不一致的样本保留在归档中，不导入训练快照。零时长保留并标记，训练读取器默认排除，不补成正数。它们不构成精确 KV 的 C 或 D。

上游 command_status 属于将来才知道的标签，不能放入 X。Codex running 表示该次 exec_command/write_stdin 返回时命令仍在运行，当前目标是这次工具交互的响应跨度；没有把它当作整条后台命令的完成时间。相关调用保留 continuation_of_tool_call_id 供后续链级分析。

当前未导入 prefix/cache token 数，因为它们可能由轮次结束后的 usage 回填；计数也无法证明前缀相同。历史、依赖、服务队列和 KV 压力没有补零或猜测。另一个候选目标“下一轮 Agent 首次输出”会混入服务/生成延迟及日志边界不确定性，本次先固定工具返回这一较易核查的代理。

## 切分与隔离

用户只进入一个集合；任一具名 project 或同 provider/session 被多个用户共享时，相关用户连成同组。空 project 不会把不同用户连在一起。按工具行数从大到小，将完整组放入距目标行数缺口最大的集合，目标为 train/validation/test = 80/10/10。seed 20261029 只决定相同大小组的稳定顺序。这是确定性的用户/项目分组切分，不是随机逐行切分或时间外推评估。完整组映射写入 summary。

| 切分 | 工具记录 | 正时长可训练调用 | 默认展开快照 |
|---|---:|---:|---:|
| train | 595,081 | 582,621 | 934,791 |
| validation | 74,369 | 74,122 | 114,274 |
| test | 74,369 | 74,062 | 115,842 |

默认合计 1,164,907 张快照，来自 730,805 条正时长调用。共有 51 个独立切分组。

## 验证边界

原始文件与官方 SHA-256 相符；完整读取验证 gzip/JSONL；通过时间差核对、样本逐行对齐、用户/项目/session 隔离与特征白名单检查。未来结果改写测试检查 X 不受影响。这些证据验证的是公开数据适配与隔离，不能验证上游采集事件是否完整。全套 Python 测试共 67 项通过，其中新增 8 项针对本适配器。机器验收记录见 [verification.json](../results/prediction/tracelab_audit/verification.json)。另用实际训练读取器遍历全部 1,164,907 张快照，核对各集合调用数、快照数以及逐调用权重合计为 1，记录于 [training_reader_check.json](../results/prediction/tracelab_audit/training_reader_check.json)。

这批数据只可用于工具响应日志跨度的辅助分析，不应用于主任务基线或模型训练。主任务需要在实际推理服务/调度器中采集同一 episode 的前缀身份及兼容配置、真实 lookup（含 miss）、显式 retire 和完整 observation end，并在同一运行中记录策略动作、物理 KV 占用、miss、JCT 与预测开销，才能报告预测准确率和缓存收益。本次没有训练主模型。
