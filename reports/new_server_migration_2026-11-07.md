# 新服务器迁移与 12 题真实试点

本轮已完成固定基线迁移、32B 真实后端边界验收、Qwen-Agent 两题多轮门槛和一次冻结 12 题主试点。主试点 **12/12 完整成功**，预定短/中/长各 **4/4**，独立封口审计及质量复核通过。实际主要计算也为 4/4/4；36 个 episode、106 张因果快照，U=24/D=12/C=0。使用当前服务器已有资源，没有联系华为云、购买资源、启动预测器训练、在线策略或 JCT 实验。

完整工作区 `/home/liuxv/LeasePath-new-server-data`，分支 `codex/new-server-data`；原 `/home/liuxv/LeasePath` 保持原状态。交接文档副本在 `.artifacts/migration-baseline/NEW_SERVER_HANDOFF.md`。本报告路径均相对于新工作区。

## A：基线与实际资源

唯一基线 `66a34c8eb618f4849153cb3c8d72880362e8e387`，tree `95926ec30c5b6e362530b8c34f34a06eabd4a7e0`。Git HTTPS POST 不通，使用 GitHub API/codeload 恢复确切 tree 和原 commit 对象，SHA 精确一致且 fsck 通过；祖先未下载，`.git/shallow` 明确边界，没有假造替代提交。

v100-server：Ubuntu x86_64、56 逻辑 CPU、约 503 GiB RAM、约 5.6 TiB 可用磁盘、10 张 V100 PCIe 32 GiB。CUDA 12.4 的 sm_70 小内核实测通过；系统 PyTorch 2.11 无 sm_70，实际计算失败，因此不用其推理。Docker/NVIDIA runtime 和 user namespace 探测通过；不将这些探测说成工具 OS 沙箱。环境/端口/进程/权限/网络见 `results/migration/new_server_2026-11-07/environment.json` 与 `.artifacts/migration-baseline/environment-full.json`。

本轮只用 GPU 5/6/7；0–4 的其他负载和原有 9B 服务保持。每次加载重新检查所选 GPU 空闲。实际峰值显存 22,346/22,324/21,850 MiB；退出后均回到约 4 MiB。batch-d 的采样估计每张卡非空闲约 792 秒，共约 0.66 GPU·小时，包含加载和协议/工具等待，不是 profiler 计算时间，也不含早期探针。完整资源采样覆盖更早阶段。共享主机负载约 49–56，无独占 CPU/磁盘保证；没有停止他人任务。

基线适用回归 174 passed/1 skipped/1 deselected；新增路线全适用回归 199 passed/1 skipped/1 deselected。排除 `full_pinned_audit` 是外部 CacheWise 快照未随 Git 恢复；不能记为全套通过。D1 50 项、事件契约 8 正例/16 反例通过。最后协议格式调整后工具/审计相关 23 项通过，官方 parser/stop 离线核验通过。CPU 测试与真实模型验收分列。命令、日志、退出码在 `.artifacts/migration-baseline/`，锁在 `requirements/`。

## B：受限工具和冻结任务

只暴露 inspect_input、compute、view_artifact。模型只能提交会话 ID、枚举、字段白名单和有界参数，不能提交 Python/SQL/shell、表达式、路径、URL 或动态导入。内部固定 SQL 结构、标识符白名单、绑定值；numpy 禁止 pickle。拒绝符号链接、硬链接、哈希变化、跨会话资源和并发修改。继承已校验输入 FD，固定 2 线程、12 GiB 地址空间，单工具上限 240 秒，超时杀进程组并 wait。能力限制不等于同 UID OS 隔离。

12 题源于 TPC-H 四类查询意图和 sklearn 八类分析/学习意图；未复制 DS-1000 代码，不声称原 benchmark 分数。每题有独立 reference/checker、3 次参考耗时、实质错误拒绝、输入 SHA256、source_group、许可与来源版本。元数据在 `.artifacts/qwen-data-sources/` 和任务私有清单。四个 SQL 题共享生成数据库；分类任务共享 sklearn 来源和交叉验证结构，source_group 不等于独立数据来源，也不是多来源泛化测试。

冻结目录 `.artifacts/qwen-data-tasks-v5/`。冻结 task hash：`e4cc34b953dafae813677dfd4e89eea843592aad8b35c70d3a2459e109df3a08`；reference hash：`086a71e2115a086c14a6209ff9fae0cc5657239c8299a1b25563be0c103f3d26`。冻结前校准 v1–v4 失败及耗时全部保留；冻结后没有改输入规模/意图/参考解凑均衡。预检实际 4/2/6，本次主试点 4/4/4，不能据此宣称耗时档稳定；相同岭回归曾出现约 30/166 秒，主试点约 10 秒。

## C：固定 32B 和实际物理 KV

