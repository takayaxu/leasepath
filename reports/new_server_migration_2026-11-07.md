# 新服务器迁移：Qwen-Agent 与受限参数工具

本轮基线固定为 GitHub 66a34c8eb618f4849153cb3c8d72880362e8e387。新 checkout 为 /home/liuxv/LeasePath-new-server-data，分支 codex/new-server-data。原 /home/liuxv/LeasePath 保持原提交与资源。交接文档的上传副本保留在 .artifacts/migration-baseline/NEW_SERVER_HANDOFF.md。

## A：基线与实际资源

GitHub HTTPS Git 的 POST 不通，API/codeload 可读。因此通过固定提交 tarball、GitHub Git commit/tree API 恢复浅 checkout；git write-tree 精确匹配 95926ec30c5b6e362530b8c34f34a06eabd4a7e0，重建 commit 对象精确匹配指定 SHA；git fsck 通过。不是重新提交一个类似快照。祖先未恢复，.git/shallow 明确记录边界。

实际主机 v100-server，Ubuntu x86_64，56 逻辑 CPU，约 503 GiB RAM，数据卷约 5.6 TiB 可用，10 张 Tesla V100 32 GiB。GPU 5–9 初始空闲；0–4 的其他负载保持。CUDA 12.4/nvcc 和 sm_70 小内核实测通过；系统 PyTorch 2.11.0 缺 sm_70，实际张量计算失败，不能作为推理环境。Docker/NVIDIA runtime 可用，user namespace 只读探测通过。完整端口、进程、显存和命令输出见 results/migration/new_server_2026-11-07/environment.json。

新 CPU .venv 基线适用回归：174 passed, 1 skipped, 1 deselected。full_pinned_audit 按交接排除，另一条历史测试自行 skip；不能记为全套通过。D1 50 项和事件契约 8 正例/16 反例通过。新路线初次回归为 189 passed, 1 skipped, 1 deselected。依赖锁位于 requirements/，每次补充依赖保留安装日志。

## B：新增软件与已冻结任务

新增工具只接收会话 dataset/artifact ID、操作枚举、字段白名单和有界参数。工具子进程不接收 Python/SQL/shell/路径/URL；内部固定 SQL 标识符白名单、值参数绑定；numpy load 禁止 pickle。资源拒绝符号链接、硬链接、哈希变化和并发写入。计算使用继承 FD，子进程组 240 秒超时后 SIGKILL 并 wait，固定 2 线程/12 GiB，源/参考/检查器不由工具注册表暴露。这是能力边界，不是同 UID 的 OS 隔离。

边界测试 15 项通过，包括 FD 校验后的偏移回归、跨会话、注入、超限、源修改、链接和超时清理。独立投影测试 8 项通过，包含 hit/miss 同为 U、未 retire 的 C、abort 不造 idle、缺消费、错误物理 token、断流和未映射请求；测试为合成证据。

12 个开发母题按 TPC-H 的四种独立查询意图及 sklearn 的八种常规统计/学习操作改编。每题独立 reference/checker，质量检查比较具体数据、数值、行和最终答案；不声称原 benchmark 成绩。CPU 校准发现预计中档的聚类/回归过快、一个森林 CV 超过 180 秒，已在模型试点前完成调整并冻结 v5；旧 v1/v2/v3/v4 输入、耗时和失败均保留。冻结后不再按模型运行耗时调整。

## C：CUDA 后端与协议（进行中）

复用本机已核验 llama.cpp fd27b2034905f8dadd8bd6d2cc44595f4929d391 的 sm_70 库，通过单序列 C++ adapter 直接调用。用 public get_cells/seq_pos_get/ext_get 读取实际物理 KV cell ID、位置、token 和 owner，memory_breakdown 读取各 CUDA 设备实际 KV 字节。release 前同步 GPU、核对完整输入前缀，并在解除 active 之后记录 release_ns。正常 EOG/协议 stop 可建立 idle；length/cancel/timeout 不建立。每 16 个 cell 仅为分析分组，不伪称 vLLM 分页块。仅支持同主机单序列、dense full attention、F16 KV，无抢占、压缩、speculative decode 和多 owner 共享。层切分不是 TP；不宣称 vLLM private hook 已适配。

3B 基础设施实测 7 次，输入 token 与独立 tokenizer 精确一致，命中/受控清空 miss、正常 EOG、协议 stop、length、timeout、cancel 路径通过。实际进行中的取消尚待 32B 新探针补验，旧探针的取消在请求前设置 flag。此结果只算基础设施，不能代替 32B。3B 工具流诊断两题第一次失败全部保留；收紧相关参数 schema 后第二版 1/2 正确，全部 5 个关系经过独立审计（3 U / 2 D），仍不算主试点。

主模型固定 Qwen2.5-Coder-32B-Instruct / 381fc969f78efac66bc87ff7ddeadb7e73c218a7，14 分片共 65,527,841,688 字节。HF 与 ModelScope 元数据的逐分片大小/SHA256 全部相符。14 分片已全部下载并通过完整 SHA256 复核，已成功转为不量化的 F16 GGUF 并完成完整输出 SHA256；不换模型。共享磁盘读取缓慢，转换输出暂存在 /dev/shm/leasepath-liuxv-qwen-data；正在复制到持久模型目录 persistent-f16 并核对 SHA256，不能将 tmpfs 当备份。使用 GPU 5/6/7 层切分：两卡放权重后无足够 KV/runtime 余量，三卡约 96 GiB。启动前重新检查空闲设备。

