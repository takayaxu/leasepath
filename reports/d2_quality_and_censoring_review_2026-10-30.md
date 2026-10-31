# D2 任务、执行配置与删失语义复核

日期：2026-10-30。依据：首轮冻结轨迹、实际引擎 token 输入、固定版本 mini-swe-agent 源码，以及下面列出的官方来源。此次补充分析和文档，未切换模型或服务配置，也未声称新方案已经提高成功率。

## 五个任务具体是什么

它们是 SWE-bench Lite 中五个真实的 SymPy 仓库缺陷修复任务，运行各自缺陷尚未修复的 base commit。实例 ID 后缀是对应修复 PR 的编号，并不等于原始 issue 编号。

| 实例 | 实际问题 | 原始 issue / 修复 PR |
|---|---|---|
| `sympy__sympy-20590` | Symbol 等对象意外出现 `__dict__`，需要检查 mixin/父类的 `__slots__` 继承。 | [issue 20567](https://github.com/sympy/sympy/issues/20567) / [PR 20590](https://github.com/sympy/sympy/pull/20590) |
| `sympy__sympy-21614` | 矩阵表达式的 `Derivative(A, x).kind` 返回 `UndefinedKind`，应传播正确的矩阵 kind。 | [issue 21604](https://github.com/sympy/sympy/issues/21604) / [PR 21614](https://github.com/sympy/sympy/pull/21614) |
| `sympy__sympy-21847` | `itermonomials(..., min_degrees=3)` 漏掉总次数为 3 的混合项，如 `x1*x2**2`。 | [issue 21845](https://github.com/sympy/sympy/issues/21845) / [PR 21847](https://github.com/sympy/sympy/pull/21847) |
| `sympy__sympy-23262` | Python printer/lambdify 将单元素 tuple 生成 `(1)`，丢失逗号，函数返回整数而非 `(1,)`。 | [issue 23224](https://github.com/sympy/sympy/issues/23224) / [PR 23262](https://github.com/sympy/sympy/pull/23262) |
| `sympy__sympy-24909` | 单位前缀与量相乘异常，例如 `milli*W == 1`；应得到正确的缩放单位。 | [issue 24832](https://github.com/sympy/sympy/issues/24832) / [PR 24909](https://github.com/sympy/sympy/pull/24909) |

这些任务即使最终补丁很短，也需要查找大型项目中的实现、理解 SymPy 内部 API 并运行回归测试。“Lite”是基准的子集名称，不保证适合小模型第一次 Agent 诊断。前一轮选择它们主要是为了避免复杂外部依赖，未证明它们是最简单的任务。

## 已确认的执行问题

本轮 `scripts/run_d2_pilot.py` 使用自写简化提示词，没有使用官方 SWE-bench 的完整交互配置。多行命令允许失败后继续执行，末尾 `echo` 又可能让 shell 返回 0；部分回复只是把“修改代码”写在注释里，没有真正写文件。21614 的提交标记在一串输出的末尾，upstream `LocalEnvironment` 只识别输出首行的独立提交标记，任务也始终没有补丁。

已解码实际引擎 token 输入，确认 system/user/assistant 边界正确，历史命令与错误反馈确实进入后续请求，未发现消息丢失。100 次回复全部 `finish_reason=stop`，最长 284 个输出 token，因此 768 输出上限没有截断这批回复。增加输出上限可以留余量，但不是已证实的修复。

当前请求为 `temperature=0, top_p=1`，服务又使用 `--generation-config vllm`，未沿用模型发布的默认采样。固定模型 revision 的官方 [generation_config.json](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/blob/c03e6d358207e414f1eca0bb1891e29f1db0e242/generation_config.json) 是 `temperature=0.7, top_p=0.8, top_k=20, repetition_penalty=1.1`。这些参数值得对照，尚不能断言贪心解码就是循环根因。

因此，0/5 是当前模型、提示词、任务和环境组合的结果；不能直接归因于 7B 参数规模，也不能保证换 14B 就解决。

## 建议配置与任务降难度

先保留现有 7B、BF16、单卡、16K 后端，分开检验基础生成能力和 Agent 交互能力。

| 项目 | 下一轮诊断建议（待验证） |
|---|---|
| 题目 | 预先固定 10–20 个 MBPP sanitized 纯标准库、单函数问题，按依赖和题型筛选，不按模型结果筛选 |
| 基础生成检查 | 同题先做函数生成及独立测试，确认模型能完成问题本身；与多轮 Agent 分开统计 |
| Agent 任务 | `solution.py` 放签名/stub，提供可见样例；通过真实读取、写代码、运行测试和错误修正完成任务，额外评测测试保存在工作区外 |
| 提示词 | 采用固定 upstream 的 [SWE-bench 文本配置](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/config/benchmarks/swebench_backticks.yaml)，将 `/testbed` 替换为真实 workspace；保持一条命令或 `&&/||`，独立提交 |
| 预算 | 20–40 步/任务、单次输出 2,048 tokens、工具 60 秒；这是诊断初值，不是官方最优配置 |
| 循环反馈 | 同命令和同结果连续重复达到预先固定阈值后，提示重新检查路径与错误原因；保留失败与循环计数 |
| 解码对照 | 先在相同题目上比较原提示/官方提示，再对同一官方提示比较贪心与模型默认采样；固定 seed，并记录全部参数 |
| 成功判断 | 独立测试通过和真实补丁；完成标记、shell 返回 0 均不足以证明成功 |

若修正后的 7B 在这些问题上仍不稳定，再比较同系列 Qwen2.5-Coder-14B-Instruct。回到仓库级 issue 时，可另设 32K context、60–100 步和更长工具预算。官方 SWE-bench 配置的 250 步说明原先 20 步偏紧，但延长重复错误命令本身不会产生修复。每种设置另建运行记录，不能和当前固定配置混称同一次实验。

mini-swe-agent v2 的 [文本适配源码](https://github.com/SWE-agent/mini-swe-agent/blob/04d809ceab9df28f9adaed044884180159172930/src/minisweagent/models/utils/actions_text.py) 强烈推荐原生 tool calls，文本模式仍受支持。vLLM [Qwen Models 文档](https://github.com/vllm-project/vllm/blob/main/docs/features/tool_calling.md#qwen-models) 说明 Qwen2.5 可配 Hermes parser（`--enable-auto-tool-choice --tool-call-parser hermes`）。这是另一条可测路线：需同时修改客户端工具定义与 `tool_calls` 处理，并先验证两轮工具调用、反馈和实际编辑。仅增加服务 flags 不会自动把当前文本适配器转换成工具调用，也不能保证本机 7B 的可靠性。

任务来源选择：

- [MBPP 官方数据](https://github.com/google-research/google-research/tree/master/mbpp)：面向入门 Python，sanitized 文件含 427 题，适合先选无依赖小题。
- [HumanEval](https://github.com/openai/human-eval)：164 个函数补全任务，可作为下一组轻量诊断；建议通过 [EvalPlus](https://github.com/evalplus/evalplus) 的增强测试识别仅通过样例的实现。
- [BigCodeBench](https://github.com/bigcode-project/bigcodebench)：1,140 题，涉及更复杂指令和库调用，不建议作为本轮的第一步降难度替代。

MBPP/HumanEval 改成多轮 Agent 工作区后，应标记为“公开函数基准派生的 Agent 开发任务”，不能冒充真实 GitHub issue，也不能把改造后的得分称为官方 pass@1。它们能产生真实请求、工具、idle 与 lookup，但不能替代后续真实仓库、慢测试和并行依赖覆盖；禁止加 sleep 人造慢工具。这些公开老基准也不提供未见任务泛化的保证。

## 为什么 C=0，小窗口却仍有等待

完整观测的标签是 U/D/C；风险区间模型输出是 U/D/h0。h0 表示已观察该区间且尚未发生事件，C 表示关系仍开放时观测结束、之后未知。此轮 runner 先明确丢弃上下文，再结束观测，最后的关系因而是 D。

以每个 episode 的 idle 起点作为 t0，100 个 episode 的累计统计为：

| H 秒 | H 内 U | H 内 D | 已知截至 H 仍等待 | 因提前 C 而未知 |
|---:|---:|---:|---:|---:|
| 0.01 | 0 | 0 | 100 | 0 |
| 0.1 | 56 | 3 | 41 | 0 |
| 1 | 74 | 4 | 22 | 0 |
| 10 | 95 | 5 | 0 | 0 |

456 个快照从各自 t0 看，0.1 秒内有 300 个 U、11 个 D、145 个已知等待。两种统计的起点不同，不能混用。完整表、episode 加权计数和输入哈希见 [horizon-summary.json](../results/prediction/d2_first_round/horizon-summary.json)；这是累计窗口摘要，不是已经实现了 hazard 训练行导出。

例如实际 2 秒后 U：如果轨迹完整保留，前 0.1 秒是已知等待，最终仍是 U。如果预先规定只观察 H=0.1 秒，就能另建固定 H 的行政删失视图，将 T>H 的记录记为 C at H。用户“小窗口应有 C”的直觉在后一口径下成立；必须显式声明截断策略，并保留原始最终标签。

对于离散 hazard，发生事件前已完整观察的区间训练 h0；事件所在区间训练 U 或 D；真实 C 之后的区间不计 loss，不能将未知填成等待。C 不是需要凑齐的第三种竞争事件，无删失的完整观测数据也可以合法训练。若以后预算耗尽只暂停任务且允许恢复，应该保留上下文并记录观测截断，不应无条件沿用本轮的明确关闭 D。
