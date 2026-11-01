# 阶段 0 补救 Review（2026-11-01）

**阶段 0 补救已完成本轮执行与 review，整体仍未通过，阶段 1 暂缓。** 工具宿主回退已修复，一般请求投影 v2.1 在已验证范围内通过；两轮调试独立正确性均为 **0/3**，第二轮三份生产补丁均有 SyntaxError。按预登记的两轮停止条件结束当前 Agent 链，没有执行新两题、最终五题验收或完整 Agent on/off 测量。

| 验收项 | 判定 | 证据与限制 |
|---|---|---|
| A1 私有工具环境 | 通过 | CPU 3/3，六次实际 Agent 环境逐题预检，命令、源码与依赖导入路径固定 |
| A2 通用工作流 | 控制已实现，行为未通过 | 提示词、循环/无 diff/提交反馈有版本；实际未形成稳定修复闭环 |
| B 一般请求投影 | 已验证范围通过 | 185/185 后端请求投影，306 lookup 全保留，独立核验通过；10 个失败前端尝试和旧 B 33 个缺映射请求仍单列 |
| C 任务质量 | 不通过，触发停止条件 | v1 0/3、v2 0/3；五题最终验收未运行，不能写成 0/5 或择优拼接 |
| D 完整 Agent JCT | 未测量、证据不足 | C 前置条件未满足；没有 on/off 配对或非劣置信区间 |

[机器摘要](../results/real_development/stage0_revise_2026-11-01/summary.json)、[逐题人工行为审查](../results/real_development/stage0_revise_2026-11-01/manual-behavior-review.json) 与 [第二轮冻结哈希](../results/real_development/stage0_revise_2026-11-01/round2-freeze.json) 可独立查阅。

依据：[补救计划](../revise.md)、[上一轮 Review](real_development_stage0_review_2026-11-01.md)。本轮使用新目录，保留上一轮全部失败、原始数据和导出；不人工修改 Agent 候选补丁。

## 范围与冻结

本地证据根目录为 `/home/ma-user/work/leasepath/real-development-v1/`，以下路径均相对此目录：

| 目录 | 内容 |
|---|---|
| `stage0-revise-20261101-preregistration/` | 原计划、初稿语义、v2.1 修正、两轮配置和实施文件的冻结副本及哈希 |
| `stage0-revise-20261101-cpu-a/`、`cpu-b/`、`cpu-c/`（同前缀） | CPU 工具预检各次结果；最后一次验证实际依赖导入位置 |
| `stage0-revise-20261101-legacy-v2-a/` | 初稿投影，保留历史含义 |
| `stage0-revise-20261101-legacy-v21-final/` | v2.1 对旧 B 运行的离线复核与普通流回归 |
| `stage0-revise-20261101-service-a/` | 本轮 TP2 服务、受控请求、两轮 Agent、独立验收和完整原始流 |

模型保持 Qwen2.5-Coder-32B-Instruct，revision `381fc969f78efac66bc87ff7ddeadb7e73c218a7`；BF16、TP=2、16K、KV 192 blocks、max_num_seqs=8。实际设备 0/1，端口 8210。mini-swe-agent 固定 commit `04d809ceab9df28f9adaed044884180159172930`。调试任务仍为 Flask-4992、Flask-5063、pytest-9359，均属于已使用来源组。

两轮均为 greedy、seed=23、30 步、每次最多 768 输出 token、整项任务 900 秒、单工具 90 秒。先 review 第一轮，再冻结第二轮；没有第三轮或预算扩张。配置文件分别为 [v1](../configs/prediction/real_development_revise_agent_v1.json)、[v2](../configs/prediction/real_development_revise_agent_v2.json)。

预登记的新增题是 `pallets__flask-4045`、`pytest-dev__pytest-11143`：第一道未使用 Flask、按字典序第一道未使用 pytest。选择发生在模型成绩之前；是否执行由 C 的前置门槛决定。最终五题验收仍要求同一冻结配置 ≥3/5、成功覆盖 ≥2 仓库、新题 ≥1/2，不拼接不同配置成绩。

## A：工具环境和修复工作流

[环境实现](../src/leasepath/real_environment.py) 为每题复制独立冻结 venv，包装 `python/python3/pytest/py.test/flask/pip` 到该环境的解释器模块入口。PATH 只含私有包装目录、`/usr/bin:/bin`；单独复制 rg 可执行文件，移除宿主 Python/Conda 环境变量。源码 PYTHONPATH 指向当前 checkout 的 src。保持离线依赖、4 CPU 亲和性和单线程限制。

