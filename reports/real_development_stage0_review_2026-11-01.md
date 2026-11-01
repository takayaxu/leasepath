# 阶段 0 Review：32B、TP 与真实 issue 实测

2026-11-01，Asia/Shanghai。本轮按 [完整计划](real_development_lookup_plan_2026-10-31.md) 执行阶段 0，承接 [CPU 准备 review](real_development_stage0_review_2026-10-31.md)。

**结论：本轮实测和 review 已完成，阶段 0 整体未通过，阶段 1 暂缓。** 服务侧 TP、真实 token/lookup、块生命周期及完整流验收通过；Codex 工具循环未通过，按计划使用同一 32B 的 mini-swe-agent 回退链。三个真实 issue 的独立正确性为 **0/3**，有效源码修复行为不足。多 lookup/抢占请求仍只有原始审计，尚未进入新版训练投影；整项 Agent 完成时间非劣性也未验收。不能据此扩大自然差异普查或训练新的 GBDT。

机器入口为 [summary.json](../results/real_development/stage0_2026-11-01/summary.json)。主数据在 `/home/ma-user/work/leasepath/real-development-v1/stage0-20261101-tp2-b/`；原始 tokens、工具输出、请求与轨迹保留本地数据盘，Git 保存摘要、哈希和脚本。

[本地证据清单](../results/real_development/stage0_2026-11-01/local-evidence-sha256.json) 关联 233 个文件，并核对六次服务的冻结探针包；[实现哈希](../results/real_development/stage0_2026-11-01/implementation-sha256.json) 和 [验证摘要](../results/real_development/stage0_2026-11-01/validation.json) 单独保存。

## 资源与固定配置

用户随后明确要求释放显存，覆盖先前等待已有服务退出的安排。原服务已停止；本轮实验使用独占卡，新服务结束后亦已停止。[最终资源快照](../results/real_development/stage0_2026-11-01/final-resource.json) 显示 **8 张卡均无运行进程**，HBM 约 3,414–3,427 MiB/卡为系统保留。不能要求这部分降到零才判为空闲；预检同时要求 ≤4,096 MiB 和明确的无进程证据。

| 配置 | 本轮事实 |
|---|---|
| 模型 | Qwen/Qwen2.5-Coder-32B-Instruct，revision `381fc969f78efac66bc87ff7ddeadb7e73c218a7` |
| 来源核验 | 14 个 BF16 分片，共 65,527,841,688 字节；9 个小文件；沿用已核验下载回执 |
| 服务 | vLLM `0.23.0+empty`、vLLM-Ascend `0.1.dev4229+g7495e9d69`；torch `2.10.0+cpu` 加 torch-npu `2.10.0.post4`；Transformers `5.5.4` |
| 推理 | BF16、TP=2、eager、16,384 context、128 tokens/block、chunked prefill 2,048；固定 seed 23 |
| 主运行 B | 卡 2/3、端口 8202、max-num-seqs=8；显式 192 blocks 的受控容量 |
| 实际 KV 物理分配 | rank 0/1 各 3,221,225,472 bytes（3 GiB），合计 6 GiB；权重、工作区另计 |
| 可分配容量 | 192 blocks 含 1 个 null slot，普通块为 191 个；不是无压力的默认容量 |
| 工具资源 | Agent 工具固定 CPU 188–191、线程数 1；独立评测固定 CPU 0–3 |

[服务 manifest](../results/real_development/stage0_2026-11-01/service-manifest.json) 保存命令、依赖版本、兼容身份和探针源码哈希。每个 run 使用独立 `plugin/` 包副本，以该副本和哈希复现当时插桩；仓库后续加入的断流防护不追溯改变已采事件。16K 验收是 16,368 输入＋16 输出；8 并发验收使用约 1K 的短请求，未证明 8 个 16K Agent 长跑同时可用。

