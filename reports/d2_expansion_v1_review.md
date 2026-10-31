# D2 数据扩展 v1 review

2026-10-31，状态：采集、冻结、独立评测和因果状态导出完成，等待本部分 review。此轮没有重训 GBDT。

新增六个不同母题：CSV 汇总、JSONL 日志统计、SQLite 查询、gzip 校验、整数文件排序、图最短路。它们是本项目设计的任务和生成输入，实际 Agent、工具调用和 KV lookup 均为实测。旧 MBPP 的十个母题、已有预测结果与冻结 raw 未覆盖。配置、运行命令及具体路径见 [说明](../docs/d2_expansion_v1.md)。

| 指标 | 全部可审计执行 | 通过冻结质量条件的子集 |
|---|---:|---:|
| workflow / 不同母题 | 6 / 6 | 5 / 5 |
| 真实请求 / idle episode | 30 / 30 | 25 / 25 |
| U / D / 自然 C | 24 / 6 / 0 | 20 / 5 / 0 |
| 快照 | 173 | 145 |
| ≥1 秒 episode | 6 | 5 |
| ≥1 秒 elapsed 快照 | 22 | 19 |
| 最长工具 / 最长 idle | 2.574 / 2.610 秒 | 2.574 / 2.610 秒 |

六次真实大输入测试调用分别耗时 1.705–2.574 秒，包含输入准备和验证，没有 sleep 或补时。旧 MBPP 完整主执行最长 idle 为 0.751 秒；本次增加了明确的秒级风险记录。但所有新长调用仍是 pytest，尚不能称为已覆盖不同原生工具、慢构建、网络请求或生产负载。

6/6 后端请求链均通过序号、时钟、token 前缀、兼容配置、实际 lookup/prefill/释放块审计。5/6 通过事先冻结的 visible/workload/independent 测试及执行质量条件。图实现仅在简单链上成功，独立测试遇到不存在的起点产生 `KeyError`；候选还未跳过空行。保留原始源码、失败日志和所有合法 U/D，不作手工修复。

新增十个因果状态字段单独导出为 `expansion-v1`。当前命令类别、已知输入规模、timeout、已结束工具历史均能追溯；未来返回码和完整耗时不会提前出现。原 GBDT 的九字段接口不变。当前完整集有 75 张 running、54 张 completed、44 张未知工具状态；phase 中 75 张 waiting_tool、24 张 queued、74 张未知。工作量字段已知 39/173，其余未知；这些是快照覆盖率，不是独立样本量。

本轮的不足应直接用于下一轮设计：

- Agent 看到的测试没有失败，`prior_failed_tests` 全为 0，无法学习失败/修正差异。会话结束后的独立失败不得倒填。
- 有 6 次真实协议纠正（初始检查顺序 4 次、交互编辑器 2 次）；这些动作未执行，不能计为工具失败。raw 和 [反馈清单](../results/prediction/d2_expansion_v1/protocol-feedback.json) 保留全部记录，当前特征尚不编码此类反馈。
- 没有 ≥3 秒的 episode；3–10 秒和长尾仍空缺。六类各只有一个大输入规模，规模与任务类别混杂。
- 串行单工具，没有真实 fanout/join、工具并行或排队拥塞；当前短暂 queued 应分层报告。
- 5/6 表示冻结测试和质量条件通过，不等于证明实现满足所有语义。比如 SQLite 的显式连接关闭并未由当前测试检查，候选使用连接上下文管理器，仍需资源生命周期专项检查。
- 工作目录外保存答案、哈希和命令复核不等于 OS 隔离。已审阅 24 条命令，未见访问答案或篡改测试；也未观察到 Agent 显式读取 workload 文件。
- C=0 符合显式关闭关系的协议；行政截断另存。在 1 秒窗口，40/173 张快照已知仍等待；不能叫自然 C。

自动化回归 **105 项通过**，包括新增的秒级采样边界、episode 权重、未来结果不泄露、只在 tool end 更新历史和跨关系不倒推当前工具的检查。另对 318 张完整/合格快照逐一只用已观测 raw 前缀重建增强字段，结果全部一致；核对了 24 对工具 start/end、文件哈希和历史 MBPP raw 未变。完整证据见 [verification](../results/prediction/d2_expansion_v1/verification.json)、[summary](../results/prediction/d2_expansion_v1/summary.json)、[全量统计](../results/prediction/d2_expansion_v1/all-observed-summary.json)、[质量清单](../results/prediction/d2_expansion_v1/execution-quality.json) 和 [SHA256SUMS](../results/prediction/d2_expansion_v1/SHA256SUMS.json)。

建议下一部分补 3–10 秒、同类不同规模和真实 bug 修复后的失败/重测状态，再冻结扩展特征和母题分组方案；之后才重新比较经验基线与 GBDT。此次数据适合扩展链路与秒级分布开发，尚不足以支持泛化能力或在线收益结论。
