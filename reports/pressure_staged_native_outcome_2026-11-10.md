# 本轮原生诊断结果：执行完成，完整审计未通过

2026-11-10北京时间15:25，后台已停止，原始证据归档校验及自有tmpfs清理完成。GPU5/6/7在执行结束后各4MiB，未留下本轮模型进程。

| 项目 | 结果 |
|---|---|
| 模型完整校验 | 两次独立SHA256均匹配冻结模型；每遍所有复制块哈希均一致 |
| 原生协议探针 | 13/13通过 |
| 24块/10会话诊断 | 10/10最终答案正确，31次任务生成 |
| 本轮新增生成 | 44；累计已知1771 |
| 实际计算子进程 | 10个均有preexec控制证据，read_bytes/major_fault/block_input均0 |
| 批次父进程 | read_bytes/major_fault/block_input均0 |
| 完整独立审计 | 报错停止，尚未通过 |
| 自然JCT单元 | 0，没有新增端到端收益结论 |

审计触发点是missing_median第一次compute携带多余column=2，被原冻结schema拒绝，尚未打开输入或启动子进程。因此该记录没有child_pid/preexec_controls。模型消费真实ValidationError反馈后，删除column参数，下一次compute成功并返回正确最终答案；该题用了4次生成，其余题3次。只读复核重现了相同ValidationError及完整截断错误文本，并确认真实错误反馈出现在后续模型提示中。这是已知开发题中的实际修订行为，不能替代独立母题agent循环验收。

旧审计器对所有compute记录无条件执行worker控制检查，遇到参数拒绝记录时KeyError退出。初次审计失败保持原样，没有跳过错误、重跑任务或直接改判通过。下一步需要新增严格的只读审计版本：重现并验证启动前参数拒绝，同时继续完整检查全部真实子进程、生成账本、token/物理KV、反馈消费、参考答案和资源证据，再审计本轮同一份数据。

原始增量归档1,369,222,030字节，1169增量条目，复用旧归档22273文件；SHA256：25de56d44ab79c29f42e53342c5a0332b4631aacc41dc895a2e86841312dc3ac。准备元数据另行校验归档，含模型检查点、准备日志及启动器。清理前完成逐文件核对。临时模型副本已删除，不宣称清理后又对该路径进行了实时核验。

- [只读错误定位记录](../results/cache_policy/pressure_staged_matrix_v2_2026-11-10/audit-failure-triage.json)
- [最终状态](../results/cache_policy/pressure_staged_matrix_v2_2026-11-10/FINAL.json)
- [停止及归档记录](../results/cache_policy/pressure_staged_matrix_v2_2026-11-10/state.json)
