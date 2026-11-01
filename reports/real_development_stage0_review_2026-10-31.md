# 阶段 0 Review：接入准备与资源门槛

2026-10-31，Asia/Shanghai。执行依据：[完整实验计划](real_development_lookup_plan_2026-10-31.md)。

**结论：阶段 0 已启动，CPU 侧准备与真实任务环境验收完成；阶段 0 整体尚未通过。** 当前所有 NPU 仍有其他负载，Codex/32B 工具循环、TP=2、多卡 KV 生命周期和探针开销尚未实测，不能扩大到阶段 1。

用户已明确：所有卡均可在现有负载不用后使用，先做其他准备。此授权不表示当前已有独占窗口；等待现有负载退出，不停止现有服务。

## 1. 本次交付与门槛

| 检查 | 本次结果 | 是否满足阶段 0 最终验收 |
|---|---|---|
| 资源 | 8×910B2，每卡 65,536 MiB；各约 45 GiB 已占用，现有 sgl_diffusion 等进程仍在 | 否，须在使用前重新核查 |
| 32B 来源与本地权重 | 14/14 分片已下载并核验固定来源哈希 | 仅模型文件准备，不代表推理通过 |
| Codex 协议 | CLI 0.166.0，在隔离 provider 配置下实测发出流式 `POST /v1/responses` | 仅客户端协议发现 |
| 真实任务 | 3 个真实 issue、2 个仓库；全部复现原始缺陷，独立参考修复通过相关测试文件 | 任务环境通过；Agent 执行为 0 |
| 观察器 | 新增独立版本的逐进程记录器与严格传输完整性检查，7 项新增测试通过 | 仅 CPU 单元验收 |
| 新后端探针 | 已核查旧探针限制和新 hook 候选，未安装至新服务 | 否 |
| 多卡/上下文/生命周期 | 未实测 | 否 |
| 探针开关端到端开销 | 未实测 | 否 |

证据入口：[机器摘要](../results/real_development/stage0_2026-10-31/summary.json)、[最终资源快照](../results/real_development/stage0_2026-10-31/preflight-final/snapshot.json)、[候选配置](../configs/prediction/real_development_stage0.json)。本轮不生成 U/D/C 主数据，不重训经验预测器或 GBDT。

## 2. 模型与 Codex 接口

模型固定为 `Qwen/Qwen2.5-Coder-32B-Instruct`，Hugging Face revision `381fc969f78efac66bc87ff7ddeadb7e73c218a7`。14 个 BF16 权重分片共 **65,527,841,688 bytes**，约 61.0 GiB。下载器先比较镜像与固定 HF 的大小、LFS SHA-256，再读取本地文件验证；配置、tokenizer、许可证等小文件另验固定 Git blob。权重放数据盘，不写入 checkout。

最终 14/14 分片和 9 个小文件全部通过核验。首次第 14 分片只返回 8 MiB，校验拒绝完成；修正下载器以处理分段范围响应并严格检查 Content-Range 后，从保留的部分文件续传成功。首次失败及续传日志均保留，新增两个范围下载回归检查。模型目录的 `download-complete.json` 只在全部验证后生成，`inference_validated=false`。

配置显示 64 层、40 个 attention heads、8 个 KV heads，原生 `max_position_embeddings=32768`。阶段 0 候选仍为 BF16、TP=2、16K 上下文、先 1 个会话；并发 2/4/8 只在前档通过后尝试，均未标为已支持。

`check_real_development_protocol.py` 使用 `--ignore-user-config --ephemeral`、独立 provider、空工作目录和只读沙箱，向 loopback 捕获端发送一次请求。捕获端刻意返回 503，不生成模型回答、不执行工具，也不产生 lookup。实测请求含 `stream=true`、`input`、`parallel_tool_calls`、`prompt_cache_key`、`reasoning`、函数工具、namespace 工具和 web-search 工具。原始请求仅存本地，Git 只保存字段结构、哈希和命令。

这一发现说明后续须核查 Responses 的流式工具调用、工具 schema、请求 ID 传播、模板、辅助请求和重试。现有运行时虽有 Responses 实现，**源文件存在不能证明 32B/Codex 工具循环兼容**。若要增加适配器，必须另存转换前后输入、版本和额外延迟。尚未决定需要适配器，也未把旧 Chat Completions 导出器当作可直接复用的实现。

