# 阶段 0：Codex CLI + 本地 32B Review（2026-11-01）

**本轮完成：Codex CLI + 32B 的真实多轮工具接入通过，真实仓库开发闭环未通过，阶段 0 仍不放行，阶段 1 未启动。** 执行依据为 [指导.md](../指导.md)。本轮由 Codex CLI 实际执行工具，网关仅转换协议和记录证据；没有使用 mini-swe 或自定义 Agent 循环。旧实验与失败结论保留。

| 阶段 | 判定 | 直接证据 |
|---|---|---|
| A CPU 协议与实现 | 完成 | 模型/CLI 身份、旧断点、真实私有 shell、转换回归均核查 |
| B 真实多轮接入 | 第二套配置通过 | 两种场景独立通过，18 次真实 shell，结果进入后续模型请求 |
| C 已使用来源联调 | 0/1，停止 | Flask-5063，24 次模型请求、空补丁；独立 55 passed / 2 failed |
| D 新五题 | 未触发 | C 未过；五题均未准备或执行，不能写成 0/5 |
| 后端与标签 | 本轮受限范围通过 | 45/45 实际 prompt IDs 对齐、7 producer 完整、41 U / 4 D |
| 完整 JCT、自然开发、阶段 1 | 未验收、未启动 | 不以接入、受控 fixture 或失败循环抵消质量门槛 |

结果见 [摘要](../results/real_development/stage0_codex32b_2026-11-01/summary.json)、[阶段 B Review](../results/real_development/stage0_codex32b_2026-11-01/B-review.json)、[阶段 C Review](../results/real_development/stage0_codex32b_2026-11-01/C-review.json)。

## A. CPU 接入核查

本阶段完成。CLI 固定 0.166.0，二进制 SHA256 和独立模型 catalog 在数据盘 `codex32b-20261101-a/preregistration/`。不升级 CLI、不修改全局配置。官方 Codex 配置与 Responses 流式文档两页均返回 HTTP403，以下判断依据本机 CLI、源码、实际请求和合成协议测试，不能称最新版官方兼容性已核验。

旧两次 Codex 请求的工具均为 function；旧 function-only 过滤未改变工具类型。实际返回为普通文本 JSON，CLI 没有执行对应工具。固定 Qwen 模板没有 developer 角色分支；旧主要指令采用 developer。当前本机 vLLM Responses 默认使用 SimpleContext，工具解析上下文另受实验开关控制。没有再次原样重跑两次旧 smoke。

新 [适配器](../src/leasepath/codex_protocol.py) 和 [执行入口](../scripts/run_codex32b.py) 将 Responses 完整历史转换到 Chat Completions/Hermes：developer 显式映射 system，保留七个 function 工具定义、调用 ID、参数和真实结果。非文本、非函数工具、引用式历史、压缩及其他未支持项明确拒绝，不静默过滤。后端 SSE 完整缓存到正常结束并通过 schema 校验后，再按顺序发送 Responses 增量/完成事件；因此存在缓冲延迟，不宣称流式延迟透明。网关不执行命令、不补造工具或修复代码。

CPU 核查修正了三处实现假设：catalog 不接受 function 类型的 apply_patch 字段，已删除该字段；CLI 不识别 features.goal/skills，已去除；CLI 会将 shell 路径重新解析到 /bin/bash，直接替换路径未获执行。以上失败均保留，发生在新模型生成之前。

使用显式 BASH_ENV 启动 [执行观测器](../scripts/codex32b_shell.py)，对 CLI 的原命令按验证过的调用 ID 精确匹配，记录实际进程起止及退出码，独立进程组限制 90 秒并清理子进程。不改写模型命令，不自动调整环境。合成协议测试验证了 Codex 真实执行、私有 python/pytest/flask、结果回传；这是 CPU 合成测试，不能代替真实模型 smoke。CLI 事件接收时间另记为接收时间。

最终协议/执行器回归 24 项通过，完整项目 **180 passed**。所有 23 个模型文件重新按原 manifest 核验 SHA256，固定 revision `381fc969f78efac66bc87ff7ddeadb7e73c218a7`。配置、两种 fixture、开发来源 Flask-5063、每步预算与触发条件已在首个新模型调用前冻结。第二套配置和一次基础设施修正各自在相应尝试前另行冻结，没有按成绩覆盖旧配置。

## B. 两套配置及真实工具闭环

B 冻结为最多两套配置、各三次 smoke，每次 12 次模型请求/600 秒/每请求 2048 输出 token；C 最多两次联调，各 24 请求/900 秒。旧 L1 门槛没有阻止本条新链。实际完成四次 B 尝试，其中一次基础设施无效仍消耗预算；没有第三套配置。