先前运行 A 位于 `stage0-20261101-tp2-a/`，正常容量为 1,316 blocks，rank 各 20.5625 GiB KV。它保留 Codex 接入尝试和 cold/warm/reset：1,409-token 输入依次 cached=0、1,408、0。最后一次来自显式 cache reset，不能算容量淘汰。A 的普通 SIGTERM 未完整关闭 engine/rank 流，故其数据**不进入本轮主标签，也不支持 C**。

## 协议与 Agent

Codex CLI 0.166.0 实际发送流式 `/v1/responses`。做了两次有界 smoke：原生转发（仅重写模型/请求 ID），以及 function-only、移除 reasoning、greedy、限长并显式提示工具标签的配置。两次 CLI 都返回 0，却只输出普通 JSON 文本，未形成真实工具调用，目标 `probe.txt` 未创建。实际输出 token IDs 不含 Qwen 的工具标签 token；没有靠解析器或适配器补造调用。

结论仅限于这两种已测试配置，不是所有 Codex/Qwen 接入均不可能。原始请求、转换后请求、SSE、CLI 事件、结果和失败工作目录保留在 A 的 `gateway-*`、`codex-*-smoke/`。当时官方文档仍返回 403；接入判断依据本地实测。

按计划回退到 **mini-swe-agent 2.4.6＋同一 32B**，固定源提交 `04d809ceab9df28f9adaed044884180159172930`，使用文本 shell 协议，temperature=0、单次最多 768 输出 tokens、每题最多 30 次请求/900 秒、单工具 90 秒。不把它称为自然 Codex 会话。日志记录执行器调用/返回边界、结果可用、请求 ready、引擎 enqueue/lookup；CLI 事件接收时间未冒充 shell 实际开始时间。

## 三个真实任务与独立验收

任务仍使用 `stage0-20261031-a/tasks-b/tasks.json` 的原始 issue 和固定源码；没有将参考修复送入 Agent。先前独立 CPU 检查已验证原始缺陷可复现、参考修复相关文件 131 项通过。

| Issue | 请求/工具 | 退出 | 完整补丁 | 独立结果 |
|---|---:|---|---:|---|
| pallets__flask-4992 | 9 / 8 | Submitted | 0 bytes | 18 passed / 1 failed |
| pallets__flask-5063 | 30 / 29 | LimitsExceeded | 1,087 bytes | 55 passed / 2 failed |
| pytest-dev__pytest-9359 | 20 / 19 | Submitted | 433 bytes | 54 passed / 1 failed |

独立结果为 **127 passed / 4 failed、0 errors，任务 0/3**。全部四个 FAIL_TO_PASS 仍失败。第一题未形成修改；第二题反复猜测应用文件并创建独立 `custom_commands.py`，未修复 Flask CLI；第三题改写了自建 `testing/test_statement.py`，未修复 pytest 实现。56 次工具中 29 次返回非零，全部保留。

最初补丁提取只运行 `git diff`，遗漏新建、暂存或 Agent 已提交文件。已补充从冻结初始树提取完整补丁的实现和回归用例。原 `agent.patch`、result 和轨迹原样保留，新增 `agent.complete.patch` 与 `patch-capture-review.json`；本次独立评测使用完整补丁。验收在全新 clone 中应用生产修改，使用独立冻结测试；Agent 的测试改动保留供 review，但不能替代验收测试。没有人工修复 Agent 候选。

证据：[执行结果](../results/real_development/stage0_2026-11-01/pilot-results.json)、[独立评测](../results/real_development/stage0_2026-11-01/pilot-evaluation.json)。这是本地相关文件测试，不是官方 SWE-bench 容器 harness；公开基准可能存在预训练暴露。不能把 Submitted 或服务成功回答算作 issue 成功。

## 后端、生命周期与标签

B 共 **92 个真实引擎请求**，均有 enqueue、lookup 和 release；其中 59 个属于仓库 pilot，33 个属于独立受控测试。逻辑事件仅记录一次，两个 TP rank 单独报告物理内存。槽位代次、hash、refcount 和真实分配候选完整审计通过：962 次块分配、321 次容量淘汰，max generation=8、max refcount=2；[最终块审计](../results/real_development/stage0_2026-11-01/block-audit-final.json) 另确认 204 次分配符合实际候选队列顺序，结束时活动块为 0。淘汰与显式 reset 分开统计。

