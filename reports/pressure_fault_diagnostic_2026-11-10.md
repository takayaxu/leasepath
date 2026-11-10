# 压力矩阵缺页诊断：有限复现与归因更正

本轮完成两组预先限定的10并发诊断，共20次真实工具调用；全部答案正确，父进程与worker生命周期 major fault/read_bytes/block input均为0。**历史故障未复现，尚未定位具体缺页文件，也不能认定已修复。** 原10并发资源预检仍保持失败，新增GPU generation、自然JCT与独立题模型执行均为0。

## 更正上一轮归因

上一轮 `rank_sum_test` 的worker全生命周期major fault为1，数值计算计时窗口内为0，Python import异常窗口为空，已跟踪的4个ldconfig子进程major fault也均为0。这只能支持“缺页发生在数值计时窗口之外”，**不能区分计时前启动与计时后收尾**。上一轮报告/说明中的“计时前”表述证据不足；本记录纠正解释，原始记录和报告字节不改写。

## 新增观测与本轮结果

在新版本诊断worker中保留原工具、请求参数、数值分派和答案检查，仅增加观测：

- resource导入后最早累计计数，明确其覆盖此前ELF/Python/site启动但不定位地址。
- 原worker各层导入和main、资源设置、numerical execute/snapshot、threadpool_info、结果序列化和外层解析边界。
- 源码/扩展模块导入前后计数、实际打开文件和最终映射的device/inode。
- 原wait4全生命周期计数及library-discovery子进程记录继续保留。

每个worker记录48–60个边界、215–1342个导入窗口。一次full strace组和一次无ptrace但有Python边界观测组，各10会话；固定CPU/NUMA/单数值线程不变。两组开跑前已登记总上限20次工具执行，没有补样或重复挑选。ptrace与Python profiling均产生额外开销，所有诊断耗时禁止计入JCT收益。

| 检查 | strace + 边界组 | 无ptrace边界组 |
|---|---:|---:|
| 实际工具执行 / 正确 | 10 / 10 | 10 / 10 |
| worker lifetime major fault总数 | 0 | 0 |
| worker read_bytes / block input | 0 / 0 | 0 / 0 |
| 父进程major fault / read_bytes / block input | 0 / 0 / 0 | 0 / 0 / 0 |
| 未覆盖的最终文件映射 | 0 | 0 |
| Python打开的未锁页磁盘文件 | 0 | 0 |

另逐个核对strace中10个worker及其已记录子进程的非空普通文件：每题338–2003条不同路径，全部在锁页清单内，包含启动时访问而最终不保留映射的文件。全父进程追踪新发现的非空未锁文件只有本次配置JSON，读取发生在父进程预备阶段；不是数值worker依赖。零长度的py.typed/REQUESTED/lock文件没有可锁页负载。未发现可以据此修复的worker依赖缺口。

## 权限与结论边界

当前内核5.15.0-198，perf_event_paranoid=4。对当前自有短进程申请disabled、self-only major-fault perf计数器被拒绝（EACCES=13）；没有修改系统权限、NUMA balancing、全局THP、page cache或其他用户任务。本轮没有内核故障地址记录。

锁页与mincore证明所检查文件页面驻留，不代表所有内核fault等待机制、匿名页、NUMA迁移及短暂映射都可以从历史记录反推。历史read_bytes=0不能抹去major fault=1。也不能在无新故障时把NUMA/页锁竞争或swap宣布为根因。

**已完成的是观测缺口修补和一次有界负结果；未完成的是历史故障的唯一文件归因及自然JCT准入。** 无故障的诊断组不替换旧失败，不被事后重贴为自然准入通过。

后续应使用此边界观测捕获非性能诊断中的再次故障；只有取得具体依赖/资源原因并作可审计修正，或另立明确的新资源方案并在结果前登记验收规则，才启动新的资源资格轮。不能循环重跑原门直到出现一组零缺页。当前保持24/32/64×3/6/10矩阵参数、等成本分布参数、旧50题排除和新题留出状态不变。

## 验证与保存

独立诊断审计、worker/子进程文件覆盖核对均通过，相关测试9 passed；冻结基线与本次执行源码hash一致。所有原始结果和失败解释保留。

增量归档位于仓库的 `.artifacts/checkpoints/pressure-startup-diagnostic-2026-11-10/`。其中 `restore-manifest.json` 描述每个文件来自旧pressure归档还是本轮delta归档；原始旧归档保持不变。delta为29,456,789字节，SHA256 `1c46060cf26bdae2d11ef647e43edf58e954bedd95f71f662fab4dcc1aa4ada2`，231个delta条目逐个验证，复用22,273个与旧归档相同的普通文件。归档验证后只清理本轮自有tmpfs。