从 Agent 实际使用的 bash/taskset 环境验证命令解析、sys.executable、sys.prefix、源码位置、依赖版本及导入文件。Flask 的 Click/Werkzeug/Jinja2 与 pytest 的 pluggy/packaging 均来自各题私有环境；Flask-5063 不再回退宿主 flask/Click。CPU 最终 3/3 预检通过，每次真实 Agent 运行及独立验收仍另存本题环境证据。最初缺少 rg 的预检和之后修正都保留。宿主入口、错误源码、错误解释器负例纳入回归。

[runner v2](../scripts/run_real_development_tasks_v2.py) 使用通用库源码修复说明，要求检索/读取实现、保持复现前提、修改生产源码、窄测试和完整 diff。完整补丁包含暂存、已提交和未跟踪文件。提交检查及自动行为标记只提供反馈，不能代表正确性。新增控制属于版本化 Agent 配置。

v1 对连续相同无进展操作在第 3 次反馈、第 6 次停止，提交最多反馈一次。v2 另外按最近 12 次动作识别相同失败输出且补丁不变的循环，3 次反馈、6 次停止；每 6 次未改变补丁提供阶段反馈，写入命令无 diff 时明确反馈。每次工具输出首次交付给模型时最多 1,200 字符首尾摘要，完整原输出仍在 trajectory 的 extra.raw_output；这是配置变化，不是事后压缩历史、增大上下文或增加预算。

第一轮复核发现 mini-swe 的继承 query 包装对 HTTP400 自动重试 10 次，与该轮 manifest 所写“不自动重试”不符。原始尝试和错误文件保留；这些请求未进入引擎，不能计为成功后端请求，也不能事后伪造 retry_of 关联。v2 显式设置 `MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT=1`，错误状态及响应哈希进入 client_error。第一轮清单错误不能由第二轮修正追溯消除。

## B：请求关系投影

[语义 v2.1](../docs/real_projection_v2.md)、[attempt schema](../schemas/real_attempt_v2.schema.json) 和 [投影实现](../src/leasepath/real_projection_v2.py) 与 v0.1 分开保存。逻辑请求、attempt、backend、owner、context version、用途及显式 retry_of 分别追溯。每次真实 lookup 均保留；首次同 owner/version/compatibility/salt 的兼容前缀需求形成 U，包括 miss。preemption 保持 pending 保护，不创建 idle/D；abort 结束 attempt 保护但不推断关系死亡；retire 仅关闭明确版本，父 owner 的 D 不删除子 owner 的共享块关系。

正常 release 以当前输入的完整块前缀及物理 hash 证据创建 idle。已 retire 的 pending 请求可以排空，但不复活 idle。owner observation_end 只有在请求排空、producer 清单齐全、流哈希/boot/序号和 TP checkpoint 通过后才能支持 C。未支持请求若可能包含更早兼容需求，对应标签转 unresolved，不能靠剔除请求制造首个 U/D/C。

语义审查纠正了初稿的一个错误：`auxiliary_no_demand` 用途名不能阻止真实兼容 lookup 形成 U。v2.1 将其作为历史用途名兼容，按实际 lookup 和正常 release 投影；新名称为 auxiliary。初稿、初稿导出和受控 raw 中旧名全部保留，未改写历史日志。计数采用最终 v2.1，初稿计数不能作为最终验收。

旧 B 运行共 92 个 backend：59 个普通流有可证明映射，以 projected_legacy 保留缺字段提示；33 个受控请求缺 owner/version/attempt 证据，明确 unsupported，不推测填充。新版普通流为 U=56、D=3、C=0；59 条标签的 owner、开始/结束时间、结果逐条与旧版完全一致。独立核验覆盖 92 个 backend，59 个保护区间；原始抢占/取消的物理审计保留，但不等于旧数据新增关系字段。

本轮受控实际推理包括两条 12,032 prompt +256 decode 并发压力请求、真实抢占/重复 lookup、共享 parent/child、父版本关闭后子版本继续、辅助请求、流式取消后显式重试、compact 到新版本、restore 开放版本，以及明确观察结束。11 个后端请求、132 次 lookup、1 次抢占；其中一条压力请求有 122 次 lookup。最终受控标签 **U=5、D=4、C=1**，不代表自然发生率。

