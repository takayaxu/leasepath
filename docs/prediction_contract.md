# KV 复用预测数据契约

状态：D1 首轮 pilot 契约已冻结，已通过[受控实验验收](../reports/prediction_d1_acceptance.md)。本文定义预测终点和信息边界；不表示现有轨迹已包含下列全部特征，也不表示实时预测器回调已经实现。

实际已采集的任务、数量、字段可用范围、质量子集及本机路径见 [统一数据集说明](dataset_card.md)。本文的目标字段契约与数据集中目前已观测的字段需分别理解。

## 预测单位与结局

每条样本是在时刻 `t0` 对一个处于 idle 状态的 `owner-context-version` 及其兼容前缀生成的快照。episode 标识为 `idle_episode_id`；前缀由 (`compatibility_id`, `prefix_token_ids`) 标识。KV 是否仍驻留、缓存是否命中、需求到来前数据是否已被淘汰，都不改变标签。

预测目标是从 `t0` 开始，到以下最早结局的时间：

| 标签 | 定义 | 训练含义 |
|---|---|---|
| U：合法需求 | 后续第一个 `cache_lookup` 请求与 idle 前缀兼容，且请求以该前缀开头。经校验的 `legal_demand` 事件用于关联此 lookup。 | 竞争事件：下一次合法 lookup，包括物理缓存未命中的情况。 |
| D：retire | 显式 `context_retire` 结束该 owner-context-version 的复用关系。 | 竞争事件：关系已终止；不得从工具结束、timeout、淘汰或其他 owner 的 retire 推断。 |
| C：右删失 | episode 仍开放时发生 `observation_end`。 | 只表示观测时长内没有看到事件；后续区间未知，不能当作负样本。 |

第一个 U 或 D 会关闭 episode。同一时间戳下以记录中的事件顺序为准：若 U 或 D 先于 `observation_end` 出现，则属于已观测事件，不是删失。终点是 lookup；`request_ready`、enqueue、dispatch、tool start/end 和 request completion 均不能替代。之后出现不兼容的 lookup，也不能证明该前缀今后永不复用。

注意区分两个起点：`t_idle` 是 episode 开始的时间，`t0` 是当前快照时间。输入中的已等待时长是 `t0 - t_idle`；预测标签的剩余时间是 `结局时间 - t0`。现有 `derive_idle_labels` 导出的是从 `t_idle` 起算的 episode 标签，后续快照数据集还需要换算为剩余时间。例如 idle 在 5 ns 开始、9 ns 拍快照、14 ns lookup，则已等待 4 ns，标签剩余 5 ns，完整 episode 时长 9 ns。

同一 episode 产生的快照彼此相关，必须随完整 session/来源项目放在同一个数据切分中。采样或损失加权应按 episode 分组，避免把长 episode 当作多个独立 workflow。

### 时间窗口内等待与右删失

观测结果 `U/D/C` 与模型在风险区间 k 的输出 `U/D/h0` 是两套概念。`h0` 表示已完整观察该区间、仍未发生 U 或 D；C 表示观测在关系开放时结束，之后的结果未知。C 不是第三个竞争事件，不能直接作为 softmax 的第三类；它用于限制哪些区间可以计入 loss。

例如 t0=0、2 秒后发生 U：在 0.1 秒窗口内是已知等待，完整标签仍是 U、remaining=2 秒。若预先规定只保留至 0.1 秒的观测，则可以另建行政截断视图，记录 `observed_time=min(T,H)`、`event_observed`、`horizon_s=H` 和 `label_scope=administrative_horizon`；其中 T>H 的记录在这个派生视图中属于 C。不得用它覆盖原始完整轨迹的 U/D。若真实观测在 H 之前就结束，则 H 的结局未知，不能填成“已等待完整 H”。

首轮 runner 在终止任务时显式丢弃上下文，然后结束观测，因此最后的开放关系生成 D，自主数据的 C=0。小窗口仍有大量 h0：首轮 100 个 episode 从 idle 起点看，0.01 秒内全部仍等待，0.1 秒内 41 个仍等待，见 [首轮窗口统计](../results/prediction/d2_first_round/horizon-summary.json)。没有 C 本身不表示标签错误，也无需为凑齐类型制造删失。如果以后预算退出只是暂停、上下文仍允许恢复，则应保留关系并结束该观测区间，不能沿用当前“明确丢弃”的 D 政策。