OpenAI Docs 技能要求的官方资料查询遇到 HTTP 403：Codex config reference、advanced config 和 Responses API 页面均未能读取正文。上述接口结论来自本机 CLI 的实际请求，未据无法访问的文档声称接入成功。

## 3. 真实任务与独立环境验收

数据来源为公开 `princeton-nlp/SWE-bench_Lite` test，固定数据集 revision `6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2`；Parquet SHA-256 为 `7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4`。这批只用于接入开发，存在公开基准预训练暴露的可能，不是新的独立确认集，也不代表自然用户会话。

| 任务 | 固定 base commit | 环境 | 缺陷版本相关文件测试 | 独立参考修复测试 |
|---|---|---|---|---|
| Flask #4992：配置文件二进制读取 | `4c288bc97ea371817199908d0d9b12de9dae327e` | Python 3.12 | 18 passed / 1 failed | 19 passed |
| Flask #5063：CLI routes 的域名信息 | `182ce3dd15dfa3537391c3efaf9c3ff407d134d4` | Python 3.12 | 55 passed / 2 failed | 57 passed |
| pytest #9359：装饰器导致源码范围错误 | `e2ee3144ed6e241dea8d96215fcdca18b3892551` | Python 3.9 | 54 passed / 1 failed | 55 passed |

共验证 131 个相关文件测试，原始版本为 127 passed / 4 failed，参考修复为 131 passed；所有命名目标均实际出现。测试固定使用同一 CPU 集中的 4 个 CPU，OMP/OpenBLAS/MKL 各 1 线程；具体 CPU ID、解释器版本、命令、耗时与依赖锁见清单。此处耗时属于环境预检，不是 Agent idle 或真实工具耗时样本。

Agent 工作区为未修复源码；测试 patch 与参考代码仅进入独立 evaluation clone。参考代码不作为 Agent 输入，参考通过不计为 Agent 成功。这不是官方 SWE-bench 容器 harness，也没有声称整个上游项目测试全部通过。一个 issue 的重跑始终属于同一来源组，项目分组另存。

初始失败和修正保留如下：

1. `environment-checks-a`：旧导入函数的 `git add .` 漏掉上游实际跟踪但被 `.gitignore` 匹配的 Flask `.env/.flaskenv` 测试夹具；另缺少 pytest 标准构建生成的 `_version.py`。新入口强制跟踪已下载公开源码的全部常规文件，并冻结逐文件哈希；没有修改任务解决方案。
2. `environment-checks-b`：补齐源码与标准构建后，Flask 两题通过。Python 3.12 上旧 pytest 的 AST API 弃用触发上游 warnings-as-errors，因此改用本机独立 Python 3.9 环境。Flask 仅对 `pkgutil.get_loader/find_loader` 的 Python 3.12 弃用增加窄范围过滤，其他上游警告规则保留。
3. `environment-checks-c`：新检查器误将 venv Python 符号链接解析成系统 Python，丢失隔离依赖，构建/导入失败。已修正为保留 venv 入口路径，没有安装到系统环境。
4. `environment-checks-d`：最终三个任务全部通过环境门槛，逐文件核对每个新 clone 与冻结源码一致。此前所有尝试均保留，均为 CPU 预检，不属于 Agent 主执行。

首次 Flask 依赖安装因本机私有索引缺少指定 pytest 版本失败，切换显式 PyPI 索引后成功；原始安装日志和最终依赖锁均保留。后续真实 Agent 运行须使用同样的环境和警告规则。

## 4. 新观察器与尚缺的后端能力

`real_observer.py` 与旧 D2 插件隔离，未改变已有插件入口或旧 raw 文件。新记录器提供：

- run/stream/boot/PID/source/rank 身份、锁内序号和同机单调时间；每进程单独文件，拒绝覆写、fork 后复用和失败后继续记录。
- 显式开始、带前文 SHA-256 的完整结束；控制器必须给出预先登记的 stream 清单。
- 拒绝序号缺口、截断、内容不一致、身份/时钟异常、缺失或重复 stream、混合 run/boot 和无完整结尾。

