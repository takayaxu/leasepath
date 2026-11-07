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

后续聚合只接受原12题和新增38题两个完整成功且审计通过的冻结批次。保留各自 run_id、来源任务、实际前缀/KV证据和代码版本，协议/受控/失败记录不混入主集，不能拼接不同版本最佳答案。特征与未来目标文件分离。最终统计、质量和独立审计结果在封口后补充。

## 当前运行入口

驱动 .artifacts/extension50/driver.py，批次 .artifacts/qwen-data-extension50-a；完整 argv/environment、stdout/stderr、逐阶段退出码、10秒资源采样保存在批次目录。只读复核入口仍为 check_qwen_data_dataset.py。最终合并入口 aggregate_qwen_data_fifty.py 再次独立审计两批、复核 artifact/reference 和冻结哈希后导出。

固定持久权重位于 /home/liuxv/local-models/qwen25-coder-32b-leasepath/persistent-f16；本次只创建已完整SHA256核对的临时运行副本，结束移除。临时文件不替代持久权重备份。源/输入冻结 checkpoint 为 .artifacts/checkpoints/extension50-freeze；最终还需补 bundle、raw/输入归档及SHA256。尚未推送。

## 新工具协议门槛通过

两题 missing_median / feature_correlation 完整成功、独立质量与轨迹复核通过；7 次实际生成。缺失值题首次携带额外 column 参数，oneOf 分支严格拒绝，错误结果进入下一次真实请求，模型纠正后成功；没有放宽边界或忽略失败。两题主结果4+3次生成，U=5/D=2、未消费工具结果0。完整38题入口在此门槛通过后启动；主结果另计，不把这两题门槛当作新增母题再加一次。

## 首批完整采集与版本2修正

批次 a 完整采集38题：37/38成功，初次近邻检索6次生成耗尽。真实compute已正确，但模型反复调用view_artifact；非表格结果返回完整result同时带空page/row_count=0，造成误导。最后一个工具结果在显式丢弃前未被下一请求消费，单列保留，未伪造U。独立审计通过且质量37/38，全部原始流、消息、产物和成功子集保留，不聚合成50成功。

修正通用非表格产物分页：完整对象为1项，明确complete_result_returned和分页单位，nested arrays始终保留完整。系统提示说明compute已返回完整结果，仅显式截断时才需view。表格分页保持真实行语义。203 passed/1 skipped/1 deselected；38题compute/view重新与参考解一致。三次原参考计时按相同输入/参数和未变的compute/reference源码SHA继承，不假造新测三次；任务和参考文件hash与v1完全相同，schema/响应版本变化另记v2。

为避免跨版本最佳答案拼接，使用v2完整重跑38题。批次a耗用125次（probe7、主118）封存，其ledger.sealed.json禁止再次预约；剩余127次总额度独立分配probe7、主120，不增加原新增252/累计338上限。v2读取旧账本SHA和125条记录作前置校验。新目录qwen-data-tasks-extension38-v2保留同一任务/参考字节，输入仍指向原不可变v1文件；冻结schema新hash。批次b协议选近邻与相关矩阵，先重新验收再主采集。