[受控审计](../results/real_development/stage0_2026-11-01/controlled-audit.json) 的 16 项检查全部通过：

| 验收项 | 实际证据 |
|---|---|
| 16K、2/4/8 并发 | 实际输入/输出预算和 prefill 至 release 的重叠区间通过 |
| 共享前缀 | ≥32 个共同块达到 refcount=2；关闭 A 后 B 仍命中 4,096 tokens |
| 容量淘汰后需求 | 14,080-token 原输入再次真实 lookup，仅 cached=10,240；完整选定前缀仍有 U，不能称所有子前缀都 miss |
| 抢占/恢复 | 两个 12,032-token 输入解码增长触发 1 次真实抢占，均完成 256 输出；多次等待 lookup 不算多次抢占 |
| 取消/重试 | 取消到达 FINISHED_ABORTED；显式 retry_of 关联另一个真实请求，重试 cached=384/512 并正常结束 |
| 分支/关闭 | 显式 fork；父 owner 关闭后子 owner 仍命中 1,024 tokens |
| 压缩/还原 | controller 明确关闭旧版本、提交短输入；另一个开放关系按完全相同 tokens 恢复 |
| 开放关系 C | 子 owner 保持开放至 observation_end，完整 backend 证据验证后导出 C；随后清理不回填 D |
| 断流拒绝 | 删除一个 producer 或去掉终端记录的派生负例均拒绝导出/验证 |

分支、压缩和重试是明确的 controller 操作，使用真实推理；不是捕获到的 Codex 自动压缩、自然重试或自然发生率。抢占请求有 122 次 lookup，原始事件可审计；**v0.1 训练投影拒绝多 lookup/抢占请求**，尚不能直接用于阶段 1 的一般并发流。

所有请求排空后，先调用两个 rank 的 `leasepath_checkpoint`，再记录 outstanding_requests=0 的 engine checkpoint，最后停止 API。预先冻结的 7 个 producer 流均有连续序号、同 boot 单调时钟和终端哈希；runner 与后端 boot 也必须一致。缺失流不接受，[最终负例结果](../results/real_development/stage0_2026-11-01/negative-audit-final.json) 还包括候选队列顺序篡改和另一个 boot 的 runner 被拒绝。关闭日志有 resource_tracker 清理警告；NPU 已无进程，但尚未以长期运行证明主机 IPC 无残留。

主导出严格关联每个 client ID 与唯一 backend ID；从 release 时当前输入确定最长完整 block 前缀，再匹配后续同 owner 的真实兼容 lookup，包含 miss。正常请求的 cached＋scheduled prefill 与实际输入一致；后者不是设备计算时间。

| 数据范围 | Owner / episode | U | D | C |
|---|---:|---:|---:|---:|
| 仓库 pilot | 3 / 59 | 56 | 3 | 0 |
| 独立受控 fork | 2 / 3 | 1 | 1 | 1 |

D 来自 runner 明确丢弃上下文；仓库 pilot 没有自然 C。受控 C 只有约 2.9 ms，作用是验证语义和完整性，不代表真实长暂停。成功质量子集为空，未从主集删除失败任务。[逐条样例](../results/real_development/stage0_2026-11-01/examples.json) 保存实际时间和本地原始证据地址：一个 U 的工具结束至 lookup 为 14.509 ms，另有 D、C 和容量淘汰后部分 miss 的 token 匹配例。单个例子不支持 H1a、可预测性或缓存决策收益。

## 探针开销

使用四个独立服务目录，两组同机设备交叉：卡 0/1 先 off 后 on，卡 4/5 先 on 后 off。off 在启动时不安装引擎钩子，也不创建 rank observer；on 使用相同探针源码。模型、精度、TP、容量和工作负载固定。每次排除一个 warmup；512/2,048/4,096 输入、32 输出、cold/warm、四次重复，共 **48 对/96 个计入请求**。每对输入/实际输出哈希及 token counts 完全一致。