模型 `Qwen/Qwen2.5-Coder-32B-Instruct`，revision `381fc969f78efac66bc87ff7ddeadb7e73c218a7`。14 个原始 BF16 分片共 65,527,841,688 字节，HF 与 ModelScope 逐文件 SHA256/大小一致且本机全部完整校验。转换为未量化 F16 GGUF，SHA256 `0c2f9e4daac78e72b24f7c054416232a523f0fd2aeb37653747d3c21f7dca078`。权重/元数据/校验清单持久保留在 `/home/liuxv/local-models/qwen25-coder-32b-leasepath`。采集时 GGUF 从 tmpfs 运行；持久副本 `persistent-f16/Qwen2.5-Coder-32B-Instruct-F16.gguf` 已 fsync 并通过完整 SHA256 复核，身份和 receipt 同目录保存。单线程复核在共享机械盘很慢，改为最多 128 MiB 有界预读、全部字节按顺序进入相同 SHA256，267.14 秒完成；不是抽样校验。核验后临时 GGUF/identity 已清理并释放 65,535,970,144 字节，原始身份副本保留 conversion-original.identity.json，旧磁盘不完整输出另名保存。

使用本机 llama.cpp `fd27b2034905f8dadd8bd6d2cc44595f4929d391` sm_70 库和新 C++ adapter。275 个源码/header Git blob 核对，运行库逐文件 SHA256 身份保存；不声称可复现二进制构建。三 GPU 层切分，**不是 TP**。单序列、dense full attention、16K 上下文、F16 KV、贪心生成；无共享 owner、抢占、压缩或 speculative decode。CPU 映射 embedding 约 1,485 MiB，三设备模型 buffer 约 20,461/20,461/20,086 MiB；真实 KV 字节分别 1,476,395,008/1,476,395,008/1,342,177,280。

adapter 从真实 KV cell 读取 ID、位置、token、owner/refcount，GPU 同步后核对输入前缀再记录 release。每 16 cell 是分析分组，**不是 vLLM 物理分页块**。未宣称旧 vLLM 私有 hook 完成 CUDA 适配。复用 trace.py/prediction_state.py 的 U/D/C 与因果状态契约；首个合法兼容 lookup 包括受控 miss 均为 U，D 需最终 release 后显式 retire，C 需生产者排空后的完整观测边界。

`batch-b/backend` 8 次真实 32B 探针通过：精确输入 token、真实 cell、hit/受控 miss、EOG、协议 stop、length、短前缀、进行中取消和 timeout。取消发出时间处于 lookup/release 之间；timeout/cancel 实际执行 41/44 个 prefill token。中断在 batch 边界协作检查，20ms 探针实际约 641ms/206ms 释放，不是硬实时 GPU kernel 抢断。独立审计 U=2/D=1/C=1、unmapped/unexplained=0；受控实验不混入自然发生率。摘要见 `backend-32b-boundaries.json`。

Qwen-Agent 固定 0.0.34，tokenizer Transformers 5.3.0，真实推理由 native CUDA 完成。使用官方 QwenFnCallPrompt 的 FUNCTION/ARGS 预处理/解析及真实续写，stop 传入后端。不压缩、不隐式重试、不从任意文本猜工具调用。每题最多 6 次真实生成、900 秒；每次尝试先写 durable 预算，即使失败也计账。工具结果必须出现在后续真实 backend prompt。严格最终 JSON 同时比较实际 artifact 和独立参考。

协议历史完整保留：batch-a 启动器解引用 venv 路径，在生成前停止（143），修正后保留虚拟环境 prefix；batch-b/c 的 Nous XML 格式均 0/2，输出缺/错标签，原始 token 证实非解码问题，没有宽松解析。batch-d 改用同一库官方 Qwen 格式，两题各 3 次生成，**2/2**，工具结果消费 4/4，独立复核 U=4/D=2/C=0。主试点只在此门槛通过后启动。

## D：一次冻结主试点结果

主数据 `.artifacts/qwen-data-32b-batch-d/pilot/`；交付 `.artifacts/qwen-data-32b-batch-d/delivery/`。首次完整批次 12/12（100%）；无外部重跑、无内部参数拒绝修复，每题 3 次生成。没有拼接不同协议版本最佳答案。12 个注册 source_group 均完整成功，预定各档 4/4；主试点 36 次真实 lookup、24 次工具调用，24 个结果全部进入后续输入，未消费结果 0。审计拒绝 0、unmapped=0、unexplained=0。

| 母题 | 预定档 | 本次主要 compute 秒 | 本次实测档 |
|---|---|---:|---|
| ship_groups | 短 | 0.736 | 短 |
| air_count | 短 | 0.575 | 短 |
| segment_orders | 短 | 0.831 | 短 |
| priority_top | 短 | 0.663 | 短 |
| variance_pca | 中 | 14.163 | 中 |
| cluster_inertia | 中 | 10.901 | 中 |
| logistic_holdout | 中 | 5.464 | 中 |
| ridge_validation | 中 | 10.020 | 中 |
| forest_cv | 长 | 127.891 | 长 |
| boosting_cv | 长 | 32.862 | 长 |
| kernel_cv | 长 | 48.820 | 长 |
| extra_trees_cv | 长 | 128.990 | 长 |