## D：试点与边界

尚未运行 32B 的 12 题主试点，主数据完成数为 0。Qwen-Agent 固定 0.0.34，仅用接入/解析与简单顺序循环；禁止自动压缩、隐式重试和任意文本命令提取。全局账本 .artifacts/qwen-data-batch-v1/ledger.jsonl 包含小模型实测，probe≤72、pilot≤72、total≤144。历史用量标记 historical_unreconciled，不沿用旧余量；没有购买、扩容或联系华为云。

只有 32B 的后端边界和两题真实多轮闭环通过后才允许 12 题入口；完整成功/按预定档成功/实际耗时三类分别判定。没有伪造主 U/D/C 或训练包，没有启动预测器训练、在线缓存策略或 JCT 收益测量。

全部运行目录使用新名，失败不覆盖。每阶段保存本地提交、补丁、bundle 与 raw 数据归档/SHA256；不擅自推送。后续以本报告的实测更新为准。


## 12 题 CPU 冻结结果

最终输入与独立参考在 .artifacts/qwen-data-tasks-v5/。12 个不同 source_group；每题三次独立参考执行，实质错误值/缺行负例均拒绝，受限工具数值与独立参考一致。四题 SQL 约 0.58–0.81 秒；实际工具中档为聚类 13.78 秒、逻辑回归 5.34 秒；PCA 30.38 秒、岭回归 166.44 秒、森林 CV 135.55 秒、提升树 CV 39.48 秒、SVC CV 53.00 秒、Extra Trees CV 135.05 秒。预定 4/4/4，实测 4/2/6，不能宣称稳定三档均衡。

保留的共享服务器耗时差异：岭回归相同输入及参数在 v4 工具约 29.8 秒，在 v5 约 161.9 秒计算子进程墙钟；均与参考数值一致。PCA 的参考三次为 6.15/3.91/3.25 秒，v5 工具墙钟 30.38 秒。后台 CPU 工作负载存在，没有停止其他用户任务；固定线程数不等于独占 CPU。未为均衡追加无意义计算或 sleep。

为避免将同一种森林分类的留出/CV 变体计为不同母题，最终中档分类改用逻辑回归留出测试。所有调整均在 32B 请求之前；最终输入规模、意图、参数范围和 checker 现已冻结。完整小清单见 results/migration/new_server_2026-11-07/task-preflight.json；大输入、三次参考与失败校准日志另打包于 .artifacts/checkpoints/task-freeze-v5/inputs-and-references.tar，并有 SHA256。

当前新增软件回归为 199 passed / 1 skipped / 1 deselected；CPU 测试不包含真实模型验收。观测写入/封口 fsync、源保护检查、模型预处理和 worker 执行时间分别计量；workspace_hash 两项不存在，标记 not_applicable。计时嵌套不可直接相加。

Git bundle v2 本身不会序列化 shallow 边界。恢复须同时保留 metadata.json，并使用 scripts/restore_qwen_data_checkpoint.py；已在新的空目录通过恢复和 git fsck。普通 git clone bundle 的失败日志也保留，不能仅凭 bundle verify 宣称完整祖先已恢复。

## 真实 CUDA 受控边界补验（3B 基础设施）

同一 16K 单序列后端完成 8 次新增探针，独立审计证实 U=2（实际命中与受控清空 miss 各一）、D=1（显式 discard）、C=1（生产者排空后仍开放的关系）。控制日志与自然样本隔离。两种物理路径返回相同输出 token；EOG 与协议 stop 保留正常释放证据，length、短于分析块前缀、取消和超时不伪造 idle。超时与取消请求分别执行了 41 和 44 个新 prefill token 并实际释放；后续 32B 版本还将显式记录取消发出时间位于 lookup/release 之间。受控终态摘要见 results/migration/new_server_2026-11-07/infrastructure-controlled-audit.json。

全部 3B 探针/失败流入本轮总账，目前 32 次 probe，0 次主试点；32B 可用后再按照两项门槛执行。模型进入 GPU 前再次核对空闲卡；未触碰现存 9B 服务。归档见 .artifacts/checkpoints/controlled-cuda/raw-infrastructure.tar.gz。

## F16 转换与启动路径修正

完整 GGUF SHA256 为 `0c2f9e4daac78e72b24f7c054416232a523f0fd2aeb37653747d3c21f7dca078`，模型身份副本见 results/migration/new_server_2026-11-07/model-32b-identity.json。转换原始分片的干净文件缓存仅在转换子进程结束后通过 POSIX_FADV_DONTNEED 建议释放；没有 drop_caches、全局内存参数调整或其他任务终止。共享主机在输出校验初段有 swap/I/O 压力，保留采样，不将其当作独占环境性能。