完整本轮合并 195 条前端尝试记录、185 个后端请求；185 个 backend 全部 projected，第一轮 10 个 HTTP400 前端尝试因未进入引擎而 unsupported，保留其配置清单和重试关联缺陷。所有 306 次后端 lookup 进入事件导出，185 个保护区间，184 条标签 **U=172、D=11、C=1**，unresolved=0。完整覆盖不表示这 10 个失败尝试的 logical retry 元数据已得到补全。

7 个 producer 均有终端哈希，producer 清单时间早于首个 enqueue，rank 0/1 checkpoint 排空，所有后端请求完整结束。块审计通过：504 次容量淘汰、333 次分配候选检查、最大 refcount=2，结束 active slots=0。标签、保护区间和全请求覆盖通过 [独立核验](../results/real_development/stage0_revise_2026-11-01/projection-independent-check.json)。[U/D/C 样例](../results/real_development/stage0_revise_2026-11-01/label-source-examples.json) 保留 start/end 的 stream、seq、单调时间和需求 backend ID；完整 token/块证据仍在本地 dataset/raw。

语义回归包含重复 lookup、抢占、abort/retry、版本关闭、辅助真实需求、未知兼容 lookup 隔离、退役后恢复拒绝和 pending 排空。旧 B 与本轮流的损坏副本分别验证五项拒绝：缺 producer、缺终端、跨 boot、缺 rank checkpoint、引擎未排空；不修改原 raw。结果见 [本轮负例](../results/real_development/stage0_revise_2026-11-01/boundary-negative-checks.json)。

## C：两轮全部结果与行为审查

独立验收使用全新 checkout、冻结源码哈希、私有环境，以及已冻结 FAIL_TO_PASS 和相关完整文件回归测试。先预检未改动的环境，再应用候选生产补丁；候选语法错误计为质量失败。候选测试改动不进入独立验收，也不向 Agent 提供参考修复。这是本地独立相关文件验收，不宣称官方 SWE-bench harness。

| 配置 | 任务 | 结束状态 | Agent calls / 成功响应 / HTTP 失败尝试 | JCT 秒 | 补丁字节 | 独立结果 |
|---|---|---|---|---:|---:|---|
| v1 | Flask-4992 | LimitsExceeded | 30 / 30 / 0 | 206.09 | 834 | 不通过；候选 SyntaxError，测试 exit 4 |
| v1 | Flask-5063 | LimitsExceeded | 30 / 30 / 0 | 95.52 | 0 | 不通过；55 passed、2 failed |
| v1 | pytest-9359 | HTTPError | 25 / 24 / 10 | 455.64 | 0 | 不通过；54 passed、1 failed |
| v2 | Flask-4992 | LimitsExceeded | 30 / 30 / 0 | 497.29 | 1,318 | 不通过；候选 SyntaxError，测试 exit 4 |
| v2 | Flask-5063 | LimitsExceeded | 30 / 30 / 0 | 906.62 | 5,804 | 不通过；候选 SyntaxError，测试 exit 4 |
| v2 | pytest-9359 | LimitsExceeded | 30 / 30 / 0 | 645.91 | 951 | 不通过；候选 SyntaxError，Python exit 1 |

v1 为 0/3。Flask-4992 读到源码后仍盲目 sed 修改多行函数签名，未能修复语法；Flask-5063 循环猜测符号和不存在的目录，空补丁；pytest-9359 运行宽泛测试，输出导致 prompt 15,617 +768 请求超出 16K 上限，随后继承重试 10 次。84 次成功响应 finish_reason 全为 stop，没有输出长度截断证据。

第二轮为 0/3。Flask-4992 已定位并读取实现，但反复插入 `mode` 参数、损坏文本；新增验证脚本只检查字符串存在，不能验证行为。Flask-5063 建立了可运行的应用复现，却将输出代码反复插入装饰器和函数周围，未修复持续出现的缩进错误。pytest-9359 读取实现后在复现脚本缩进上循环，最后只向生产代码添加了引号错误的 debug print，未实现修复。三个候选都在导入阶段失败，冻结目标测试均未运行成功；不能把自动“读取/验证候选”标记当作闭环。

