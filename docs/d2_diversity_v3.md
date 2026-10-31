# D2 母题扩展至 50：v3 采集说明

日期：2026-10-31。统一数据定义见 [数据集说明](dataset_card.md)。本轮目标是在已有 16 个母题上增加 34 个此前未运行的 MBPP 母题，实际执行后再按后端证据与任务质量分别统计。本轮不重新训练预测器。

**已完成：** 34/34 新执行后端可审计，31/34 通过独立测试及全部质量条件。累计主集为 50 个不同母题、65 条执行、472 个 episode、2,414 张快照；完整质量子集为 47 个母题、54 条执行、1,553 张快照。三条限额失败原样保留。实际时间、失败细节、因果重建和下一部分见 [本轮 Review](../reports/d2_diversity_v3_review.md)，跨轮逐文件哈希见 [50 母题清单](../results/prediction/dataset_inventory_50parents_2026-10-31.json)。

配置为 [d2_diversity_v3.json](../configs/prediction/d2_diversity_v3.json)，沿用固定 mini-swe-agent、Qwen2.5-Coder-7B-Instruct BF16 和 vLLM-Ascend。单次主执行采用 temperature=0、seed=23、2048 输出 token，最多 16 步、工具 timeout 60 秒、任务 timeout 240 秒。

## 选择、测试与执行协议

本轮仍使用固定 sanitized MBPP revision `f82046ba5aabbbb427dbfd38a254d26bff08b533`，原始 source SHA-256 为 `ca95deaa9a01ef0a6f439f88bcf0dd3db3563d22f22aad6cae04ebb9a8d8c8e9`。新增 ID 与旧十题没有交集，同题型或不同输入规模不自动制造新母题，分组为 `google-research/mbpp:mbpp-NNNN`。

每题固定一个公开 visible 例子和六个额外检查供 Agent 执行，其余公开例子与额外检查在会话结束后独立评测。共新增 1,325 个确定性检查，覆盖空输入、重复值、负数、边界值、稳定排序、字典冲突和组合计数等；通过独立计算期望值，而不是批量调用参考实现输出答案。参考实现必须先通过全部期望值检查，再用实际 pytest 验证测试模块；空函数负对照必须使 workload 测试失败。34 个参考 pytest 和 34 个空函数负对照均完成验证。

公开参考实现也可能有错：准备阶段的 MBPP 71 排序实现无法通过一个额外排序检查，在任何 Agent 结果出现前已排除，用 MBPP 106 替换。失败准备目录与 selection note 保留；不针对 Agent 失败结果替换题目。

Agent 必须实际读取任务、写 `solution.py`、单独执行 `python -m pytest -q test_visible.py test_workload.py`，当前源码成功后单独提交。编辑要求用非交互的完整文件 heredoc，不用 sed -i；编辑/测试合并动作会在执行前被拒绝并记录，不能算作真实测试失败。参考代码和独立检查不放进工作区。

每题只做一次选定主执行，失败与循环保留；没有人工修改候选或向 Agent 反馈独立答案。受保护文件通过哈希和命令复核，不宣称 OS 隔离。测试通过不等于证明所有语义正确。

## 新增母题

输入域的明确说明随冻结任务提供给 Agent。下面的“母题”按公开任务定义计数，不表示它们彼此语义毫不相关，更不表示 34 个独立来源项目。