[MBPP 派生执行轮](d2_mbpp_round.md) 已实现 `scripts/export_d2_hazards.py`：逐快照导出风险区间 `U/D/waiting`，真实 C 的部分区间设 `loss_mask=false`，不生成其后的训练行。固定 H 的行政删失另存 `administrative_views.jsonl`，不替换完整观测标签。区间边界、累计窗口统计及输入/输出哈希见 [合格子集摘要](../results/prediction/d2_mbpp_round/qualified-hazard-summary.json)。这些 targets 已用于 [经验基线拟合](../reports/empirical_baseline_review_1.md) 和 [共享 GBDT 的母题分组开发评测](../reports/gbdt_review_2.md)。两种模型在本轮使用相同的工具、phase 和 elapsed 桶信息；独立来源/时间测试尚待完成。

## 共同特征契约

首轮预测器和经验基线使用相同的快照时刻特征。只有在 `t0` 实际可见的字段才能使用；缺失值应保留为缺失，并明确表示 unknown，不得事后补齐。

| 特征组 | `t0` 时允许使用的字段 |
|---|---|
| Owner 与前缀 | 仅用于切分和聚合的不透明 owner/context-version 分组键；兼容类别；前缀长度/token 数。原始 token 内容不作为模型特征。 |
| 工具与请求元数据 | 当前工具名称/类别、命令类别、输入长度、已知规模档位、显式 timeout；仅在当前请求中可见时使用。 |
| 当前执行状态 | idle 已持续时间、已观测 phase、截至当前的 retry 数、当前请求是否已入队。未知的 phase/retry/队列状态保持 unknown。 |
| 过去历史 | 最近最多 3 次和 8 次已完成工具调用的摘要、同类调用耗时统计、近期失败情况；只使用 `t0` 前已完成的记录。 |
| 已观测依赖 | 当前已知未完成依赖数、已知 AND/OR 等待语义、分支工具类别计数，以及当前已观测分支的 elapsed 最大值/均值。不重建未来或完整 DAG。 |
| 服务状态 | `t0` 时实测的队列长度或已观测排队工作量、当前 KV 压力和当前执行状态。 |

共同基线可只使用工具/参数类别和 elapsed；共享模型首先必须在同一组特征上比较。phase、历史、依赖和服务状态分别作为后续消融组加入。当前事件契约样例尚未实现这套完整特征。

### 可执行的最小快照

`src/leasepath/prediction_state.py` 提供 D1 验收使用的最小投影；D2 的 `d2_dataset.py` 复用它导出最小特征快照及独立 targets，完整特征与多来源切分仍待实现。`snapshot` 仅消费指定 run 中截至 `as_of_ns`（含该时间）的事件，不读取后续 payload，也不调用需要未来事件的完整轨迹校验。输入应事先符合事件顺序和结构约定；在线使用时只能传入已经观测到的前缀。

返回对象的外层 ID 和 `snapshot_ns` 是关联元数据，不能作为模型输入。模型只能收到通过 `validate_features` 校验的 `features`，白名单为：`elapsed_idle_s`、`compatibility_id`、`prefix_tokens`、`tool_name`、`tool_elapsed_s`、`phase`、`retry_count`、`is_queued`、`queue_length`。额外字段直接拒绝，未知值为 `null`，不填成零。

工具和请求状态必须通过事件上的 `idle_episode_id` 显式关联。缺关联时不能根据同 workflow 或后续 lookup 倒推归属。单工具关联可产生工具类别和已运行时长；多工具细节需要后续依赖摘要，不能随意挑选一个代表。只有已观察到工具运行或关联请求排队时，才生成对应 `waiting_tool` 或 `queued` 阶段；其他 phase 保持未知。

全局 `queue_length` 只有在采集器明确保证服务 enqueue/dispatch 事件完整、且初始队列为空时才计算（`complete_service_events=True`）。默认未知。基础事件样例没有这些归属与完整性保证；D1 在独立的受控样例中显式补充它们，不能宣称原始采集已经支持这些字段。

该投影覆盖最小输入子集；命令参数、历史、重试更新、完整依赖摘要和 KV 压力等仍需 pilot 探针。验收中的字段隔离不等于尚未实现的在线策略回调隔离。

### D2 扩展状态 v1

[秒级扩展轮](d2_expansion_v1.md) 已在单独的 `features-expansion-v1.jsonl` 导出当前命令类别、预先声明且当前测试可见的工作量、timeout、已结束工具历史及已观测失败测试次数；实现为 `d2_expansion.py`，与最小九字段及已冻结 GBDT 接口分开。`prior_failed_tests` 只计算截至当前已结束的失败测试，不声称知道重试意图；该轮全为零。当前工具的结果与总耗时必须等到 tool end 后才可进入历史。原 `retry_count` 和全局 `queue_length` 继续 unknown。完整依赖和 KV 压力尚未实现。

扩展轮显式配置包含 1、2、3、5 秒的固定 elapsed 采样点，仅导出 episode 仍开放的时刻，并按 episode 归一采样权重；调用导出器时未指定新参数仍沿用历史采样设置。新增数据与旧数据的未加权快照数量不直接可比，后续训练必须继续按 workflow/母题加权与分组。

