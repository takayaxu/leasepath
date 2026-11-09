# 完整 Python 驻留与新自然 JCT 尝试

承接 `84f7aef`，遵照用户要求另开版本与账本，不追加上一轮失败账本。仍仅当前共享服务器；亲和性不是独占 CPU，其他任务及全局 THP／页缓存设置不改。

## 前瞻冻结方案

固定 Qwen2.5-Coder-32B-Instruct F16、GPU 5/6/7、F16 KV、24×128tokens 逻辑容量、16384 物理上下文、2048 headroom。仍用 logistic_holdout、ridge_validation、temporal_asof 三道已查看开发母题，输入／数值参数／SYSTEM／冻结预测器不改，预测目标仍为 F_U(3s)。三种策略六个全排列，共18批54任务；不填充工具响应、不按 JCT 或主机压力筛样本，不补跑替换。预期175次生成=13原生门控+162闭环，独立新账本上限241（probe13、pilot228），预算不当作实际完成量。

Python 3.13.9 解释器与完整标准库（不含字节码缓存）、原冻结 site-packages、当前 LeasePath 来源复制至自有 tmpfs，固定 PYTHONHOME／PYTHONPATH，逐文件 SHA256。系统依赖来自全部复制 ELF 的 ldd 传递闭包及真实三工具 strace；系统文件原地只读共享映射并 mlock，校验哈希与文件身份。完整预测运行时、模型加载后实际父进程动态库也驻留。所有策略使用同版本诊断，诊断开销包含在 JCT；strace 仅准备阶段开启。

权重专用非 GPU keeper 进程负责 PROT_READ／MAP_SHARED／mlock，父进程锁其他依赖。两进程各自遵守继承的 RLIMIT_MEMLOCK，不提高限额，不产生模型私有 COW 副本。每批前检查 keeper 存活、文件身份和 mincore；文件页驻留不意味着 CPU 页表已建立，更不等于带宽独占。

延续严格零磁盘读取／零主缺页停止规则，并扩展到完整子进程：wait4 统计解释器启动到退出的 major_faults、block_input_ops；worker 保留自 exec 以来的 read_bytes，以及原分阶段计数。逐调用记录实际解释器、sys.path、文件 open、加载映射及发生 I/O 的嵌套导入窗口。首个答案、协议、资源、来源、预算违例即停止，所有失败保留。

## 文件定位及真实工具验证

三种真实工具 strace 全部严格正确；读取与映射路径显示原先驻留清单之外还存在 `/etc/ssl/openssl.cnf`、locale 文件及缓存、时区、动态加载器缓存等。全部纳入本次清单。文件访问不能等同为发生过磁盘缺页。

后续三轮锁页真实工具共9次，全部正确、完整进程 major_faults=0、block_input_ops=0、自 exec read_bytes=0。准备期这些调用与权重复制有时间重叠，**不用于性能比较或宣称共享主机稳定**。正式 JCT 在全部准备、测试、复制完成后启动。

未重现上一轮那次主缺页。针对本账号进程实际尝试 perf_event_open 软件 major-fault 地址采样，返回 EACCES，perf_event_paranoid=4；strace 不提供内存映射故障地址。因此上一轮具体缺页文件不可追溯，本次只报告实际路径、导入窗口和当前完整进程计数，不伪造精确归因，也不请求管理员改系统设置。

初次 ldd 发现重定位后的可选标准库扩展需要原 Conda 库，已用显式符号链接保持原库字节与解析关系，实际系统目标纳入哈希／锁页；未安装或升级数值依赖。初次追踪解析器误把 read 返回的二进制内容当成路径，准备阶段报错；修正解析规则后直接重用已保存追踪，未重跑或替换这3次调用。原始错误日志一并保留，均发生在模型生成与新账本创建之前。

功能检查目前244 passed，1 deselected（外部 full_pinned_audit 数据缺失）。新自然 JCT 执行、独立审核和关闭结果将在本文件补记。