| MBPP ID | 任务 |
|---|---|
| 19 | 检查整数列表中是否存在重复元素 |
| 57 | 数字列表组成最大整数 |
| 58 | 判断两个整数符号是否相反，明确零的语义 |
| 59 | 第 n 个八边形数 |
| 61 | 数位和等于长度的连续子串计数 |
| 62 | 非空列表最小值 |
| 63 | 元组对中的最大绝对差 |
| 64 | 按元组第二项稳定排序 |
| 65 | 嵌套列表递归求和 |
| 67 | Bell 数 / 集合划分数 |
| 68 | 列表是否单调 |
| 106 | 列表追加到元组后，返回元组 |
| 72 | 整数能否表示为两个平方之差 |
| 74 | 两组符号序列是否构成双射模式 |
| 75 | 筛选全部元素均能被 K 整除的元组 |
| 77 | 整数是否能被 11 整除 |
| 80 | 第 n 个四面体数 |
| 84 | Newman–Conway 递推序列 |
| 86 | 第 n 个中心六边形数 |
| 87 | 三字典合并，冲突时左侧优先 |
| 88 | 列表元素频率 |
| 90 | 最长字符串长度 |
| 91 | 字符串列表中的字面子串搜索 |
| 92 | 十进制数是否具有两位循环模式 |
| 93 | 非负整数指数幂 |
| 94 | 取第二值最小的元组的首值 |
| 95 | 嵌套列表中最短子列表长度 |
| 96 | 正整数因子数 |
| 97 | 展平嵌套列表后的元素频率 |
| 99 | 十进制整数转二进制字符串 |
| 102 | 下划线字符串转 PascalCase |
| 103 | Eulerian 数 / 排列下降次数计数 |
| 105 | 布尔列表 true 计数 |
| 108 | 三个可能未排序列表合并排序 |

主要增加任务定义的多样性，新增调用仍以短函数实现和 pytest 为主。原文件任务 v1 与修复 v2 的真实秒级、失败/重测覆盖完整保留；本轮不为增加时长插入 sleep 或重复无目的计算。

## 保存位置与复现

| 路径 | 内容 |
|---|---|
| `.artifacts/d2-diversity-v3/tasks.json` | 准备前冻结的 34 个任务、域、工作区、测试/参考哈希 |
| `.artifacts/d2-diversity-v3/source/` | 原公开来源固定快照 |
| `.artifacts/d2-diversity-v3/reference-validation/` | 参考实现 pytest 与空函数负对照日志 |
| `.artifacts/d2-diversity-v3/runs/greedy-guided/` | Agent 轨迹、候选源码、patch、工具摘要和 run manifest |
| `.artifacts/d2-diversity-v3/evaluation-runs/` | 独立候选评测日志与 JUnit |
| `.artifacts/d2-diversity-v3/frozen-round/` | 原始 journal、后端日志、代码和配置冻结快照 |
| `.artifacts/d2-diversity-v3/datasets/all-observed/` | 后端证据完整的主执行 |
| `.artifacts/d2-diversity-v3/datasets/qualified/` | 冻结测试及完整质量条件通过的子集 |
| `.artifacts/d2-diversity-v3/hazards/` | 对应风险区间、行政截断视图 |
| `results/prediction/d2_diversity_v3/` | 审阅摘要、质量清单、分组、来源及哈希 |

在已恢复原 MBPP source 与本地服务的机器上，用新目录运行：

```bash
.venv/bin/python scripts/prepare_d2_diversity.py --output .artifacts/d2-diversity-replay
.venv/bin/python scripts/check_d2_diversity_tasks.py --tasks .artifacts/d2-diversity-replay/tasks.json
.venv-agent/bin/python scripts/run_d2_diversity.py --tasks .artifacts/d2-diversity-replay/tasks.json
.venv/bin/python scripts/finalize_d2_diversity.py --root .artifacts/d2-diversity-replay --output results/prediction/d2_diversity_replay
.venv/bin/python scripts/verify_d2_diversity.py --root .artifacts/d2-diversity-replay --results results/prediction/d2_diversity_replay
```

`verify_d2_diversity.py` 用来核对本轮完整证据，需同时提供对应 `reference-validation.json` 及其验证日志。本机验证结果随本轮审阅清单保存。跨轮清点入口 `inventory_d2_datasets.py` 使用四轮既定主集和质量子集，验证内容哈希、sample/target 配对、跨轮重复、episode 权重和母题去重；只生成清单，不复制或合并样本。

最小九字段不变，本轮另导出 `features-expansion-v1.jsonl`。`work_units=6, work_unit=test_cases` 表示当前可见 workload 的六个测试，不能与文件任务的 rows/bytes 当成同一种规模直接比较。重测六字段仍仅在修复 v2 中存在；新轮未采到的字段不能默认为零。

全部仍是 `pilot_development_only`。后端不完整执行隔离；答案错误但轨迹完整的执行保留在主集。之后训练须按完整母题/执行分组，不能随机拆快照；公开来源和相似题型使母题交叉验证仍属于开发诊断。新增数据与旧模型分开，尚未重新训练或验证在线缓存收益。
