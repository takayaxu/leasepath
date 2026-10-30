# CacheWise trace 采集与可用性审计

日期：2026-10-29（Asia/Shanghai）。

**结论：已完整采集，可以用于工具日志跨度的代理预测和基线代码核验；不能直接替代本项目的精确 KV 复用 U/D/C 训练数据。**

## 采集了什么

来源为[用户指定仓库](https://github.com/cachewise-project/cachewise-coding-traces)，固定在[提交 181c435](https://github.com/cachewise-project/cachewise-coding-traces/tree/181c435a090d328d00bbbee4c8eeb27d32f3abd2)，不依赖后续变化的 main。

完整下载保存为 [data/sources/cachewise/upstream/](../data/sources/cachewise/upstream/)，共 **320 个文件、57,895,374 字节（约 57.90 MB / 55.21 MiB）**。[provenance.json](../data/sources/cachewise/provenance.json)记录下载地址、固定提交、压缩包和逐文件 SHA-256。上游脚本只阅读，未导入或执行。

README 中的 `release/` 前缀与仓库实际目录不一致。此提交实际提供顶层 `raw_traces/` 和 `parsed_traces/`，同时提供派生训练表与预测代码。没有 GitHub release，使用提交快照固定来源。

| 实际盘点 | 数量 |
|---|---:|
| 脱敏项目 | 11 |
| 会话文件 | 94 |
| 非空会话 / 空会话 | 81 / 13 |
| 原始 JSONL 记录 | 35,127 |
| 解析事件总数 | 32,251 |
| LLM 事件行 | 20,634 |
| 按会话去重后的 message ID 数 | 11,453 |
| 工具事件行 | 11,617 |
| 按质量规则保留的代理候选 | 9,393 |

LLM 事件行包含一条响应的多段日志和历史复制记录，**不是 20,634 次独立模型请求**。message ID 数也只是去重标识数，不能保证每个都是真正独立请求。上游 manifest 的会话数与实际文件一致，解析事件数与 manifest 计数一致。

`project_006` 占 55 个会话、8,693 条工具事件，约占全部工具事件的 74.8%。它是明显的主要来源；后续结果应按项目分组和加权报告，不能只看逐行平均误差。

## 与当前预测任务怎么对应

| 所需信息 | 此集合是否提供 | 可以做什么 |
|---|---|---|
| 工具名、会话、项目、调用/结果关联 | 有，但需处理重复 ID | 按工具和项目分析日志跨度，构造代理样本。 |
| 工具调用与结果时间 | 有日志时间和派生差值 | 观测日志跨度；不能称为独立 runner 测得的纯工具执行时间。 |
| 工具原始参数、命令族、输入规模、timeout | 参数全部脱敏 | 不能完整重现参数 TF-IDF 基线，也不能把占位符长度当作真实输入长度。 |
| 最近已完成工具历史 | 可以从合适的日志子集重建 | 需要按完成时间因果更新；本次尚未生成 elapsed 多快照或历史特征。 |
| retry、sidechain、compaction | raw 中有部分信息 | 是可继续审计的增强信号，不代表完整阶段或依赖图。 |
| 合法 token 前缀、兼容配置、owner–前缀关系 | 没有可独立验证的完整内容 | token 计数或 cache_read 计数不能代替合法性校验。 |
| 服务端 enqueue、dispatch、cache lookup、KV 压力 | 没有 | 不能直接生成 lookup 时间标签或队列状态特征。 |
| 明确 retire、统一观察结束边界 | 没有本项目所需事件 | 不能把 session 最后一行自动当成 D，也不能把缺失结果直接当成精确 C。 |

本次统一标为 `endpoint=tool_result_log_span_proxy`，与 `cache_lookup` 目标分开。可以先研究“已知工具类别，在日志时间坐标中还要多久观察到结果”；要声称预测了 KV 下一次合法需求，还需要自采或其他带前缀与 lookup 的数据。

## 发现的问题及处理

**参数不可用。** 11,617 条工具输入均为 `{"content":"<redacted>"}`，可用 command 为 0。上游自己的预测 README 也承认这一限制。不能据此宣称完整复现原论文参数聚类，更不能使用脱敏内容猜测测试规模。

**工具时间不是独立的执行探针。** 上游 `event_parser.py` 把 assistant 工具调用日志的时间与 user 工具结果日志时间相减，得到 `execution_time_ms`。它包含日志、编排与等待语义，未给出实际工具开始/结束的独立测量。现有非缺失时长与发布的两端时间差一致，只能证明内部算术一致。

**复制与重复关联。** 存在 84 组重复工具 ID，涉及 168 条工具事件；raw 中也有 84 组重复结果 ID。上游解析器先用 ID 建全局结果字典，后出现的同 ID 结果会覆盖早期结果。例如 `session_0051/tool_00001`，parsed 早期跨度为 866.820 秒，而 raw 中早期结果在很短时间后出现，同 ID 历史记录又在后面被写入。当前程序不尝试猜测修复，重复或结果不唯一的记录全部标记隔离。

**零值、缺失和长尾。** 1,762 条时长为 0，3 条缺失；不把零值补成任意正数，不把缺失补成 0。源数据中 460 条跨度至少 60 秒、26 条至少 1 小时，最长约 49.49 小时。长跨度仍保留，无上界裁剪；可能混有等待、会话恢复和记录问题，不能直接解释为工具持续运行。

**派生 HumanPause。** `events_with_pauses` 和 `all_tool_calls.json` 加入 684 条 HumanPause。这些由相邻 LLM 日志间隔推断，时间源于日志，但“人暂停”的语义是后处理赋予的，不是一次实际工具调用或独立用户行为探针。本次没有将其计入工具代理候选。

**未来字段泄漏风险。** 上游派生表的 12,298 行都带 `session_progress=event_index/最终总事件数`，运行时无法知道分母。不能直接拿整张派生表做在线模型输入；它不意味着上游模型一定使用了该列。本次只导出工具名和 `elapsed_s=0` 的最小输入，把时长、结果和错误放进标签文件。

**上游切分不满足项目隔离。** 提供的 34 个 train session 与其补集中有 4 个项目重叠：004、006、007、008。因此不能把该切分当作“未见项目泛化”实验。本次导出 project/session 清单，未拟合模型或冻结新切分；后续需按项目隔离，且不能仅靠脱敏后的跨会话日期证明独立自然时间切分。

**时间坐标限制。** 日志日期显示在 2020 年，而模型名含 2025 年版本，不能把这些日期当成原始真实采集日历。仓库未提供可核验的原始日期映射和全部脱敏过程。会话内差值可作为公开日志代理使用，跨会话的自然到达、实际并发负载和时间漂移仍缺证据。

**许可状态。** 固定快照中未找到 LICENSE/COPYING 等许可文件，GitHub 仓库元数据也未声明许可证。公开可下载不等于已明确开源或数据再分发授权。本次完成本地来源采集与审计，未向外发布数据；许可状态记录为 `not_found`。

## 导出了什么

完整结果在 [results/prediction/cachewise_audit/](../results/prediction/cachewise_audit/)：

| 文件 | 内容 |
|---|---|
| [summary.json](../results/prediction/cachewise_audit/summary.json) | 全量计数、分位数、工具类别、质量标记、来源及输出哈希。 |
| [sessions.jsonl](../results/prediction/cachewise_audit/sessions.jsonl)、[projects.json](../results/prediction/cachewise_audit/projects.json) | 每个会话和项目的覆盖、缺失与候选数，供后续分组切分。 |
| [features.jsonl](../results/prediction/cachewise_audit/features.jsonl) | 全部工具行的 sample ID、关联元数据及最小输入。只有内部 `features` 可作为模型输入，外层 ID/时间用于对齐。 |
| [labels.jsonl](../results/prediction/cachewise_audit/labels.jsonl) | 对应的日志跨度、结果时间、错误；精确 KV outcome 始终为 null。 |
| [quality.jsonl](../results/prediction/cachewise_audit/quality.jsonl) | 同一 sample ID 的候选资格和排除原因。质量标记用于离线选样，不是在线输入。 |

三份逐工具文件各保留全部 11,617 行，按 sample ID 一一关联，不静默删除源记录。候选条件为：raw/parsed 的调用与结果唯一、时间匹配、时长为正、不属于 sidechain 或 synthetic。得到 **9,393 条候选**，不等于 9,393 条经人工证明纯净的真实工具执行样本；长尾和日志语义问题仍需开发阶段审查。零值子集未来可以单独研究，本次只是从正时长代理候选中排除，不声称它们全是错误。

全部非缺失源跨度中位数为 0.088 秒、P90 15.339 秒；候选子集中位数 0.117 秒、P90 19.101 秒、P99 248.860 秒。没有在采集阶段按照最终预测效果选择阈值，也未借用未来标签训练。

## 复现与检查

在项目根目录运行：

```bash
python3 scripts/collect_cachewise.py
python3 scripts/collect_cachewise.py --offline
python3 -m pytest -q tests/test_cachewise.py
```

首个命令在快照缺失时下载固定提交，并检查预期压缩包 SHA-256；已有快照时校验全部文件。`--offline` 不访问网络。审计只依赖本项目 Python 模块，不安装或执行上游 requirements/脚本。

测试覆盖重复结果覆盖风险、重复工具 ID、零时长、缺失结果不变成 D/C、改变未来结果不改变当前最小特征、源文件篡改拒绝，以及全量两次审计一致性。另在独立临时目录重新下载固定提交，核对全部 320 个源文件和 5 个导出文件哈希。

本次实际验证：新增的 **6 项数据审计测试通过**，当前项目全部 **59 项测试通过**；独立重新下载的 320 个源文件与全部 5 个审计导出哈希一致。README、架构说明与本报告的本地链接检查通过。

这完成了 D2–D3 中的一个公共来源采集与字段审计，不表示真实 pilot、完整预测数据集或模型评估已经完成。