| 配置 / 尝试 | 真实后端请求 | 实际 shell | 独立结果 |
|---|---:|---:|---|
| v1 / 1：Chat 原生工具、Hermes | 1 | 0 | 失败；四段普通 JSON，无工具标签，CLI 返回 0 但未执行 |
| v2 / 1：严格文本协议 | 0 | 0 | 基础设施无效；vLLM 拒绝 `tools: []`，保留 HTTP400 与原始请求 |
| v2 / 2：读写检查 | 8 | 7 | 通过；读取输入、写 result.json、运行检查、读取并报告实际 receipt |
| v2 / 3：失败测试恢复 | 12 | 11 | 通过；观察 pytest 失败、两次语法错误，修改 helper 后 3/3 独立通过 |

v2 使用 [独立配置](../configs/prediction/real_development_codex32b_v2.json) 和预先声明的 `strict-json-envelope-v1`：完整模型响应只能是一个 `tool_calls` 对象或 `final` 对象，每次一个调用，不接受自然语言、代码围栏、多个 JSON、未知工具或无效参数。工具 schema 全部移入明确的系统协议区，工具结果原文放在带调用 ID 的 JSON envelope 中。适配器从严格响应赋予唯一调用 ID，再生成 Responses 事件，Codex 执行并携带真实结果继续请求。这是**严格文本工具适配**，不是 Qwen 原生 function calling 已验收，也不是网关运行 Agent。未从任意文本宽松提取 JSON。

v2 第一次在生成前遭到 HTTP400，未进入引擎。修正为省略空 tools 字段，保留全部 schema 的文本表达，协议和提示不变；保存原实现与修正后实现，原失败仍计为 v2 的第一次尝试。v2 的第二、第三次是最后两个剩余 smoke 名额，均通过。CLI 和网关重试配置为 0；没有隐式模型重试。各次真实运行 stderr 均为空，没有 fallback metadata 警告。

读写场景的最终内容为 `{"label":"obs-7bc2e1","total":30}`，receipt 为 `verified:bfc35c0cd588`；输入与检查器哈希保持不变，最终回答确实引用实际生成的 receipt。恢复场景首次 pytest 返回 1，随后两次返回 2；模型修复自身生成的字面换行错误后，原测试全部通过，测试文件未修改。42 次执行中的这 18 次属于受控 fixture，不计真实任务成功率、自然失败率或开发工具时长分布。

原始链例在数据盘 `smoke-v2-2/request-*/` 和 `smoke-v2-3/request-*/`：`backend.sse` 是真实模型输出，`normalized.json`/`responses.sse` 为转换结果，`tool-execution/*.jsonl` 有实际命令起止和调用 ID，后续 `original.json` 与 `forwarded.json` 有相同 ID 的真实结果。独立检查分别见仓库 [读写验收](../results/real_development/stage0_codex32b_2026-11-01/smoke-v2-2-independent-check.json) 和 [恢复验收](../results/real_development/stage0_codex32b_2026-11-01/smoke-v2-3-independent-check.json)。

## C. Flask-5063 开发来源联调

来源在 B 成绩之前固定，使用全新 checkout、全新 Codex 上下文与私有 Flask 环境。CPU 预检核验源码与依赖；Codex 的首个真实 shell 也打印出正确的私有 python/pytest/flask、sys.executable、sys.prefix 和本 checkout 的 flask.__file__。没有宿主 Python/CLI 回退。

需求契约审查发现原 issue 示例用 Domain，而冻结上游目标要求 Subdomain/Host 标题。为避免要求模型猜测未说明的唯一输出词，本次 **C 是带显式输出契约的开发联调**：运行前补充两种模式的标题、取值和无相关值时保持旧布局的行为要求，并冻结原 issue 与补充契约。没有提供参考修复或隐藏测试内容。独立原 benchmark 测试单独评估；即使成功也不应称为未经帮助的 benchmark 成功。

实际 24 次模型请求、24 次 shell，约 144.19 秒。第 1 次核对环境；第 2 次 CLI 找不到应用；第 3 次使用错误的 `--app` 参数位置；第 4 次起反复执行 `timeout 90s FLASK_APP=... flask routes`，共 15 次返回 127。每个真实错误都进入后续输入，但模型只更换路径并重复 find，没有读取相关源码、运行相关测试或修改生产文件。完整补丁为 0 字节。

第 25 次前端请求在预算检查处拒绝，没有第 25 次模型生成；CLI 返回 1。最后一次 shell 结果附在该请求中，**没有进入模型**，不能把全部 42 个执行结果都称为已反馈。独立 fresh clone 应用空生产补丁与冻结测试后，**55 passed / 2 failed**，两个目标均失败。见 [完整独立验收](../results/real_development/stage0_codex32b_2026-11-01/C-independent-evaluation.json)。

