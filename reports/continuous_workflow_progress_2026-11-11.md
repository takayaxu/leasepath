# 持续多工具 Agent 工作流实时进度

北京时间：2026-11-11T18:27:30+08:00。当前流水线：**completed_development_cycle**。

六个持续开发项目各八轮用户需求；真实文件、Python、SQL、本地 HTTP、数值实验和检查工具。六个独立留出族未使用。

## 逐版保留结果

| 版本 | 状态 | 实际轮次 | 通过轮次 | 预约生成（含gate） |
|---|---|---:|---:|---:|
| v1 | completed_collection | 6/48 | 0 | 50 |
| v2 | completed_collection | 35/48 | 30 | 173 |
| v3 | completed_collection | 40/48 | 36 | 216 |
| v4 | registered_not_started | 0/48 | 0 | 0 |

<!-- v4 was registered but cancelled before model execution after more complete repair became necessary. -->
| v5 | stopped | 0/48 | 0 | 0 |
| v6 | completed_collection | 45/48 | 44 | 242 |
| v7 | completed_collection | 48/48 | 48 | 257 |

各版本使用同一批项目，不能相加当作新增独立实例。失败后停止该项目，因此未执行轮次也属于48轮计划覆盖分母。v4登记后在模型执行前撤下，新增模型调用0。

## 根因与处理

- v1：产物正确，但控制器要求最终回答必须裸JSON。v2已改为自然交付后独立验收，原错误保留。
- v2：复制日志为代码字面量导致输出截断，实际文件读取指导缺失；v3已验证日志第二轮通过。容量从128增到192块，保留全历史。
- v3：日志目标文件未写明、库存配送参数类型未写明；下一版只补充规格，不改变正确答案。
- v3：账单查得正确数据却保存默认零值、API写出非法JSON。下一版工具在写入前验证JSON，公开检查验证目标文件、排序、代码行为及金额一致性；返回真实错误供Agent修订，独立最终验收器不变。

v5启动因测试改写私有运行时字节码被拦截，模型生成0次；已从原始副本恢复全部2121个字节码，22274个清单文件哈希全部通过。v6补齐工具提交事件，严格重放状态及计时。

v6已完整审计：44轮通过、1轮失败、3轮未执行；五项目8/8，数值实验第5轮OLS分母用加号替代减号。保留失败；v7增加基于实际数据的正规方程一致性检查及真实错误反馈，独立验收器不变。

v7采集48/48轮及完整审计通过，目标经验分布/GBDT已训练；初次E2E在初始化事件kind参数重名处停止，模型生成0次。v6 E2E继续版仅修复事件字段，复用已审计数据/初始模型。

## 当前版分项目结果

| 项目 | 已执行 | 通过 | 原因 |
|---|---:|---:|---|
| continuous_inventory | 8/8 | 8 |  |
| continuous_billing | 8/8 | 8 |  |
| continuous_logs | 8/8 | 8 |  |
| continuous_api | 8/8 | 8 |  |
| continuous_experiment | 8/8 | 8 |  |
| continuous_documents | 8/8 | 8 |  |

## 训练及端到端

目标场景预测器已训练：6项目、234个观测episode、1079个决策快照。时间前后验证与全量初始模型分别保存。

- lru：`completed_e2e_arm`，48/48已观察轮次通过。
- empirical_distribution：`completed_e2e_arm`，48/48已观察轮次通过。
- gbdt_static_distribution：`completed_e2e_arm`，48/48已观察轮次通过。
- gbdt_dynamic_point：`completed_e2e_arm`，48/48已观察轮次通过。
- gbdt_dynamic_distribution：`completed_e2e_arm`，48/48已观察轮次通过。

等成本评分H30/tau10、成本1、支持门槛及FCFS保持冻结。动态训练只用截止时已成熟的标签，开销计入服务观察。此次登记范围是六项目修复及一轮开发对照，60项目扩展和多轮平衡重复仍属后续工作，不能提前宣称端到端收益。

[活动流水线状态](/home/liuxv/LeasePath-new-server-data/results/agent/continuous_workflow_v6_2026-11-11/supervisor-state.json) · [活动计划](/home/liuxv/LeasePath-new-server-data/results/agent/continuous_workflow_v6_2026-11-11/plan.json) · [当前采集日志](/home/liuxv/LeasePath-new-server-data/results/agent/continuous_pilot_v7_2026-11-11/run.log)

## 已完成的五臂结果

五臂均48/48成功、全部审计通过；逐owner请求完全一致，6次在线更新的截止点数据哈希复演通过。

动态GBDT分布相对LRU多复用329344 token，prefill时间减少44.86%，平均轮次响应减少19.76%。本轮未证明优于静态GBDT或点预测；独立60项目扩展和多次平衡重复尚未完成。

[完整结果与限制](/home/liuxv/LeasePath-new-server-data/reports/continuous_e2e_development_2026-11-11.md)