### D2 显式重测状态 v2

[修复扩展 v2](d2_repair_v2.md) 的 `features-repair-v2.jsonl` 单独加入 `current_suite_attempt`、`current_suite_is_retry`、`current_source_changed_since_failure`、`suite_retries_started`、`failed_full_suites`、`last_full_suite_passed`。同一完整测试必须先有已结束的失败，当前重测才可在实际开始时引用它；仅有过去失败数不能推出当前一定在重试。源码变化在当前测试开始时通过已知哈希比较，不能读取未来是否最终修复成功。审计引用 ID 和源码哈希不作为模型输入。

该轮失败计数只在真实 tool end 后递增，已观测 0/1/2；原最小 `retry_count` 仍 unknown，不直接替换成新字段。未执行的协议拒绝不是测试失败，会话结束后的独立测试失败也不能回填。中/大两档以及 v1 同母题必须按 `source_group:parent_task_id` 同组，而不能按新实例名切分；分组存入新清单及增强文件外层，不进入模型特征。[训练 v2](../reports/prediction_v2_50parents_review.md) 的 `prediction_merged.py` 已按该映射合并四轮；原十题入口保持旧协议。

训练 v2 的 `evaluation-metadata-only.jsonl` 可包含最终 episode 时长和执行质量，用于事后分层评分，禁止交给模型。已观察失败和当前重试也只用于本轮分层；共同学习输入仍为 tool/phase/elapsed 桶和风险区间。源数据中未采到的增强字段保持 unknown，不将缺失补成“0 次失败”。全部模型在同一折训练支持上评分；缺少训练风险区间时输出 null 并报告排除数量，不借验证标签补齐支持。

## 标签与禁止输入隔离

仅标签字段包括未来事件类型/时间、下一次需求的 request ID 和 token、未来兼容性判定、显式 retire 时间/原因，以及 observation 结束时间。它们只能用于离线构造标签和评分。`legal_demand` 可以用来校验标签是否对应引用的 lookup；它本身及未来 lookup 内容都不能进入快照特征。

特征构造和因果决策回调绝不能接收：未来请求顺序/列表、未来工具时长或结果、未来 phase/依赖变化、未来队列长度/压力、未来缓存命中或淘汰、标签字段及离线动作脚本。不得用后续事件倒填 phase。环境推进器可以保留这些数据来推进回放和生成标签，但预测器/策略边界只能收到按白名单序列化的当前快照。

## D1 回放权限审计

`src/leasepath/source_replay.py` 支持具名因果基线（`lru`、fixed TTL 和 retired-first 变体），以及单独命名的 `offline_script` 诊断策略。因果运行拒绝传入未来脚本；离线诊断可以接收逐请求脚本。引擎持有完整 workload 以推进依赖并产生结局，尚未实现预测策略回调。早期的 lease 回放器及旧实验入口已移入项目外归档，不属于当前运行链。

因此，现有输出适合因果基线比较和事件/标签校验，但不能证明新预测器已在隔离的运行时权限边界下评估。离线脚本只能作为 oracle 诊断，不能报告成预测器的在线决策。后续实现需要增加一个只接收当前特征白名单、而未来 workload 状态继续留在环境推进器中的回调。

## 现有证据与限制

`schemas/trace.schema.json` v0.1 和 `src/leasepath/trace.py` 已表示 `idle_start`、`legal_demand`、`context_retire` 与 `observation_end`；校验器检查前缀兼容性，并要求 U 与同一时刻的 lookup 关联。`derive_idle_labels` 会导出 U/D/C 标签。事件契约验收覆盖 hit/miss 标签不变性、工具结束但未 retire、工具仍运行或请求仍排队时的删失，以及观测边界上的事件顺序。这些检查只能验证已提交的事件，无法证明采集器没有漏记真实需求，也无法证明声明的 retire 在语义上成立。

当前 source D6 pilot 是选定 BFCL 参考 workflow 的固定轨迹数据，包含串行及组合式 fanout/join 场景。它不是自然到达/处理轨迹，没有通用的 idle-timeout U/D/C 样本群，不能作为计划中特征均已观测到的证据。受控/合成等待仍是机制检查，不是实测工具时间标签。Pilot 收集阶段必须先报告特征可用率和 evidence kind，再决定是否拟合预测器。

D2 实测 lookup 的 token 与配置身份存入 `data.backend_lookup`，校验器将 U 与这份后端证据逐条比较。`backend_blocks` 是后端请求边界观测，不使用 D1 合成数据的完整物理生命周期声明。D2 首轮的采样规则、显式 runner close、覆盖缺失及观测限制见 [D2 说明](d2_first_round.md)；自主任务与受控缓存清空检查分别导出。
