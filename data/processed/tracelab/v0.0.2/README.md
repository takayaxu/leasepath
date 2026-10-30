# TraceLab：LeasePath 工具等待代理数据集

固定版本 v0.0.2；原始数据位于 data/sources/tracelab/v0.0.2/。

在项目根目录运行 python3 scripts/prepare_tracelab.py --offline 可重建；追加 --verify-only 可只做完整核验。

**用途边界：仅供工具等待时间辅助分析；主任务可用 U/D/C 标签为 0。禁止把此目录用作精确 KV 复用模型训练集或缓存收益验收数据。** 这里的代理目标是在工具仍未返回的快照时刻，预测到结果日志出现还要多少秒。

| 切分 | 工具记录 | 正时长可训练调用 | 默认展开快照 |
|---|---:|---:|---:|
| train | 595,081 | 582,621 | 934,791 |
| validation | 74,369 | 74,122 | 114,274 |
| test | 74,369 | 74,062 | 115,842 |

每个切分的三个 .jsonl.gz 文件按 sample_id 逐行对应：features 是输入、labels 是未来结果、quality 是离线质量标记。模型只能接收内层 features，不能接收外层元数据、labels 或 quality。

默认训练读取器：leasepath.tracelab.iter_training_samples。固定快照点为已等待 0、1、5、30、120 秒，只取仍在等待的点；每次调用的快照权重合计为 1。缺失结果和零跨度不进入等待快照。匿名用户及共享具名项目/session 的连通组全程隔离；sessions.jsonl、summary.json 记录分组与哈希。

来源：[TraceLab（SyFI Lab, University of Washington）](https://tracelab.cs.washington.edu)，许可 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。LeasePath 改编包括质量标记、输入/标签分离、分组切分和快照展开。

**此数据没有精确 KV 的 U/D/C 标签。** 工具结果日志跨度不能代替合法 cache lookup 时间，也不能用于声明真实缓存收益。Codex running 是一次交互结果，不是整条后台命令的完成。详细说明见项目 reports/tracelab_dataset_audit_2026-10-29.md。
