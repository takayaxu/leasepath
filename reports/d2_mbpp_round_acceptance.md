# D2 MBPP 派生执行数据验收

日期：2026-10-30（Asia/Shanghai）。本轮已完成实际执行与独立评测，获得 **14 条满足全部质量条件的执行，覆盖 10 个不同题目**。完整主实验共 20 条，失败和校准轨迹均保留。所有数据仅用于开发；D2–D3 的多来源、长工具与并行依赖验收仍未完成。

配置见 [冻结配置](../configs/prediction/d2_mbpp_round.json)，运行方法与目录见 [采集说明](../docs/d2_mbpp_round.md)，机器可读结果见 [summary.json](../results/prediction/d2_mbpp_round/summary.json)。

## 来源与执行配置

使用 Google Research MBPP sanitized 固定 revision `f82046ba5aabbbb427dbfd38a254d26bff08b533`；来源文件 SHA-256 为 `ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9`。题目 ID 在运行前固定，没有按结果替换题目。参考实现仅在准备端验证测试，未进入 Agent 输入。

| ID | 实际函数任务 | 可见＋独立测试数 |
|---:|---|---:|
| 2 | 列表共同元素 `similar_elements` | 43 |
| 3 | 判断非素数 `is_not_prime` | 54 |
| 4 | 最大的 n 个数 `heap_queue_largest` | 43 |
| 7 | 筛选长单词 `find_char_long` | 43 |
| 8 | 列表平方 `square_nums` | 43 |
| 9 | 字符串旋转周期 `find_Rotations` | 70 |
| 11 | 删除字符首尾出现 `remove_Occ` | 43 |
| 12 | 按行和排序矩阵 `sort_matrix` | 43 |
| 14 | 三角棱柱体积 `find_Volume` | 43 |
| 16 | 小写下划线字符串 `text_lowercase_underscore` | 43 |

共 10 个可见测试与 458 个独立测试。独立评测只复制模型候选源码，重新放入原始测试；不采用 Agent 自报的测试结果。额外边界测试在运行前已用参考实现校验。逐题测试哈希、函数签名与来源见 [task-provenance.json](../results/prediction/d2_mbpp_round/task-provenance.json)。这些是 MBPP 派生开发任务，不是原生 GitHub issue，也不是官方 MBPP pass@1。

服务沿用 Qwen2.5-Coder-7B-Instruct BF16、16K 上下文、Ascend 910B2 单卡与 vLLM-Ascend。模型 revision 为 `c03e6d358207e414f1eca0bb1891e29f1db0e242`，mini-swe-agent 2.4.6 commit 为 `04d809ceab9df28f9adaed044884180159172930`。后端兼容身份为 `8c2c272c9e4510c4dae33537bbe75970e56a8e7dfdbfdf213b7283f07c994dfe`，本轮未切换服务或模型。

最终两组均使用官方 `mini_textbased.yaml` 的本地适配、明确函数签名和可见样例；最多 30 步、600 秒/题、60 秒/工具、2,048 输出 tokens。贪心为 temperature=0、top_p=1；采样为 temperature=0.7、top_p=0.8、top_k=20、repetition_penalty=1.1；seed 均为 23。

runner 强制第一次实际命令读取 README、源码与可见测试；拒绝交互编辑器，并在当前源码通过可见 pytest 后才接受完成标记。被拒绝动作记录为协议反馈，不冒充真实工具执行。实现与命令由模型生成，没有人工修补候选答案；独立测试不用于反馈修复。因此合规率反映受约束执行协议下的结果，不能外推为无约束自主能力。

## 独立评测与质量门槛

| 配置 | 独立测试全通过题数 | 全部执行质量条件通过 |
|---|---:|---:|
| 原始 stub 基线 | 0/10 | 不适用 |
| 单次函数生成 direct | 10/10 | 0/10，非完整 Agent 执行 |
| mini 文本提示，无强制流程 | 10/10 | 0/10，未实际检查文件，部分未测试 |
| 最终 greedy-guided | 8/10 | 8/10 |
| 最终 sampled | 7/10 | 6/10 |

质量条件在最终运行前声明：正常提交、实际源码修改、保护文件未变、真实检查/编辑/成功 pytest、独立测试完整且全通过、无过量重复命令循环，以及后端事件审计通过。全部 20 条主执行与逐项条件见 [execution-quality.json](../results/prediction/d2_mbpp_round/execution-quality.json)。

贪心失败：ID 7 的 sed 编辑丢失正则转义并循环至步数上限；ID 9 通过可见样例，但旋转逻辑未通过 54 个额外断言。采样失败：ID 2、4 编辑后语法错误并循环至上限；ID 12 最终源码独立测试通过，但测试文件被改动、没有有效可见 pytest 成功记录；ID 14 编辑错误与循环后发生 HTTP 400。

采样参数没有改善本轮完整执行合格率。后续简单任务采集优先沿用 greedy-guided，并保留采样作为独立对照；这只是十题开发实验的观察，不构成模型或解码策略的普遍排名。

早期长仓库提示和仅补 API 的诊断运行也有循环，已保留停止原因及原始轨迹，见 [calibration-records.json](../results/prediction/d2_mbpp_round/calibration-records.json)。未把被中止的运行补记为成功。

## 后端审计与数据范围

