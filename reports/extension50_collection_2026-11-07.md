# 扩展到 50 个开发母题

用户在完成 12 题试点后授权“继续增加母题到50”。本阶段补充 38 个原始分析意图，保留旧 12 题完整运行和冻结输入；不恢复旧 Codex/Flask 流程、不训练预测器、不运行在线策略/JCT、不购买资源。

## 固定范围与预算

代码基线仍为 66a34c8eb618f4849153cb3c8d72880362e8e387，初始已验收 12 题交付提交 5336d2f，新增冻结提交 682dcf8。新协议与采集使用同一个官方 Qwen-Agent 0.0.34 FUNCTION/ARGS 接口、相同 32B revision/F16 后端和 GPU 5/6/7；0–4 的负载保持。复用已完成的 8 请求后端验收，新增工具须重新通过两题真实多轮门槛。

旧 .artifacts/qwen-data-batch-v1/ledger.jsonl 保留 86 次（probe50/pilot36），不修改其上限。用户新的扩量指令授权本阶段独立账本 .artifacts/extension50/ledger.jsonl，probe≤24、主采集≤228（38×6）、合计≤252；连同旧账最多338次，低于原432次总计划。没有货币费用估算或扩容。工具仍2线程/12GiB、单调用240秒、每题900秒/最多6次生成。账本锁定上限，重启不能提高同一 ledger 的额度。

## 新母题与预检

38 个意图包含缺失值填补、分位数、分组加权均值、相关矩阵、复合键去重、直方图、列联独立检验、bootstrap、随机化检验、配对检验、ANOVA、秩和检验、线性趋势、IQR异常、as-of时序连接、滑动窗口、时间重采样、会话划分、插值、频谱、互相关、滤波、峰检测、积分、求根、约束优化、稀疏线性求解、NMF、ICA、密度聚类、近邻检索、异常检测、特征互信息、自回归预测、生存估计、图像连通域、最优指派、分布传输距离。

每题有实质操作/参数选择，工具按通用固定函数分派，没有 solve_task 或模型控制代码/SQL/路径。源版本和许可保存于 .artifacts/extension50/sources。题面及合成输入为本项目原创，使用 NumPy2.5.4/SciPy1.18.1/sklearn1.7.2，不宣称外部 benchmark 成绩。

.reference 不调用受限 dispatch；可行处用不同公式/算法/库（如指派用线性规划复核、稀疏解用带状求解复核、相关系数用中心化矩阵公式）。复杂 sklearn 估计器和峰检测仍共享同一第三方实现，明确保留此限制，不声称两份独立算法证明。

38/38 均完成三次参考计时、实质错误输出拒绝和真实受限工具数值对照。201 passed/1 skipped/1 deselected；full_pinned_audit 外部快照仍未恢复。预检主要调用实测短35/中3/长0；默认预计短32/中6，预定档仅假设，不现场增大输入凑长尾。

冻结目录 .artifacts/qwen-data-tasks-extension38-v1；task SHA256 197b7293dbb09321fe07f1dde4a98991191f0bfb2c8e0104bf82a6fa5c643578。参考解、tool schemas、preflight 和 collection-contract 独立保存哈希，探针前冻结。新格式允许操作间同名参数有不同范围，oneOf 分支仍严格拒绝额外字段/类型/越界；含专门回归。原输入FD保护、哈希/链接/并发检测与子进程清理继续有效。

## 统计口径

50 个 source_group 的单位是不同分析意图，不是50个独立数据来源。它们共享库家族与部分计算结构；当前全部为开发集，无验证/测试留出泛化结论。本次用户指定50题，没有为此冒充原60题的42/9/9切分，也不宣称≥10个成功长工具目标已经完成。

后续聚合只接受原12题和新增38题两个完整成功且审计通过的冻结批次。保留各自 run_id、来源任务、实际前缀/KV证据和代码版本，协议/受控/失败记录不混入主集，不能拼接不同版本最佳答案。特征与未来目标文件分离。最终统计、质量和独立审计结果如下；本报告保留失败版本和恢复过程。

## 当前运行入口