v2 共 90 次成功响应，88 次 stop、2 次 length；两次截断均为 Flask-5063（第 25、28 次），全部 HTTP 尝试各只执行一次。不能沿用 v1 的“无截断”描述到 v2，也没有在见到截断后追加第三轮或放宽预算。Flask-5063 的实测 JCT 为 906.62 秒：mini-swe 在动作边界检查 900 秒墙钟预算，该值包含最后动作越过边界的时间，原始值不截短为 900 秒。

循环控制对连续相同命令和相同失败输出有界，但对不断改变错误补丁的行为不能保证停止；它没有补上模型的源码修复能力。本轮不据此归因模型的普遍能力，也不将 v1/v2 的多项配置变化当成单因素消融。完整独立结果见 [v1](../results/real_development/stage0_revise_2026-11-01/debug-round1-evaluation.json)、[v2](../results/real_development/stage0_revise_2026-11-01/debug-round2-evaluation.json)。

两轮后未形成稳定源码修复行为，执行计划 C 的停止条件。新两题未准备或执行，最终同配置五题验收未启动，阶段 1 的 15 个候选仍全部未使用、环境尚未验收。下一条链需要另建 Agent 配置或模型版本，并重验受影响的服务、模板、资源和任务质量；本轮不继续调提示词或增加预算。

## D、资源与整体判定

D 的前置条件是 Agent 配置、质量与投影稳定；目前未启动完整 Agent on/off 测量。预登记方向为按任务聚类、配对 log JCT 的几何均值增幅、单侧 95% 上界 ≤2%，失败与超时保留。没有配对清单、样本或置信区间时，不能给出非劣结论。历史短请求 +0.34% 仅保留其原有描述性含义，不能替代完整 Agent JCT。

JCT 计时从 agent.run 进入到返回或异常，包含编排、工具和推理；任务准备、服务启动与归档另计。实际并发范围为两条长受控请求和逐题串行 Agent；max_num_seqs=8 不能据此宣称 8 路 16K Agent 长跑通过。全程没有采集自然开发天数、训练新模型或运行 KV 预测策略。

自有服务已在全部请求结束后完成 TP checkpoint 并停止。最终 [资源快照](../results/real_development/stage0_revise_2026-11-01/final-resource.json) 与 npu-smi 原文显示 8 卡均无运行进程，每卡约 3.3 GiB 系统保留；本轮共享内存和 semaphore 清理完成，无新增残留 IPC，原先已有的 CodeStore 保留。

项目 [pytest](../results/real_development/stage0_revise_2026-11-01/pytest-final.log) **151 passed**，D1 50 项、事件契约 8 个正例与 16 个反例继续通过；[147 个历史文件哈希](../results/real_development/stage0_revise_2026-11-01/historical-hashes-final.json) 无变化，冻结探针及第二轮四个实现/配置文件哈希核对一致。阶段 0 的质量和完整 Agent 开销门槛仍未满足，其他已通过项目不能抵消这两个缺口。

## 离线复核入口

以下命令读取已完成的本轮证据，不启动新 Agent 尝试。先定义实际路径，`--output` 使用全新文件/目录：

```bash
cd /home/ma-user/takaya/leasepath
REVISION_RUN=/home/ma-user/work/leasepath/real-development-v1/stage0-revise-20261101-service-a
PYTHONPATH= .venv/bin/python scripts/check_real_projection_v2.py \
  --run "$REVISION_RUN" --dataset "$REVISION_RUN/dataset-v21-final" \
  --clients "$REVISION_RUN/controlled-v2/controller.jsonl" \
  "$REVISION_RUN"/debug-round1/*/runner.jsonl "$REVISION_RUN"/debug-round2/*/runner.jsonl \
  --output /tmp/leasepath-revise-v21-independent-check.json
PYTHONPATH= .venv/bin/python scripts/check_real_projection_v2_boundaries.py \
  --run "$REVISION_RUN" --clients "$REVISION_RUN/controlled-v2/controller.jsonl" \
  "$REVISION_RUN"/debug-round1/*/runner.jsonl "$REVISION_RUN"/debug-round2/*/runner.jsonl \
  --output /tmp/leasepath-revise-v21-boundary-check.json
```

完整数据文件哈希、最终实现哈希与仓库小产物清单保存在本轮结果目录，旧 Review 和旧导出保持原版本。当前证据不证明自然 lookup 发生率、预测器优势或 KV 策略净收益。
