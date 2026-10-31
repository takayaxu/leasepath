# 离线训练第二部分：共享 GBDT 与按母题分组的开发评测

日期：2026-10-31（Asia/Shanghai）。**GBDT 已完成训练、保存/加载和 5 折开发评测，等待本部分 review。当前条件经验风险基线优于本轮固定配置的 GBDT。** 本部分交付离线模型和同输入对照；在线缓存接入、扩大采集和后续调参另行推进。

## Review 入口

| 文件 | 内容 |
|---|---|
| [GBDT 配置](../configs/prediction/gbdt_review_2.json) | 单一预先固定配置、5 折规则与主指标 |
| [GBDT 模型实现](../src/leasepath/gbdt_predictor.py) | 共享多分类树、特征编码、回退、JSON 保存/加载 |
| [分组与评分实现](../src/leasepath/prediction_training.py) | 来源校验、workflow 权重、按母题切分、删失与概率评分 |
| [训练入口](../scripts/train_reuse_predictor.py) | 折内重拟合基线/GBDT、折外预测、全开发集模型与来源快照 |
| [结果摘要](../results/prediction/gbdt_review_2/summary.json) | 固定主指标、模型大小、概率检查和 CPU API 耗时 |
| [完整分状态指标](../results/prediction/gbdt_review_2/metrics.json) | 各时间窗 Brier、风险区间 NLL、U 校准分桶与覆盖数 |
| [折划分](../results/prediction/gbdt_review_2/split-manifest.json) | 10 个母题及其 workflows 的固定分组 |
| [折外概率示例](../results/prediction/gbdt_review_2/prediction-examples.json) | idle 起点、工具等待、已排队等真实输入的预测 |
| [独立复核](../results/prediction/gbdt_review_2/verification.json) | 来源哈希、训练内词表、折外预测重放和主指标复算 |

## 固定模型与输入

使用 LightGBM 4.6.0 的一个共享 multiclass GBDT，标签顺序固定为 `U/D/waiting`。输入为当前快照的 `tool_name`、`phase`、`elapsed_idle_s` 桶，再加待预测的 `interval_index`。elapsed 桶完全沿用第一部分边界 `0.03,0.1,0.3,1` 秒，没有给 GBDT 增加连续 elapsed、未来阶段、命令正文、任务 ID 或最终成功信息。

因此，条件经验基线和 GBDT 获得相同状态信息。工具和 phase 的类别词表只从每折训练样本建立；缺失值保持独立类别。未见工具、phase 或 elapsed 桶在相同兼容后端内回退该训练折的全局经验基线；未见兼容后端不输出估计。兼容身份只用于检查，不作为可分裂特征。

固定训练参数：

| 项目 | 值 |
|---|---:|
| boosting 轮数 | 40 |
| 每棵树最多叶子 | 4 |
| 最大深度 | 2 |
| learning rate | 0.05 |
| min_data_in_leaf | 20 |
| min_sum_hessian_in_leaf | 0.1 |
| L1 / L2 | 0.1 / 10 |
| min_gain_to_split | 0.01 |
| CPU threads / seed | 1 / 23 |

多分类每轮产生三棵树，因此全开发集模型各为 **40 轮、120 棵树，实际最大 4 个叶子**；不是为每个时间区间分别训练模型。未做参数搜索、早停选参或评测后校准，也未要求 GBDT 胜过基线。

`predict(features, horizons)` 与经验模型使用相同输出字段：条件概率 `hU/hD/h0`、累计 `F_U/F_D/survival`，并检查归一化、单调性和 hazard 累积递推。当前只有区间右端点 `0.01,0.03,0.1,0.3,1` 秒存在训练风险记录；更长区间仍输出不可估计。GBDT 在 1 秒末可能保留正 survival，不能将其强制归零，也不能据此推断更长时间窗的分布。

## 数据与切分

沿用第一部分冻结数据，核验其原始文件哈希与质量清单：

| 数据范围 | workflows | 母题 | episodes | 快照 | 训练风险行 |
|---|---:|---:|---:|---:|---:|
| all-main，主开发范围 | 19 | 10 | 185 | 729 | 1,899 |
| qualified，成功筛选对照 | 14 | 10 | 76 | 285 | 717 |

19 条范围包含任务失败但后端证据完整的执行；HTTP 400 的不完整 workflow 仍隔离。两组不是独立数据集，qualified 是完整范围的子集。

同一母题的两种生成配置、全部 episodes、快照与风险行始终同折。按 `SHA256(seed:parent_group)` 排序后轮流分配 5 折，每折 8 个母题用于训练、2 个母题用于验证；划分只依赖母题身份，在模型拟合前保存。两种数据范围共用母题划分。每折基线、词表、风险集支持和 GBDT 均重新拟合，只对该折未见母题生成概率。

原始快照按 episode 平衡，再除以所属 workflow 的 episode 数，使每条 workflow 的快照总重为 1。展开风险区间时沿用快照权重，不按行数二次归一化；较长观测会为生存似然提供更多已知区间。指标先在 workflow 内按该权重平均，再平均同母题的 workflows，最后等权平均母题，另存 workflow 平均结果。这避免将多个配置当成多个独立题目。

## 预先固定的主结果

主指标为 **0.1 秒累计三状态 Brier，按母题等权平均，越低越好**：

`Brier3 = (F_U - y_U)^2 + (F_D - y_D)^2 + (S - y_waiting)^2`

