# CacheWise 固定来源快照

来源：[cachewise-project/cachewise-coding-traces](https://github.com/cachewise-project/cachewise-coding-traces)。
采集日期：2026-10-29（Asia/Shanghai）。
固定提交：`181c435a090d328d00bbbee4c8eeb27d32f3abd2`。

## 目录

- `upstream/`：该提交的完整原始快照，320 个文件、57,895,374 字节；不修改、不执行其中脚本。
- `provenance.json`：提交、下载地址、压缩包 SHA-256 和每个文件的 SHA-256。
- 本文件：LeasePath 的本地说明，不属于上游仓库。

上游 README 写的是 `release/raw_traces/`，该提交实际路径为顶层 `upstream/raw_traces/`，相应解析数据是 `upstream/parsed_traces/`。

## 能用到什么程度

11 个脱敏项目、94 个会话文件（81 个非空）、11,617 条工具事件。可用于工具类别条件下的**日志观测跨度代理**，不能直接作为下一次合法 KV lookup 的精确 U/D/C 数据。参数、命令、文本内容被脱敏；日志没有服务端 lookup、合法前缀、retire 或队列记录。

上游 `tool_duration_prediction/datasets/` 包含插入推断 HumanPause 后的派生数据；其中的 `session_progress` 使用未来总事件数，不能作为因果预测输入。当前导出从基础 `parsed_traces/` 读取，与 raw 日志核对，不使用上述派生表作为训练输入。

未发现 LICENSE/COPYING 或明确数据许可，不能把公开可下载自动解释为已有开源授权。本次仅保留来源快照和审计结果，未向外发布数据。

## 复现

从项目根目录运行：

```bash
python3 scripts/collect_cachewise.py
```

本地快照存在时验证哈希并重新审计，不重复下载。只允许本地数据、禁止联网时：

```bash
python3 scripts/collect_cachewise.py --offline
```

审计输出在 `results/prediction/cachewise_audit/`，详细字段及结论见[审计报告](../../../reports/cachewise_dataset_audit_2026-10-29.md)。
