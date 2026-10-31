# 离线训练第一部分：经验竞争风险基线 Review

日期：2026-10-30。状态：**经验基线已实现并用现有真实数据拟合，等待本部分 review**。本部分包括全局/条件经验风险、删失处理、权重、保存/加载与概率预览。按分阶段 review 的要求，题目分组评测和共享 GBDT 留在后续部分。

后续记录（2026-10-31）：已按用户指示完成 [第二部分 GBDT 与分组开发评测](gbdt_review_2.md)。本文及第一部分模型/结果保留当时的拟合口径。

## Review 入口

| 文件 | 用途 |
|---|---|
| [`reuse_predictor.py`](../src/leasepath/reuse_predictor.py) | `EmpiricalRiskPredictor.fit/predict/save/load`，经验风险与累计概率 |
| [`empirical_risk_baseline.json`](../configs/prediction/empirical_risk_baseline.json) | 固定时间边界、分组、回退和权重配置 |
| [`fit_empirical_risk.py`](../scripts/fit_empirical_risk.py) | 校验输入哈希与 workflow 范围，拟合四份基线并验证保存加载 |
| [`test_reuse_predictor.py`](../tests/test_reuse_predictor.py) | 手算概率、竞争事件、删失、权重与推理边界测试 |
| [`结果摘要`](../results/prediction/empirical_baseline_review_1/summary.json) | 数据范围、支持区间与运行检查 |
| [`概率示例`](../results/prediction/empirical_baseline_review_1/prediction-examples.json) | unknown/waiting_tool/queued 状态的真实输入与各区间输出 |

## 概率定义

在快照状态固定的条件下，对每个未来区间 k，统计尚未发生 U/D 的风险记录：

`hU = U 的加权计数 / 风险集加权计数`

`hD = D 的加权计数 / 风险集加权计数`

`h0 = 已完整观测且区间内未发生 U/D 的加权计数 / 风险集加权计数`

三者和为 1。通过 `F_U(k)=F_U(k-1)+S(k-1)*hU(k)`、`F_D(k)=F_D(k-1)+S(k-1)*hD(k)`、`S(k)=S(k-1)*h0(k)` 得到累计概率；`F_U+F_D+S=1`。预测未来各区间始终使用当前快照的状态，不倒填未来工具或队列状态。

U 和 D 共用风险集，D 不当成 U 的删失。真实 C 只贡献已经完整观测的等待区间；包含删失点的部分区间不计，之后不再提供标签。该保守离散化复用已有 `derive_hazard_rows`。当前真实集 C=0，删失处理由手算测试覆盖；行政删失派生视图未混入拟合。

## 两种基线与证据不足处理

- **全局基线**：每个相对时间区间一组加权 U/D/waiting 频率。
- **条件基线**：按 `tool_name`、`phase`、`elapsed_idle_s` 桶分组。elapsed 桶为 `[0,.03)`、`[.03,.1)`、`[.1,.3)`、`[.3,1)`、`[1,+inf)` 秒。分组与区间的风险集至少涉及 3 条不同 workflows 时，局部计数向全局概率收缩：`(局部计数 + 1 * 全局概率) / (局部总权重 + 1)`；否则直接回退全局。

`prior_weight=1` 的单位是本次权重下的一条完整 workflow。这个阈值是开发初值，尚未通过分组验证选参；不同 workflows 也不自动代表不同母题。条件基线仅使用当前已有特征中的上述三项，不能称为完整 CacheWise-style 参数基线；后续同输入 GBDT 比较须先沿用相同可用信息。

当前全局风险集仅覆盖右端点 `0.01,0.03,0.1,0.3,1` 秒。更长区间没有风险记录，输出 `supported=false`，`hU/hD/h0/F_U/F_D/survival=null`，不会伪造等待或外推。所有原计划时间边界仍保留。未见过的 backend compatibility 也返回不可估计；未知工具/状态分组在同一兼容后端内回退全局。

