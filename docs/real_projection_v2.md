# 真实请求投影 v2：阶段 0 补救冻结语义

初稿冻结于 2026-11-01，先于补救模型运行；语义审查修正为 v2.1，原初稿哈希/副本保留在 preregistration。v0.1 契约和旧导出不变。新版原始引用、请求映射、保护区间、关系事件与标签分别导出，不将多次 lookup 压成伪造的单次 lookup；也不要求 cache＋总 prefill=prompt（抢占后可能重算）。本版本不是已有预测器的训练输入。

- 每个 request_ready 必须显式给 owner、context_version、logical_request_id、attempt_id、purpose、wire_api、retry_of（可为 null）。client request_id 唯一；同 logical_request_id 的重试有不同 attempt_id，并指向更早 attempt。同一重试链不得改变 owner、version 或 purpose。辅助请求用途独立记录，不作为新任务。**v2.1 修正：用途名称不能禁止同 owner/version 的真实兼容 lookup 形成 U。** 初稿/受控日志已有的 `auxiliary_no_demand` 名字保留作追溯，解释为历史用途名，绝不据此漏记需求或正常 release 的 idle；新名称为 `auxiliary`。若辅助请求确实不属于同一关系，必须显式给出另一 owner/context。
- 引擎关联由服务实际 request_id 的严格 API 命名规则核对，chat=`chatcmpl-{client_id}-{suffix}`，completion=`cmpl-{client_id}-0-{suffix}`，只接受一个精确命名规则匹配，并核验 enqueue 配置/cache salt。显式 backend_link（若有）仍须核验该规则。无法映射的请求进入全覆盖清单，不能进入标签。
- 同一 owner/version/compatibility/cache_salt、在 idle 时已经选定的完整输入前缀，以首次符合条件的真实 lookup 形成 U，包括 miss。保留每次 lookup 的源 stream/seq 和时间；后续相同 lookup 不重复关闭关系。release 之前的 lookup 不可作为它自己的未来需求。
- enqueue 到终端 release 形成 pending/active 保护区间；每次 preemption_release 单列，不能创建 idle、D 或任务结束。preempted 请求仍 pending，不能因此进入可淘汰 idle owner 候选；真实物理块的资格只来自独立 block refcount/代次/候选队列证据。
- 正常 terminal release 按当前输入最长完整 block 前缀创建新 idle；需完整物理 hash 块证据。abort 终止 attempt、解除该 attempt 的保护，但不创建新 idle、不关闭已经存在的上下文关系。retry 按显式旧 attempt 关联，之后真实 lookup 可以满足此前开放关系。显式 retire 后允许已 pending 请求排空，保留其原始事件和保护区间，但不复活该版本的 idle。
- context_open/版本必须显式存在；context_retire 必须给 owner/version，关闭该版本的所有尚开放关系，不能删除别的 owner 的共享块。compact 是显式 retire 旧版本加 open 新版本；不会推断全部子前缀死亡。restore 只恢复明确开放的旧版本；已 retire 的版本不能复活为同一关系，需新版本。
- fork 记录 parent/child owner/version；不推断子 owner 的 idle 前缀。只有该 owner 自己的真实正常 release（或未来单独版本化的物理继承证据）才能建立 idle。父 owner 的 D 不关闭子 owner 的关系。
- observation_end 按 owner 作用域关闭观察，所有仍开放关系形成 C。后续清理不回填 D；缺 producer、序号断流、跨 boot、缺终端哈希、未排空 checkpoint 等拒绝整个标签导出。无法确定观察结束的 owner 不生成 C，而标为未完成。
- 每个 backend request 均输出 coverage，包含映射状态、用途、复杂事件计数和不支持原因。旧 B 缺少 attempt/purpose/wire_api、部分受控版本和 scope，保留严格关联可证明的普通流；缺字段明确列出，不从 issue 内容、事件顺序猜测。旧 B 的 preempt/abort/retry原始生命周期可审计，不据此虚构缺失的 owner/version。
- 未支持请求若在关系存活期存在可能属于该 owner/version 的兼容 lookup，则该关系保留 unresolved，不能制造首个 U、D 或 C。缺字段不能只作为覆盖统计警告后继续声称标签完整。

导出 `events.v2.jsonl` 保存全部关联的真实 lookup/release/preemption 和显式上下文边界，`labels.v2.jsonl` 只保存首次 U/D/C 与 start/end 源引用，`coverage.v2.jsonl` 覆盖所有 backend 请求及未关联客户端尝试，`protection.v2.jsonl` 保存实际保护范围。实际 token IDs 和参数仍保留本地数据盘，不进入预测特征。