驱动 .artifacts/extension50/driver.py，批次 .artifacts/qwen-data-extension50-a；完整 argv/environment、stdout/stderr、逐阶段退出码、10秒资源采样保存在批次目录。只读复核入口仍为 check_qwen_data_dataset.py。最终合并入口 aggregate_qwen_data_fifty.py 再次独立审计两批、复核 artifact/reference 和冻结哈希后导出。

固定持久权重位于 /home/liuxv/local-models/qwen25-coder-32b-leasepath/persistent-f16；本次只创建已完整SHA256核对的临时运行副本，结束移除。临时文件不替代持久权重备份。源/输入冻结 checkpoint 为 .artifacts/checkpoints/extension50-freeze；最终还需补 bundle、raw/输入归档及SHA256。尚未推送。

## 新工具协议门槛通过

两题 missing_median / feature_correlation 完整成功、独立质量与轨迹复核通过；7 次实际生成。缺失值题首次携带额外 column 参数，oneOf 分支严格拒绝，错误结果进入下一次真实请求，模型纠正后成功；没有放宽边界或忽略失败。两题主结果4+3次生成，U=5/D=2、未消费工具结果0。完整38题入口在此门槛通过后启动；主结果另计，不把这两题门槛当作新增母题再加一次。

## 首批完整采集与版本2修正

批次 a 完整采集38题：37/38成功，初次近邻检索6次生成耗尽。真实compute已正确，但模型反复调用view_artifact；非表格结果返回完整result同时带空page/row_count=0，造成误导。最后一个工具结果在显式丢弃前未被下一请求消费，单列保留，未伪造U。独立审计通过且质量37/38，全部原始流、消息、产物和成功子集保留，不聚合成50成功。

修正通用非表格产物分页：完整对象为1项，明确complete_result_returned和分页单位，nested arrays始终保留完整。系统提示说明compute已返回完整结果，仅显式截断时才需view。表格分页保持真实行语义。203 passed/1 skipped/1 deselected；38题compute/view重新与参考解一致。三次原参考计时按相同输入/参数和未变的compute/reference源码SHA继承，不假造新测三次；任务和参考文件hash与v1完全相同，schema/响应版本变化另记v2。

为避免跨版本最佳答案拼接，使用v2完整重跑38题。批次a耗用125次（probe7、主118）封存，其ledger.sealed.json禁止再次预约；剩余127次总额度独立分配probe7、主120，不增加原新增252/累计338上限。v2读取旧账本SHA和125条记录作前置校验。新目录qwen-data-tasks-extension38-v2保留同一任务/参考字节，输入仍指向原不可变v1文件；冻结schema新hash。批次b协议选近邻与相关矩阵，先重新验收再主采集。

## 版本2门槛与加载前中断恢复

版本2近邻/相关矩阵两题各3次生成完整成功、独立审计U=4/D=2且无未消费结果。随后driver与子进程在主采集模型校验阶段消失，原工具session不可用，退出原因未知；cgroup没有OOM记录。batch-b主目录仅有未封口的observer start记录，没有identity.json；账本pilot=0、probe=6。没有把这段当数据或补造exit code。中断目录与driver-interruption.json保留。

使用独立session的驱动在batch-c新目录恢复，继续同一v2冻结合同、ledger、输入/参考和已通过的batch-b两题门槛，不重复协议探针、不覆盖未封口目录。全部真实新增预算继续锁定：第一批125、v2协议6，主采集可用120。

## 最终结果：50 个成功开发母题

最终采用原12题完整成功批次与版本2完整38题重跑，不从37/38的首批中挑题补齐。版本2 **38/38完整成功**，独立质量复核、封口轨迹审计和导出重建通过；输入/参考hash与首批相同，schema/产物查看语义的版本差异保留。first-attempt口径为原12+首批37=**49/50（98%）**；经过一次整个38题重跑后，最终采用两完整批次为**50/50（100%）**。这不是把首次成功率改写为100%。

| 指标 | 原12题 | 最终38题 | 合计 |
|---|---:|---:|---:|
| 完整成功母题 | 12 | 38 | 50 |
| 采用批次实际生成/lookup | 36 | 114 | 150 |
| 工具结果被后续真实输入消费 | 24 | 76 | 100 |
| idle episode | 36 | 114 | 150 |
| 因果快照/目标 | 106 | 241 | 347 |
| U | 24 | 76 | 100 |
| D | 12 | 38 | 50 |
| C | 0 | 0 | 0 |

