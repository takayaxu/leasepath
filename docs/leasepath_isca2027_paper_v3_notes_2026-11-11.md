# LeasePath v3：负载特征分析补充说明

日期：2026-11-11。依据用户要求补充Agent workload characterization；以上一版v2为基线，旧稿、冻结实现、原始实验与留出集合保留。

## 审阅入口

- [新版PDF](leasepath_isca2027_paper_v3_2026-11-11.pdf) · [TeX](leasepath_isca2027_paper_v3_2026-11-11.tex)
- [中文分析报告](../reports/agent_workload_characterization_2026-11-11.md)
- [统计JSON](../results/analysis/agent_workload_v1_2026-11-11/summary.json) · [数据目录](../results/analysis/agent_workload_v1_2026-11-11/)
- [图表与来源清单](leasepath_isca_v3_assets/manifest.json)
- [更新后大纲](leasepath_iclr_outline_v3_2026-11-11.md)
- [后续实验计划](leasepath_followup_experiments_2026-11-11.md)

## 新增内容

共21个图注和表注。Figure 1在图注中分(a)/(b)/(c)说明具体对话、历史与KV前缀的映射，以及共享缓存取舍；方法总览Figure 6按(A)/(B)/(C)简要说明工作流及两个放大部分。其余图注和表注保持单句总结，必要的计量细节保留在正文。生成脚本同步采用相同图注。

Figure 1 重绘为三部分问题引入图：(a) 用“给connect增加timeout参数”的简短对话，展示读取文件、应用修改、工具确认及最终回复，三次模型输入重复携带先前消息；(b) 用同色H1/H2/H3把这些消息跨度对应到增长的KV前缀，保留原有模型/工具等待示意；(c) 保留多会话占满共享KV、面对新请求时的保留/淘汰取舍。对话为示意，不是新增实测记录，长度和缓存块也不代表实测比例。正文及扩展图注明确KV复用依赖相同前缀token与兼容执行配置。评分机制仍在方法部分；原v2图保留，新版提供PDF/SVG/PNG及生成脚本，引言和图注同步修改。

在Background后、方法前加入 **Agent Workload Characterization**：来源/覆盖表，六项目/用户轮次/模型调用/工具统计，完整输入与输出CDF，上下文增长与实际兼容前缀比例，工具/用户/ready-to-lookup时间分布，决策剩余时间与阶段组成。

附录补模型/工具调用次数、每用户轮次循环和会话跨度。Results补静态GBDT日志上同状态首次淘汰诊断与第一个真实分歧案例，避免只用构造CDF解释方法。

新段落明确限定：合格采集为六个authored开发项目，用户需求与思考时间预设、模型和工具真实；不是自然人类工作记录。采集192块，五臂E2E150块；两套时长不合并为额外重复。所有旧表图与Section手工编号改为标签引用。

## 核心发现

- 48个用户轮次产生244次模型请求、196次真实工具调用；80.33%的模型请求由工具反馈触发。
- 输入中位数3989.5 tokens，输出39；全部输入出现量与输出量比61.84，不等于速度比。
- 238次continuation的前一个输入块对齐前缀全部兼容；累计占输入94.31%。这是可复用机会，**不是命中率**。
- 工具调用P95仅0.318秒，ready至lookup中位48.225秒；新数据偏短工具、长队列与控制器延迟。工具通知处理延迟中位9.214秒，不能归为工具执行。
- 1079个决策快照中77.48% queued，所有标签为U。H30覆盖52.83%的原始快照需求；完整间隔只覆盖27.73%，二者不可混合。
- 静态GBDT的135个可比较首次淘汰状态中有11次评分选择不同。首个分歧是局部负例：分布淘汰的logs在37.47秒后被需求，窗口会淘汰的API在78.81秒后被需求。没有把排序变化写成已证明的额外E2E收益。

## 图表与数据

新图位于 `leasepath_isca_v3_assets/figures/`，同时提供矢量PDF和PNG：`workload_context`、`workload_timing`、`workload_request_durations`、`workload_loops`、`workload_score_case`。前三组位于方法前，循环在附录，评分案例在结果部分。

问题引入图为 `figure1_problem_setting`；由 `generate_problem_setting_figure.py` 生成，不读取实验数据。

2026-11-12重绘方法总览 `leasepath_method_overview`（Figure 6，方法§5引用）。(A)改为三条请求历史，展示同色共享前缀P、会话历史a/b、新增上下文Δ、文件/终端/数据库工具、严格FCFS和真实工具反馈闭环；空间足够时直接推理，压力时进入B/C。(B)加入实际训练链：特征观测→风险区间表→逐轮拟合分类树→部署→当前状态预测→hazard经生存递推得到无条件需求质量→指数折扣。核对初始存档为6开发项目、1,079快照、9,229有效训练行；14编码列保留工具类别，40轮、学习率0.05、深度≤2、叶≤4，保存120棵三分类树。训练内boosting与运行中从头refit分开标注；底部保留100新增成熟episode/至少5项目及请求边界切换门槛。(C)共享前缀树、active保护锁、候选分数、逻辑plan/rescan和native KV前后槽位对应显示，最大owner/字节、无owner优先、支持不足转retired-first LRU、完整计划统一应用等语义保留；只释放已用槽位，不缩小预留buffer。评分继续固定H30、τ10、cost=1。