局部经验频率可出现 0 或 1，这表示开发样本中的频率，不能解释为部署时的确定性。特别是 1 秒边界前所有开发样本已结束，仍不足以证明真实长尾为零。

## 数据范围与权重

| 范围 | workflows | 不同母题 | episodes | 快照 | 权重总和 |
|---|---:|---:|---:|---:|---:|
| all-main | 19 | 10 | 185 | 729 | 19 |
| qualified | 14 | 10 | 76 | 285 | 14 |

19 条后端证据完整执行包括任务失败；HTTP 400 的不完整 workflow 仍隔离。14 条合格执行保留为成功筛选的对照，不代表总体。两组各拟合 global 和 conditional，共保存四份 JSON 模型。

原始快照权重使每个 episode 总重为 1。本次再除以所属 workflow 的 episode 数，使每条 workflow 总重为 1；快照权重不按风险区间数量再次归一化。这避免长循环执行仅因 episodes 多而主导拟合。不同配置仍可能来自同一题，本部分未做独立验证，不能把 19 条 workflows 当作 19 个独立题目。

拟合入口核验来源 summary 中四个数据 JSONL 的哈希、quality manifest 中的 workflow 成员以及原始 episode 权重。`predict()` 仅接受完整 `features` 白名单，传入未来标签或外层 ID 直接拒绝；模型文件不存任务答案、未来轨迹或样本 ID。

## 一组实际输出

以下来自 all-main 条件模型、第一个 idle 起点快照（elapsed=0、工具与 phase 均未知），完整精度和其余状态见概率示例：

| 未来区间 / 秒 | hU | hD | h0 |
|---|---:|---:|---:|
| (0, 0.01] | 0.035764 | 0.008341 | 0.955895 |
| (0.01, 0.03] | 0.203304 | 0.182079 | 0.614617 |
| (0.03, 0.1] | 0.729868 | 0.009619 | 0.260513 |
| (0.1, 0.3] | 0 | 0 | 1 |
| (0.3, 1] | 1 | 0 | 0 |
| (1, 3] 及之后 | 不可估计 | 不可估计 | 不可估计 |

这是条件 hazard，不能把不同行的 hU 直接相加。0.1 秒累计 `F_U≈0.658905`、`F_D≈0.188041`、`S≈0.153054`。**这些输出使用拟合数据预览，不是留出预测，也不是准确率或校准结论。** 当前 queued 示例在某些细分桶会回退全局；是否需要调整分组应在后续分组评测中判断。

## 验证与复现

新增 10 项基线测试，与既有 4 项 hazard 测试合计 14 项针对性检查通过；全套 **91 项 pytest 通过**。四份模型全部完成逐快照保存/加载一致性检查、概率范围/和为 1、累计概率单调性检查。日志为 `.artifacts/d2-next/pytest-empirical-review-1.log`。

```bash
cd /home/ma-user/takaya/leasepath
PYTHONPATH= .venv/bin/python -m pytest -q tests/test_reuse_predictor.py tests/test_d2_hazards.py
PYTHONPATH= .venv/bin/python scripts/fit_empirical_risk.py \
  --output .artifacts/prediction/empirical-baseline-replay \
  --summary-output .artifacts/prediction/empirical-baseline-replay-summary
```

输出目录必须是新路径。本轮可提交模型、输入/源码哈希和摘要在 `results/prediction/empirical_baseline_review_1/`；完整概率预览在 `.artifacts/prediction/empirical_baseline_review_1/`，明确标为 `training_preview_not_held_out`。

## 本部分之后

本部分 review 重点：U/D/waiting 与 C 的区别、workflow 权重口径、条件分组及回退、无支持区间的输出契约。确认后下一部分冻结按母题分组的开发折并实现经验基线评测，分别报告 idle 起点、工具等待与已排队状态；再单独实现小型强正则共享 GBDT，复用相同分组、输入与评分规则。