首个自动 launcher 的 realpath 会解引用 venv Python 到系统 Python，在生成前被发现并停止，退出码 143；账本仍为 32 次。原批次 .artifacts/qwen-data-32b-batch-a/ 保留 launcher-failure.json。已修为仅取绝对路径，实际核验虚拟环境 prefix 与 Qwen-Agent 0.0.34 / Transformers 5.3.0，再从 batch-b 启动。没有复用失败批次为已通过门槛。完整新驱动参数、退出码与资源见 .artifacts/migration-baseline/batch-b.*。

## 32B 真实后端门槛已通过

batch-b/backend 完成 8 次真实生成，输入 token 与客户端逐项一致，物理 KV cell token/owner 和每设备实际 KV 字节均核验。首次/命中/受控 miss 返回相同输出 token，正常 EOG 与协议 stop 可建立 idle；长度截断、短前缀、取消和超时不伪造 idle。进行中取消的发出时间在 lookup 与 release 之间；timeout/cancel 分别实际执行了 41/44 个 prefill token。取消/超时在 batch 边界协作检查，不声称硬中断 GPU kernel：20ms 探针实际在约 641ms/206ms 后释放。

独立审计：8 个 request、0 个 unmapped/unexplained，受控 U=2/D=1/C=1；完整控制日志与自然试点分开。摘要在 results/migration/new_server_2026-11-07/backend-32b-boundaries.json。三设备实际 F16 KV 分别为 1,476,395,008 / 1,476,395,008 / 1,342,177,280 字节；模型层切分 buffer 分别约 20,461 / 20,461 / 20,086 MiB，另有 CPU 映射 embedding 约 1,485 MiB，不是 TP。门槛通过后的账本为 40 probe / 0 pilot。两题工具协议与主试点的结果另列。

## 32B 首次两题协议失败与有界修正

batch-b/protocol 两题各一次生成，均输出裸 JSON 工具名/参数，未生成 Nous 所需 `<tool_call>` XML wrapper。独立 tokenizer 重解 output_tokens 与文本完全一致，排除解码丢字；官方 Qwen-Agent 正确将其视为普通文本，严格最终 schema 拒绝。因此质量 0/2，工具执行 0，audit 通过且 D=2；这些不是成功主数据。完整失败保留，主试点没有生成，账本 42 probe / 0 pilot。

下一版仅在系统/用户提示明确 XML 调用示例与最终 JSON 区别，不从裸文本猜调用；另修正工具 schema 描述共享 FIELD 对象导致 group_by 描述被 value 覆盖的问题。操作、边界、冻结题目/输入/参考解不变。该代码版本为 564d7c9，适用回归 199 passed / 1 skipped / 1 deselected。新 batch-c 复用已通过后端，须再次完成 2/2 协议与审计后才启动主试点，不拼接旧批最佳结果。

持久模型复制为自有进程；protocol/pilot identity 建立到 summary 封口期间用 SIGSTOP 暂停复制，外部检查阶段/运行结束用 SIGCONT 恢复，并记录动作。它避免自有大文件归档与工具计算同时运行，但不保证共享磁盘已完成全部 writeback，也不代表其他用户负载隔离。

## 协议第二次失败与官方 Qwen 格式

batch-c 两题仍各仅一次生成，实际输出错误标签 `<compute>…</compute>` 与 `<{…}>`，0/2；独立解码确认原始 token 如此。轨迹审计通过，D=2，无工具调用；driver 在门槛失败后未启动主试点。账本 44 probe / 0 pilot。没有宽松标签解析、补造工具执行或更换模型。

batch-d 改用同一 Qwen-Agent 0.0.34 提供的官方 QwenFnCallPrompt，即 `✿FUNCTION✿ / ✿ARGS✿` 格式；完整 FUNCTION/ARGS/result history 与续写仍由官方组件处理。generate_cfg 的 `✿RESULT✿ / ✿RETURN✿` stop 现传入原生后端，保留 protocol_stop 来源。固定库 parser/stop 离线核验通过，受限工具与审计相关 23 项回归通过（117.64 秒）；源提交 cc87dad。题目、输入、参数能力和参考解仍是 v5。此处不是 32B 协议通过声明，真实结果待 batch-d。

## 32B 真实两题协议门槛通过

batch-d/protocol 使用官方 Qwen FUNCTION/ARGS 格式，ship_groups 和 air_count 均在 3 次生成内完整成功，2/2；分别实际执行 inspect_input 与 compute，4 个工具结果均进入下一次真实后端输入。原生输出 protocol_stop 有真实 matched_stop 来源，最终 JSON 由官方解析组件移除续写冒号后按严格 schema/checker 验证，未自定义猜测工具调用。独立封口复核与质量复核通过；U=4/D=2/C=0、unmapped=0、unexplained=0、未消费结果=0。控制实验与前两次失败不并入本组成功率。摘要及独立复核在 results/migration/new_server_2026-11-07/protocol-32b-*.json。门槛后的总账 50 probe/0 pilot。

同一 batch-d 驱动已启动一次完整冻结 v5 的 12 题主试点，不变更输入/参考/意图/能力。主结果以之后的封口 summary 和独立 checker 为准，不以两题成功推定 12 题通过。