短标签、实际图标与一致前缀配色替换原来的稀疏框图。图标采用有来源/版本/SHA256记录的Lucide矢量，许可随源码包保留；GBDT画法参考LightGBM/Scikit-learn官方文档并按本实验重画，出处见[设计参照](leasepath_isca_v3_assets/design_references/README.md)。训练特征、超参数、区间标签、静态/动态区别及可追溯路径见[GBDT输入与训练说明](leasepath_gbdt_training_contract_2026-11-12.md)。快照数和训练行数为真实初始fit数据；请求历史、表中例行、树形、柱高及0.07/0.19候选值为示意。初始终端标签全U，mask行说明通用删失处理，未将其写成初始数据包含D/C。未新增推理、训练或实验。

2026-11-12新增真实工具记录展示：`workload_trace_examples`放在正文§3.1来源与计量介绍附近，位于PDF第4页，为Figure 2。按用户要求改成三列两行，图高从5.85压到2.85英寸（缩减51.3%，嵌入论文后等比例缩放），紧凑排布六项目各一个实际调用，保留name/arguments/status及从纳秒起止时间计算的duration_ms。按固定规则选每项目指定工具的第一次调用，均为首用户轮次，覆盖9种工具中的6种；源文件SHA256与归档核对。原附录独立小节及长解释移除，必要说明合并进§3.1正文。完整原记录、简化JSONL、库存项目首轮5请求/4工具/1快照/验收的关联示例位于`examples/`，字段说明见[数据集示例](leasepath_dataset_examples_2026-11-12.md)。所有示例来自合格开发采集，不新增采集、不访问留出。原末页CATraces解释段落仍已删除，附录仅保留会话分布图及简短图注。

`workload_request_durations`放在§3.4的时间分析开头，包含工具执行、同会话lookup到lookup间隔、请求ready到native release响应三条独立CDF，逐图标注P50/P99。正文明确区分请求开始间隔、release后的idle间隔和完整用户轮次响应，不使用示例图的数值或曲线。

统计CSV包含逐请求、逐轮次、逐项目、逐工具、完整需求间隔及首次淘汰状态。仅摘要和这些匿名统计进入源码包，完整原始工具内容和compact token中间文件不重复打包。

公开CATraces作为来源与字段覆盖参照；没有把其脱敏日志跨度与我们的独立工具执行或native lookup曲线叠加，也没有声称复现了其论文全部统计。2026-11-12按用户要求删除末页图下的CATraces解释段落，末页以图及图注结束；生成脚本同步删除。

## 复现与构建

从仓库根目录依次执行：

```bash
python -B docs/leasepath_isca_v3_assets/extract_characterization.py
python -B docs/leasepath_isca_v3_assets/analyze_workload.py
python -B docs/leasepath_isca_v3_assets/generate_request_duration_figures.py
python -B docs/leasepath_isca_v3_assets/generate_problem_setting_figure.py
python -B docs/leasepath_isca_v3_assets/generate_trace_examples.py
python -B docs/leasepath_isca_v3_assets/generate_method_overview.py
python -B docs/leasepath_isca_v3_assets/assemble_paper.py
bash docs/leasepath_isca_v3_assets/build_paper.sh
```

提取读取约1.2GB已归档controller/backend文件，流式逐字节核对原始SHA256，再输出紧凑中间表；不运行模型、不训练、不改日志。分析保留每请求输入=hit+prefill、244/196/48计数、完整前缀、间隔分解与原始victim排序等不变量检查。

`assemble_paper.py`从v2重建并覆盖v3；人工修改v3正文后仅运行编译脚本，避免覆盖编辑。稿件沿用v2内置acmart 2.21及本机默认字体；为带附录草稿关闭最后页自动平衡，附录使用单栏，正文仍双栏。仍是内容审阅稿，提交号、最终模板字体、页数和参考文献核验沿用v2所列待处理事项。

2026-11-12版面修正：关闭microtype的边缘字符悬挂，将第一页及全篇的标点、断词连字符收回栏内；三题结果图（figure2_preliminary_results）宽度微调至98%，解决密集浮动体下方2.90 TeX pt的纵向超出。保留模板页边距、字体大小和正文内容。构建脚本现将Overfull hbox/vbox视为失败，并在发布PDF前执行`check_paper_layout.py`：读取实际TeX页面几何，扫描每页正文的PDF文字边界；红色审稿行号、页眉和页码单独排除。重绘方法总览后当前19页、2,332条正文/图中文字行检查通过（0 TeX溢出、0越界）；两处高公式加分页保护，最新行数见`build/layout-checks.json`。后续内容改动必须重新编译、通过此检查，并查看改动页、第一页和整篇缩略图；自动检查不能替代目视确认图表可读性及位置。

## 后续仍缺什么

现有统计不能填补独立来源、长工具、自然用户返回、真实D/C、强外部方法和平衡性能重复。后续E10–E15保持待执行；本轮只增加描述性证据。无需为了复刻CacheWise图表而重跑全部公开基准，先用新增来源扩大当前缺失覆盖。
