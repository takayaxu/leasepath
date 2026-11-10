# 新资源版本：在 Python exec 前设置 worker 控制

本方案承接历史未定位的1次worker major fault。它不是根因确认，也不替换旧失败样本。新增静态C启动器，以非特权系统调用只控制自身进程；设置在exec前完成，Python早期验证继承，再进入原数值worker。

固定设置：原10组CPU及其SMT兄弟、NUMA1 MPOL_BIND、PR_SET_THP_DISABLE、12GiB RLIMIT_AS、240秒RLIMIT_CPU。没有全局NUMA/THP/page-cache变更，也不限制其他用户。静态启动器文件、Python标准库、数值库、系统依赖和输入由父进程锁页并验证。全部策略使用相同启动器、相同边界计数和审计开销。

实现：`native/leasepath_preexec_v1.c`、`preexec_worker_v1.py`、`preexec_tools_v1.py`、`preexec_pressure_v1.py`。原测量源码字节不变。启动器用静态链接避开动态启动依赖；它本身开始执行到设置控制之前仍有极短的启动窗口，累计wait4门覆盖该窗口，不能说完全消灭所有启动缺页。

预注册资格：一次10并发完整strace，然后3/6/10并发各一次无ptrace资格组，共最多29次真实工具调用，模型生成0。原样保留成功、失败、major fault和磁盘读取；无ptrace组任意worker lifetime或父进程major fault/read_bytes/block_input非零即停，不补样。独立审计同时核对真实syscall顺序，证明控制调用成功发生在Python exec之前；仅有环境变量/自报pass不能通过。

新的CPU资源资格通过后，下一门为24块10会话的完整原生controller trace：13次原生探针，live最多60次，合计最多73次。此trace在JCT外，不宣称策略加速。通过完整原生、物理KV、文件闭包与源码独立验收后，才运行新24/3自然锚点及预注册矩阵。

参数冻结：H30、tau10、同一GBDT、等成本1、128-token/32MiB block、FCFS、合法叶/retired-first规则不变。新资源方案的自然JCT不混入旧两核/工具或Python启动后控制的旧结果。

恢复与每次运行都要检查真实资源。GPU5/6/7当前空闲只是快照，加载时需重新检查所有权。新母题12题划分保持冻结，最终留出尚未进入模型。