逐请求检查 ready/enqueue/dispatch/lookup/release 的顺序、实际 tokens、兼容身份、完整块前缀、正常释放及 prefill/cache 计数；全局检查观测进程序号。无法通过审计的整个 workflow 不导出主任务标签。

采样 ID 14 的最后一次客户端请求缺少后端阶段，轨迹记录 HTTP 400，服务日志也记录 400。本轮没有保存该响应正文，不能确定其具体原因，不能将其补写为 lookup 或自行宣称是上下文溢出。该 workflow 整体隔离，原始请求、先前成功请求和任务失败仍完整保留。最终 **20 条主执行中，19 条进入通过后端审计的完整标签集，14 条进入执行质量合格子集**。审计通过不等于任务成功。

逐条结果见 [backend-audits.json](../results/prediction/d2_mbpp_round/backend-audits.json)。合格轨迹的实际工具命令已逐条核查，未见读取工作区外参考代码、独立测试或网络答案的行为。公开题目可能在预训练数据中，这项检查不能排除记忆。

| 数据集 | workflows | 请求 / episodes | U | D | 自然 C | 快照 |
|---|---:|---:|---:|---:|---:|---:|
| 可审计完整集（含任务失败） | 19 | 185 | 166 | 19 | 0 | 729 |
| 执行质量合格子集 | 14 | 76 | 62 | 14 | 0 | 285 |

合格子集共 717 行风险区间：waiting=432、U=243、D=42。这些是同一快照在不同风险区间的行数，不是独立任务数量。76 次请求均观察到物理缓存命中；miss 也算 U 的检查仍由首轮独立受控真实推理提供。

冻结 raw SHA-256：`dfc5197ddee4b8ba34fd178493411e3f02c4620a038c9aa67921d4c3881fe318`。导出文件哈希见各 dataset/hazard summary，摘要清单见 [SHA256SUMS.json](../results/prediction/d2_mbpp_round/SHA256SUMS.json)。

## C 与风险区间

runner 在任务结束时明确关闭上下文，剩余开放关系生成 D，再结束观测，因此主实验自然 C=0。小窗口内未见 U/D 的已观测部分仍为 waiting/h0，不能为了补齐类别插入等待或伪造自然 C。

本轮新增逐快照风险区间 `U/D/waiting` 导出，区间右边界为 0.01、0.03、0.1、0.3、1、3、10、30、60、120、300、600 秒。真实 C 的部分区间屏蔽 loss，之后不生成训练行。固定 H=0.01、0.1、1、10 秒的行政删失作为独立派生视图保存，不覆盖完整标签。统计见 [qualified-hazard-summary.json](../results/prediction/d2_mbpp_round/qualified-hazard-summary.json)。没有训练或报告主模型预测指标。

## 保存与复核

本机根目录为 `/home/ma-user/takaya/leasepath`：

- `.artifacts/d2-next/runs/`：完整候选源码、补丁、模型轨迹与工具记录。
- `.artifacts/d2-next/evaluation-runs/`：独立测试结果和 JUnit。
- `.artifacts/d2-next/frozen-round/`：追加日志的冻结副本、服务日志、运行配置与采集/导出源码快照。
- `.artifacts/d2-next/datasets/all-main/`：19 条可审计主执行的事件、episode 标签、快照与 targets，包含任务失败。
- `.artifacts/d2-next/datasets/qualified/`：14 条质量合格执行；其余失败保存在完整记录中。
- `.artifacts/d2-next/hazards/`：完整/合格集的风险区间与独立行政截断视图。
- `results/prediction/d2_mbpp_round/`：可提交的小型运行、评测、质量与哈希清单。

原始 journal 同时含历史及校准事件，各派生数据集按冻结 workflow ID 筛选。首轮数据仍位于原先的 frozen-first-round 与 d2_first_round 目录，没有覆盖。原始大文件与候选轨迹仅保存在本机 `.artifacts/`，不随 Git 自动分发。

## 验证与限制

本轮代码检查为 **81 项 pytest 通过**、D1 50 项检查通过、事件契约 8 个正例接受/16 个反例拒绝；CPU 与 Agent 环境均通过 `pip check`。日志位于 `.artifacts/d2-next/pytest-final.log` 和 `regression/`。服务健康检查通过。[最终复核清单](../results/prediction/d2_mbpp_round/verification.json) 已核对 20 份候选哈希、14 条合格执行的命令与 raw 一致、6 个数据集输出哈希，并从截至快照时刻的事件前缀重新计算完整集 729 个与合格集 285 个快照，全部一致；74 个本地 Markdown 链接与 `git diff --check` 通过。

14 条合格执行只对应 10 个公开题目，且按最终成功筛选，存在选择偏差；不能当作无条件工作负载分布。完整与合格集须并行保留，配置须分层报告。同一题目的不同配置、同一 episode 的所有快照须保持同组，本轮统一 `pilot_development_only`，没有独立测试切分。

本轮证明简单任务能产生经过评测和审计的真实执行数据。短函数题尚未覆盖大型仓库、慢构建、并发与真实复合依赖；后端仅有请求边界块快照，不具备完整物理分配/淘汰生命周期；队列长度保持 unknown；尚未验证完整探针开销或在线策略收益。扩大采集需补充这些覆盖，不能以快照行数代替独立任务量。