新增 2 项资源门槛测试、5 项观察器测试，以及 2 项下载范围回归检查，共 9 项。观察器测试使用人工记录，**仅证明传输检查行为，不证明 hook 无漏记，不生成真实需求或自然 C**。即使文件完整，`semantic_completeness_verified=false`；业务关系、请求映射和后端事件仍须独立验收。

静态审计确认旧 `d2_probe.py` 只验证一个 full-attention cache group；旧导出器仍依赖 Chat Completions ID、每请求一次 lookup、正常结束和无抢占。下一步须独立实现并在真实服务核查：

| 层 | 待验收能力 |
|---|---|
| Agent/协议 | task/session/owner/context/turn/tool/request 关联，结果交付与 ready，重试和辅助请求，关闭/恢复/压缩/分支语义 |
| Engine | 实际 tokens、兼容配置/cache salt、每次 lookup、抢占恢复、release/abort、scheduled prefill |
| KV | slot 代次、hash/分配/插入/淘汰/释放、refcount、共享 owner、active/pending 保护、容量与候选动作 |
| TP | 逻辑事件去重；逐 rank 物理内存，不能把逻辑字节重复计数或忽略 rank |

已保存源文件哈希与 `BlockPool`、`Scheduler`、`KVCacheManager` hook 候选。cold/warm/evicted miss、共享前缀、压缩/分支、恢复、取消、抢占、重试和开放关系 C 的受控后端检查仍全部待做。

## 5. 开销、复现与下一步

开销尚未实测。先登记候选门槛：探针开启后的配对服务时间中位数增加 ≤2%、p95 增加 ≤5%，包含完整开销的任务完成时间非劣界为 2%。这是待阶段 0 现场测量与 review 后锁定的工程标准，不是已达成结果；按任务报告不确定性，无法分辨时标为不足。最终策略实验仍须单独验证净收益。

本机本轮数据根目录：`/home/ma-user/work/leasepath/real-development-v1/stage0-20261031-a/`。修正后任务冻结位于 `tasks-b/`，首次和后续预检分别在 `environment-checks-a/b/c/d/`，原始协议请求在 `protocol/`。小型清单和验证摘要进入 Git，大型权重、源码、测试 patch、参考代码、原始请求与日志保留本地，以 SHA-256 关联。

复现关键入口（输出必须选择新目录；需要 `pyarrow` 的入口使用 `.venv-agent`）：

```bash
PYTHONPATH= .venv/bin/python scripts/preflight_real_development.py --output /tmp/leasepath-stage0-preflight-new
# 资源不就绪时返回 2，属于门槛拒绝，不是测试通过。
PYTHONPATH= .venv/bin/python scripts/check_real_development_protocol.py --output /tmp/leasepath-stage0-protocol-new
PYTHONPATH= .venv/bin/python -m pytest -q
```

模型、任务准备及最终环境验收的完整参数保存在[复现命令](../results/real_development/stage0_2026-10-31/reproduction.sh)。重复运行下载器会核验已有文件并续传 `.part`；重新准备或评测任务必须使用新的输出目录。

现有回归 **130 项通过**；D1 人工契约 50 项通过，事件契约 8 个正例接受、16 个反例拒绝。所有旧采集、模型与结果保持原版本。这些结果不计入新后端验收。

阶段 0 的后续顺序：等其他负载退出，重新确认至少两张卡独占 → 完成本版探针和协议 ID 关联 → 32B TP=2 服务及 Codex 工具循环 → 受控生命周期和开销 → 三个真实 issue 的实际 Agent 主执行及独立验收 → 补齐本 Review 后决定阶段 1。候选配置允许从全部 8 张卡中选空闲卡，当前未指定已占用卡为实验资源。

阶段 1 暂登记预算为 15 个独立任务、至少 3 个项目与约 5 个实际开发日；当前只冻结了本报告的 3 个阶段 0 任务，第三项目及阶段 1 的其余任务尚未预检/冻结。不能用这 3 个任务声称满足阶段 1 预算，也不能在资源未就绪时用模拟 lookup 补齐阶段 0。