这次失败发生在当前提示、模型配置下的命令构造和错误恢复，不是工具调用未执行。没有发现可支持 C 第二次尝试的环境、适配或采集缺陷；第二次预算不用于相同条件择优重跑。按指导停止 C，不人工修候选，不追加提示版本。

## D. 未见验收及研究范围

预选的 Flask-4045、pytest-5227、pytest-5221、pytest-11148、pytest-5692 均未准备或执行，原阶段 1 候选也未执行。未改变题目、门槛或来源组；不能写成 0/5 或宣称两个 fixture 代表真实仓库能力。

目前只证明：固定 32B 在严格文本工具适配下可以由 Codex CLI 实际控制 shell，并在小型 fixture 上利用错误反馈完成修复。唯一仓库联调没有有效开发闭环。因此没有完成完整 Codex Agent on/off 配对，JCT 单侧 95% 上界 ≤2% 的门槛仍未测。网关缓冲、转换和 shell 观测开销不能由短运行均值或旧服务微测代替。没有自然用户会话、长工具、共享上下文、多阶段依赖或真正并发开发范围的证据。

若另开下一轮，需要先针对命令构造和重复失败恢复设计有界诊断，明确是否调整通用指令、工具 schema 或模型服务能力；同时保留当前已打通的 CLI 执行证据。涉及新提示/接口/模型时须另建版本与预算，不自动启动第三配置、本模型额外试题或新模型。这里提出的是下一轮需要改变的对象，不是本轮未完成工作可无限续跑的理由。

## 采集、资源与归档

本轮共 45 个真实后端请求，全部 FINISHED_STOPPED；预请求 `/tokenize` 与真实 enqueue 的**完整 token IDs 45/45 相等**，prompt 范围 3,354–7,860，没有上下文溢出或输出长度截断。完整性独立核验见 [后端与工具关联](../results/real_development/stage0_codex32b_2026-11-01/backend-and-tools-audit.json)。四个含真实生成的会话上下文显式开启/退役，另保留一个无生成的基础设施失败会话；没有压缩、恢复、fork、辅助推理或引擎重试，不能将其未出现写为已验收。工具集中只有 exec_command 实际调用，write_stdin、图像及交互工具没有实测。

42 个合法工具调用全部由 CLI 执行且有带 ID 的实际 shell 起止记录；41 个唯一结果真正进入后端，最后一个因预算终止未转发。执行器记录的是从实际命令派生前到 shell 进程退出的边界，CLI JSON 接收时间单列；不能把它们混成 CLI 内部派发或所有可能后台依赖的完成时间。禁止遗留后台工作，单命令 90 秒限制由观察执行器实施；本轮没有命令实际超时。

两条未进入引擎的前端尝试单列：v2 空 tools HTTP400 和 C 的预算拒绝。前者已到 request_ready，因此 v2.1 coverage 显示 **45 projected + 1 unsupported**；后者更早拒绝，另在前端清单和原始错误中保留，不伪造 enqueue 或把 coverage 行数称为所有 HTTP 尝试数。总计 47 个前端尝试、45 个后端；CPU 合成捕获单独保留，不并入此统计。

7 个 producer 终端哈希、rank 0/1 和 engine 排空 checkpoint 完整；45 个保护区间，45 次 lookup 全保留，独立 v2.1 检查通过。本轮标签为 **41 U / 4 D / 0 C，unresolved=0**，仅用于这批诊断流，不并入自然开发发生率。容量淘汰 0、最大 refcount 1、最终 active slots 0；没有借此重验抢占、取消/重试、多 owner 共享或完整 Codex 生命周期。上轮相应已验证边界保持原身份。

2026-11-01 **17:13（Asia/Shanghai）** 已完成自有服务 checkpoint 与停止；8 卡无运行进程，每卡约 3.3 GiB 系统保留，/dev/shm 仅原有 CodeStore。全部运行与原始失败保留，180 项项目测试通过，218 个历史结果文件按旧小产物清单重新核验无变化。配置/实现哈希与本地证据清单见 [最终验证](../results/real_development/stage0_codex32b_2026-11-01/validation.json)。没有提交、推送或发布结果。

证据根目录：`/home/ma-user/work/leasepath/real-development-v1/codex32b-20261101-a/`。本轮服务另在 `codex32b-20261101-service-a/`，BF16、TP2、16K、128 tokens/block、192 blocks、设备 0/1；执行前重新核查设备空闲。192 blocks 仅作为初始容量，不能预先认定足以支持全部 Codex 多轮负载。
