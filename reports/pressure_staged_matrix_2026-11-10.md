# 固定压力矩阵续接状态

更新时间：2026-11-10T06:15:21.723198+00:00。当前状态：**stopped**。

模型准备：verifying_prefix；已确认 65,535,970,144 / 65,535,970,144 字节。累计准备时间 4417.3 秒，独立于自然 JCT。

已汇总新增生成预约：0（运行中步骤可能尚未更新，失败预约也计入预算）。九格自然 JCT 全部通过：False。暂不宣称新增端到端收益；完整单元需再做配对统计。

等成本分布 H30/tau10、GBDT、LRU/3秒点预测对照、128-token 块、FCFS、输入及工具控制均沿用冻结方案。次序为容量24/32/64 × 并发3/6/10，先完成每个容量的10并发原生诊断，再进入自然计时；任一执行或独立审计失败即停。共享服务器不具有独占 CPU/内存带宽。本轮是已知开发题压力测试，独立母题与真实 agent 循环尚未进入模型验收。

| 步骤 | 容量 | 并发 | 状态 |
|---|---:|---:|---|

详细状态：[state.json](/home/liuxv/LeasePath-new-server-data/results/cache_policy/pressure_staged_matrix_v1_2026-11-10/state.json)；运行计划：[plan.json](/home/liuxv/LeasePath-new-server-data/results/cache_policy/pressure_staged_matrix_v1_2026-11-10/plan.json)。

停止原因：`TimeoutExpired: Command '['/opt/anaconda3/bin/python', '/home/liuxv/LeasePath-new-server-data/scripts/stage_pressure_model_v1.py', '--plan', '/home/liuxv/LeasePath-new-server-data/results/cache_policy/pressure_model_staging_v1_2026-11-10/plan.json', '--checkpoint', '/dev/shm/leasepath-model-stage-v1-20261110/checkpoint.json']' timed out after 3660 seconds`。