取值范围为 0～2；这里使用累计概率而非单区间 hazard，时间均相对当前快照。全部是按母题留出的开发折外预测，没有使用训练内预览评分。

| 数据范围 | 全局经验基线 | 条件经验基线 | 共享 GBDT |
|---|---:|---:|---:|
| all-main | 0.461138 | **0.396167** | 0.428382 |
| qualified | 0.493263 | **0.427985** | 0.461630 |

GBDT 在该指标上优于全局基线，但落后于条件经验基线。不能据此声称树模型带来增量价值。没有根据这些结果回头调整正则或改选主指标。

## 不同决策状态

以下为 all-main 的同一 0.1 秒累计 Brier；各状态内部按覆盖该状态的母题等权平均，不能将表内分数直接按快照数拼回总体：

| 状态 | 快照数 | 全局经验 | 条件经验 | GBDT |
|---|---:|---:|---:|---:|
| idle 起点 | 185 | 0.519027 | **0.514991** | 0.519593 |
| 工具等待 | 207 | 0.733519 | **0.613987** | 0.644180 |
| 已排队 | 166 | 0.130693 | **0.030608** | 0.066607 |
| 其他已观测状态 | 171 | 0.407788 | **0.364550** | 0.431506 |

已排队预测明显容易，idle 起点仍困难。phase 是当时真实可见的信号，但总体指标不能替代实际缓存决策时点的结果。条件基线在本轮各状态均更好；这项十题开发结果不足以判定模型类别的一般优劣。

作为另一个诊断，all-main 的按母题平均条件风险区间 NLL 为：全局 0.718389、条件经验 0.443566、GBDT 0.732396。它是按完整观测风险行计算的条件 log loss，不是完整生存路径似然；概率下限为 `1e-15`，本次没有触发截断。U 的固定概率分桶校准表已保存，没有额外拟合校准器。

## 删失与支持边界

U、D 是共用风险集的竞争事件。真实 C 不作为第四类；其部分区间由 `loss_mask=false` 排除，后续区间不参与训练或风险行评分。累计窗口评分只有结局可观测时才计算；删失后未知窗口被计数并跳过，不能当负例。

本次真实数据自然 C=0，因此当前可支持窗口的 Brier 无删失缺失；代码对未知的处理有手算测试。评分尚未实现 IPCW，未来存在删失时，这份 observable-case 分数不能直接当总体无偏 Brier。所有模型使用共同的训练风险支持范围，缺少支持的窗口记录覆盖数、返回空指标。

## 保存、重放与成本

每折模型和全部折外概率位于 `.artifacts/prediction/gbdt_review_2/`。全开发集重拟合的 global/conditional/GBDT 模型各保存两份，共六个 JSON 文件，位于 `results/prediction/gbdt_review_2/`；它们供加载与后续接口检查，**不是用于生成本报告折外分数的模型**。

GBDT JSON 内嵌 LightGBM 原生文本模型、标签顺序、训练词表及风险支持，不使用 pickle。四份全开发集经验模型与第一部分文件哈希完全一致，第一部分历史结果未覆盖。

在前 100 个现有输入上的描述性 CPU API 时间，all-main 中位数约为全局 0.057 ms、条件经验 0.070 ms、GBDT 0.476 ms，GBDT P95 约 0.491 ms。这是单线程 CPU `predict()` API（含本次 Python 校验/编码/累计与支持检查）的样本内成本，排除了状态采集、缓存决策和在线调度，不是完整系统开销或 GPU 时间结论。

## 验证与复现

新增 **11 项测试通过**，全套 **102 项 pytest 通过**，`pip check` 和 `git diff --check` 通过。覆盖共享树真实训练、固定种子复现、训练词表隔离、母题分组隔离、手算 Brier/NLL、C 的未知窗口、支持范围、概率递推及保存/加载。

独立复核逐条重放 3,042 份折外模型预测，确认训练/验证母题不重叠、词表只取自训练折、输入/源码/输出哈希、每 workflow 总权重和主指标的独立复算。日志分别为 `.artifacts/d2-next/gbdt-review-2.log`、`pytest-gbdt-review-2.log`，来源快照在本轮 artifacts 的 `source/`。

```bash
cd /home/ma-user/takaya/leasepath
PYTHONPATH= .venv/bin/python -m pip install -e '.[test,measurement,prediction]'
PYTHONPATH= .venv/bin/python scripts/train_reuse_predictor.py \
  --output .artifacts/prediction/gbdt-review-replay \
  --summary-output .artifacts/prediction/gbdt-review-replay-summary
```

输出必须使用新目录。已验证环境为 Python 3.12.0、LightGBM 4.6.0、SciPy 1.18.1、NumPy 2.5.4、aarch64。配置与版本记录在 [运行清单](../results/prediction/gbdt_review_2/run-manifest.json)。

## 本部分的判断

离线链路现已覆盖真实样本读取、同母题分组、折内拟合、折外概率、同输入基线对比、保存/加载和评分。主指标没有显示 GBDT 相对条件经验基线的收益，应保留条件基线作为当前比较起点。

目前仍只有 10 个公开母题、一个来源、亚秒级工具和同一服务配置；开发交叉验证不是独立来源或时间外测试，不能声称正式泛化或真实缓存收益。数据量小和正则较强可能影响结果，但本轮没有通过消融证明具体原因。下一步在本部分 review 后优先补真实执行覆盖与状态特征；如需调整模型，另冻结开发实验，保持独立评测来源，避免反复调这十题。
