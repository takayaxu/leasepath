# GitHub 同步记录：2026-11-12

目标仓库：https://github.com/takayaxu/leasepath 。当前服务器开发目录为 `/home/liuxv/LeasePath-new-server-data`，分支 `codex/new-server-data`；`/home/liuxv/LeasePath` 是另一个停留在早期初始化版本的 checkout。

本次把当前开发分支提交并推送。同步前 HEAD 为 `096d6c7fe652f128fa85980328e76fe7f99d9e36`，已包含远端 `main`（`66a34c8eb618f4849153cb3c8d72880362e8e387`）且有 51 个尚未发布的后续提交。同步采用正常分支推送，远端 `main` 仍是独立分支。

## 随仓库保存

- 连续 agent 工具、反馈协议、数据质量修复、预测特征、经验/GBDT 训练和动态 refit、容量压力及端到端对照脚本和已有测试。
- 独立任务、连续会话、压力试验及工作负载分析的派生汇总、审计报告和配置。
- 小型预测模型、初始训练观测、验证预测、动态模型版本和训练时间线。
- 论文各版 Markdown/TeX/PDF、原创矢量图及生成脚本、Lucide 图标与许可、源码包、训练契约、后续试验计划。

## 留在服务器的文件

沿用 README 对大型原始数据的分发约定，在 `.gitignore` 中明确排除 `results/agent/**/raw/*`，但保留其 `training/` 小型模型与时间线；另排除重复 `verified-view/`、`native.strace*`、运行锁和下载的设计参考网页/图片。

当前 agent 目录被忽略的文件共 3,001 个、6,738,519,056 字节，均保留在本机。部分原始 backend JSONL 为 0.5–0.66 GB，超过 GitHub 普通 Git 的 100 MiB 单文件限制。模型权重、原始后端 token 日志和工具工作目录仍需从服务器恢复，Git 克隆不是完整原始证据备份；论文的原始来源 SHA256 清单及已完成的审计记录随仓库保存。

## 推送前验证

新增的 17 个测试文件，在已驻留的实验运行时与现有 pytest 环境下执行，共 **55 passed（37.26 秒）**。包含工具状态特征、GBDT 往返与未知工具、未来结果删失、动态更新门槛、原生请求 ID 关联、真实工具修复和来源 I/O 记录。

普通 `.venv` 的先行全量检查命令为 `python -m pytest -q -k "not full_pinned_audit"`，结果 **384 passed、15 failed、2 errors、1 skipped、1 deselected（336.18 秒）**。其中普通环境缺少 LightGBM/Qwen-Agent/threadpoolctl/DuckDB 等依赖；还存在旧 shell 观测、资源驻留和固定时间窗口等失败。新增测试已使用含依赖的实际实验运行时重新验证，全部通过；本次同步没有把先行全量检查表述为通过。完整历史套件没有在该运行时再跑一遍。

实际实验运行时：`/dev/shm/leasepath-preexec-v2-20261110/runtime`；LightGBM 4.6.0 来自本地 `.artifacts/prediction-holdout-deps`；pytest 9.2.0 来自 `.venv`。训练与模型文件没有因同步重新生成。论文构建已有 19 页、0 TeX 溢出、0 文字越界的检查记录，相关页面已目视审阅。