≥60 秒且结果被后续真实请求消费的成功母题 **2 个**（forest_cv、extra_trees_cv）。未来 60 题目标的 ≥10 个尚未完成。全部工具调用（包括 inspect_input）为短 11/中 8/长 5；其中一个输入哈希慢不计为主要计算长尾，不用它增加 ≥60 秒成功母题数。

主 U=24/D=12/C=0，36 episode、106 snapshots。质量子集与全主批一致，但原始协议失败单独保留。因果快照仅使用当时可见白名单字段，标签/未来 remaining_ns 在 `quality-subset/targets.jsonl`，特征在 `features.jsonl`，不混入成功/未来总耗时。

独立 `check_qwen_data_dataset.py` 再次验证 sealed 原始流、frozen hash、实际 artifact、最终答案及 source_group，12/12 通过；证据在 `results/migration/new_server_2026-11-07/pilot-32b-*.json`。模型入口进程耗时 1,175.742 秒（含模型校验/加载）；batch-d 两题+主试点+导出 driver 共 1,717.814 秒。

全局新账本 `.artifacts/qwen-data-batch-v1/ledger.jsonl`：**86/144 次**，probe **50/72**、pilot **36/72**，包括 3B 基础设施和失败；历史为 historical_unreconciled。没有外部推理购买或货币成本估算；旧华为余量不继承。60 母题扩量计划见 `reports/new_server_scale_plan_2026-11-07.md`，未自动执行。

## 分项计时与限制

主试点中模型前处理+预算持久化中位数 0.215 秒，工具前/后源保护检查 0.025/0.043 秒；observer 序列化写入中位数 0.000187 秒，全部写入合计 0.898 秒；最终封口 fsync 0.497 秒。工具 input hash 合计 90.475 秒，某次 ridge inspect_input 为 51.990 秒，暴露共享磁盘冷读开销，不能叫模型推理/必要训练计算或缓存收益。

工具返回至下一 lookup 中位数 0.237 秒、最大 0.901 秒，包含保护、序列化、前处理等。engine enqueue→dispatch 中位数 0.010 秒，dispatch→lookup 0.000752 秒。worker execute 位于 child_wall 内，后者位于 tool_call_wall 内；各项不可直接相加。详细 `pilot-32b-timing.json` 保留样本数/分位数/全部 tool gap。

预算在进入引擎前 fsync；产物在返回 ID 前 fsync；calls metadata flush，无逐条 fsync；观测流在生产者排空后 seal+fsync。workspace_hash 两项不存在，记 not_applicable。没有通过跳过检查换速度或声称完整零开销。自有模型大文件复制在协议/主采集期间暂停，之后恢复；不保证共享磁盘 writeback 或他人负载隔离。

## 恢复、复核与下一步

代码逐阶段本地提交，未推送。Git bundle **不能单独保留 shallow 边界**，恢复时同时用 metadata.json：

```bash
.venv/bin/python scripts/restore_qwen_data_checkpoint.py \
  --bundle .artifacts/checkpoints/final-32b-pilot/leasepath.bundle \
  --metadata .artifacts/checkpoints/final-32b-pilot/metadata.json \
  --output /path/to/new-empty-checkout
```

原始 evidence、冻结输入/reference/许可/早期校准另归档 SHA256，bundle 不替代数据备份。最终 manifest 见 `.artifacts/checkpoints/final-32b-pilot/`。固定运行库源和本地库另归档，权重持久保留模型目录，未放入 Git。

只读复核无需启动模型或新增生成：

```bash
PYTHONPATH=src .artifacts/qwen-data-env/bin/python scripts/check_qwen_data_dataset.py \
  --run .artifacts/qwen-data-32b-batch-d/pilot \
  --tasks .artifacts/qwen-data-tasks-v5 --output /tmp/leasepath-pilot-recheck.json
```

新采集入口为 `scripts/collect_new_server_pilot.sh`，需要明确 3 张空闲 GPU、虚拟环境 Python、native.so、已校验 GGUF/tokenizer、任务和**新批次目录**；不得覆盖已封口 run。受本轮 ledger 及费用/可用时段限制，不自动用剩余预算扩量。下一阶段是 60 独立母题的来源/近重复复审、留出冻结与长尾预检；本轮没有验收旧阶段 0、在线收益或 JCT ≤2%。

持久模型校验完成后的原始 conversion identity 中 gguf 路径是历史 provenance；重跑时传入上述持久 GGUF 以及旁边的 identity.json，runner 以完整 SHA256 核验，不依赖历史 tmpfs 路径。最终原始归档只在同一服务器，尚无异机备份或远端推送。