| 指标 | 结果 |
|---|---:|
| 配对增幅中位数 | +0.3367% |
| 配对增幅 P95 | +4.6353% |
| on/off 各自服务时间 P95 的比值增幅 | −1.4679% |
| 卡 0/1、4/5 的配对增幅中位数 | −2.3893%、+0.7838% |
| Observer JSON 序列化＋os.write 中位数/P95 | 69.13 / 736.35 μs |
| finish/fsync 中位数 | 6.09 ms |

后两项是 3,020 个已解码 payload 的独立微测，不包含快照构造；完整开关的端到端比较包含钩子、快照和写日志。on 的两次采集原始日志各约 1.71 MB。未使用只停止 emit、仍构造快照的旧 marker 来冒充完整探针关闭；新导出器也拒绝显式 observation_gap。

预登记的 median≤2%、服务时间 P95≤5% 在这组短请求上描述性满足；两个固定相反顺序的卡组仍有时间漂移与同机竞争，重复请求不是独立任务/工作日。**整项 Agent JCT≤2% 非劣界未测，不能宣布完整开销门槛通过**；没有预测器推理开销。详见 [开销摘要](../results/real_development/stage0_2026-11-01/overhead-summary.json)。

## Review 决定与后续范围

阶段 1 保持未开始。先在阶段 0 修复并再次 review 以下门槛：稳定的源码检查—修改—验证行为及独立质量；多 lookup/抢占/重试的一般关系投影和辅助请求完整性；能覆盖实际任务的开销与完成时间边界。可以继续使用 mini-swe-agent 回退链，但必须保留 Agent 身份和失败范围；若再次尝试 Codex，独立版本化配置，不能把已有 smoke 改称通过。重跑同一 issue 仍属于原来源组。

[阶段 1 候选清单](../configs/prediction/real_development_stage1_candidates.json) 固定 15 个未用于本轮的 issue，覆盖 Flask、pytest、requests 三个仓库，保留原 revision、commit 和内容哈希。选取规则是一个未用 Flask issue、全部六个 requests issue、按 ID 排序的前八个未用 pytest issue，不使用模型成绩选择；这些候选**尚未做环境预检，不是已验收执行集**。原预算为 15 个独立仓库任务、至少三个仓库、约五个实际自然开发工作日；没有用等待凑天数，也没有采集自然用户会话。

本轮项目回归 **139 项通过**，旧结果清单 **125 个文件 SHA-256 全部未变**；没有新预测训练、策略实验、提交或发布。复现可从以下入口开始，输出参数必须换成新目录；已有原始失败和结果保持不覆盖：

```bash
PYTHONPATH= .venv-agent/bin/python scripts/run_real_development_tasks.py \
  --tasks /home/ma-user/work/leasepath/real-development-v1/stage0-20261031-a/tasks-b/tasks.json \
  --environments /home/ma-user/work/leasepath/real-development-v1/stage0-20261031-a/environments \
  --run <已启动的新服务目录> --output <新pilot目录>
PYTHONPATH= .venv/bin/python scripts/evaluate_real_development_pilot.py \
  --tasks <tasks.json> --environments <environments> --pilot <pilot目录> --output <新评测目录>
# 排空请求、保存 expected-streams、rank/engine checkpoint 并停止服务后：
PYTHONPATH= .venv/bin/python scripts/export_real_development.py \
  --run <run目录> --pilot <pilot目录> --output <新数据集目录>
PYTHONPATH= .venv/bin/python scripts/audit_real_development_stage0.py \
  --run <run目录> --output <新审计文件>
```

`check_real_development_lifecycle.py` 和 `check_real_development_recovery.py` 分别运行受控场景；`measure_real_development_overhead.py`、`run_real_development_overhead_lane.py` 与 `summarize_real_development_overhead.py` 记录和审计独立交叉开销实验。所有 U/D/C 是监督产物，不是在线特征；本轮仍未证明合法需求目标、任何预测器或 KV 策略具有实际净收益。