版本2missing_median用4次生成（参数拒绝后纠错）、noise_filtering用2次（直接compute并消费后回答），其余36题各3次。没有外部单题最佳重试拼接。成功批次未消费工具结果0、unmapped0、unexplained0、轨迹拒绝0。首批失败near-neighbor最终一次view结果未消费后显式discard，单列保留而未补造消费。

50题主要成功compute实际短/中/长为 **35/11/4**；≥60秒成功母题仍为**2个**，均来自初始随机森林/Extra Trees。新增题以广度为目标，未用人为重复或等待补长尾，未声称原计划≥10长题已完成。首批全部失败/成功轨迹、协议和控制探针均另存，不进入这150个主episode。

所有50题都是development；验证0、测试0，无独立来源/时间留出结论。分析意图按source_group归组，仍共享TPC-H/NumPy/SciPy/sklearn来源。初始12和新增38分别保留原run_id、事件、输入与参数能力版本、source hashes，不声称两批完全相同工具schema。模型、权重、tokenizer、F16 KV、native库和上下文配置在聚合中逐项校验相同。

最终数据位于 **.artifacts/qwen-data-extension50-c/fifty-parent-dataset/**：dataset-card.json、catalog.jsonl、events.jsonl、episodes.jsonl、features.jsonl、targets.jsonl、compute-calls.jsonl、provenance.json、SHA256SUMS。features只含当时可见白名单字段，未来结局和剩余时间单独存targets；347行逐键对齐且remaining_ns>0。聚合重新从两批封口raw重建events/episodes/snapshots/coverage，逐字节匹配后才导出；不只相信已有导出统计。原始新主批次在extension/，旧主批次仍在qwen-data-32b-batch-d/pilot。

## 实际用量、计时与资源

本次扩量实际 **245次**：首批125（probe7/main118）、版本2共120（probe6/main114）。连同初始86为 **331次**（probe63、所有main尝试268）；最终采用主批150次。三个账本331个reservation ID唯一，两个新账本已封口，不继续追加。低于本次独立有界计划338，没有拿原432余量静默提高额度。主机已有资源，没有外部推理购买/货币费用估算。

版本2主进程含完整模型核验/加载和采集共1229.120秒，恢复driver含独立检查/导出/聚合共1290.023秒；未知原因的加载前中断另保留，不虚构exit code。版本2已记录的整批计算/工具调用时长合计81.663秒，worker/child/tool/source-check/observer等是嵌套，不可横向相加。工具返回到lookup中位数0.215秒、最大3.179秒，包含源保护/前处理/观测，不是cache收益；observer序列化写入合计3.137秒，seal/fsync2.566秒。workspace_hash仍not_applicable。

GPU5/6/7峰值约22346/22324/21850MiB，自有engine结束后均约4MiB；0–4原任务保持。临时65.54GB运行模型副本已清理，持久GGUF、14原权重和原identity/SHA保留。受共享机械盘与CPU影响，本次没有独占性能或在线收益声明。

## 交付与只读复核

小摘要在results/migration/extension50_2026-11-07/。新旧失败/成功原始证据和冻结v1/v2任务、参考、schema、合同、账本、全部argv/log/exit、license另外归档SHA256；Git bundle不替代原始数据。最终checkpoint在.artifacts/checkpoints/extension50-final/，恢复使用restore_qwen_data_checkpoint.py和metadata.json保留shallow边界。备份仅在同一服务器，未推送、没有异机备份。

只读重验最终新增批次不产生模型请求：

```bash
PYTHONPATH=src .artifacts/qwen-data-env/bin/python scripts/check_qwen_data_dataset.py \
  --run .artifacts/qwen-data-extension50-c/extension \
  --tasks .artifacts/qwen-data-tasks-extension38-v2 --output /tmp/extension38-recheck.json
```

进一步只读重建50题：用aggregate_qwen_data_fifty.py，initial指向qwen-data-32b-batch-d/pilot，extension指向qwen-data-extension50-c/extension，tasks分别为qwen-data-tasks-v5和qwen-data-tasks-extension38-v2，output必须为新空目录。没有自动启动训练、预测器调参、在线策略或JCT实验。
