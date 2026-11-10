# 针对已观察加载缺页的新资源版本v2

v1的首次独立资源轮已失败并保留：3/6并发通过，10并发13次生命周期major fault，其中5次为worker自身扩展加载窗口、8次由ldconfig子进程报告。所有答案正确、read_bytes和block input均0，不能因此抹去major fault。观测窗口对应NumPy `_multiarray_umath` 与标准库 `_decimal`；尚无唯一内核fault地址，不追溯修改历史单次缺页的归因。

基于这项新证据，v2在结果产生前另冻结两个具体改变：

1. `ctypes.util.find_library`只解析预先发现并SHA256核验的`c`和`numa`绝对路径。未知库查询直接报错；不继续运行ldconfig，不改数值库内容。真实解析调用记录在工具证据中；存在任何未登记的后代进程则失败。
2. 在worker验证启动前控制之后、导入原数值worker之前，调用`mlockall(MCL_CURRENT|MCL_FUTURE)`。父进程仍锁定runtime/输入/系统文件；worker额外锁当前及后续自己的映射，包含匿名数值内存。调用失败即停止，不调高memlock限额，不清零缺页计数。该操作及库身份校验的实际耗时均属于工具耗时。

v1的CPU/NUMA/THP/地址空间与CPU时限设置原样保留。新增锁页只针对自有进程；每工具12GiB地址空间上限、10工具120GiB聚合上限与启动前220GiB可用内存门保持不变。实际消耗记录VmLck，不把120GiB说成实测RSS。

同样只运行一次10并发文件/syscall追踪，再运行3/6/10各一次无ptrace资格组，总工具调用上限29，不替换v1的29次样本。门槛仍为每组父进程及worker全生命周期major fault/read_bytes/block input全0。独立审计检查实际成功的mlockall syscall、exec前设置、Python继承值、无ldconfig子进程及单数值线程。

这一方案是对加载路径和映射驻留的干预，不是已证明某个内核机制就是根因。新方案耗时不与v1或旧三题结果混平均，后续所有缓存策略承担同样的真实工具开销。缓存分布参数、GBDT、FCFS和压力矩阵预注册不变。

CPU资格通过后仍须通过已预注册的24块/10会话完整原生trace（13 probe + 至多60 live）；所有trace耗时排除自然JCT收益。未通过这道门不进入自然矩阵。
