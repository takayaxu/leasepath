# 本机部署与路径说明

部署日期：2026-10-30（Asia/Shanghai）。仓库为 `takayaxu/leasepath`，本机根目录为 `/home/ma-user/takaya/leasepath`。

本项目是 Python 研究工具，CPU 环境支持事件契约、状态快照、缓存回放与数据审计。D2 已增加本地 Ascend 推理服务，接口为 `http://127.0.0.1:8201/v1`；模型、Agent 和插桩安装见 [D2 首轮部署](d2_first_round.md)。当前任务采集与独立评测入口为 [MBPP 派生执行轮](d2_mbpp_round.md)，实际质量和数据规模见 [本轮报告](../reports/d2_mbpp_round_acceptance.md)；[首轮报告](../reports/d2_first_round_acceptance.md) 保留历史结果。

跨轮的数据目录、清单、字段与恢复边界统一见 [数据集说明](dataset_card.md)。`.artifacts/` 不随 Git 分发，仅克隆仓库不会恢复完整训练样本。

2026-10-31 新增 [秒级任务扩展 v1](d2_expansion_v1.md)，沿用相同服务和两个虚拟环境；准备、执行、独立复测命令见该文。数据另存 `.artifacts/d2-expansion-v1/`，小型结果在 `results/prediction/d2_expansion_v1/`，没有覆盖原 MBPP 或 GBDT 结果。该轮回归 105 项通过。

同日继续完成 [3–10 秒/失败后重测 v2](d2_repair_v2.md)：主数据在 `.artifacts/d2-repair-v2-guided/`，最初不成功的诊断在 `.artifacts/d2-repair-v2/`，小型结果在 `results/prediction/d2_repair_v2/`。使用 `run_d2_repair_guided.py`，复现命令见该文。该轮回归 **114 项通过**，日志为 `.artifacts/d2-repair-v2-guided/pytest-regression.log`；采集阶段未重训，旧模型结果保持原版本。

[母题扩展 v3](d2_diversity_v3.md) 沿用同一服务与环境，新增 34 个 MBPP 母题。任务、轨迹、独立评测及冻结事件在 `.artifacts/d2-diversity-v3/`，可审阅清单在 `results/prediction/d2_diversity_v3/`；从准备、参考验证到采集/导出/核验的命令见该文。全量运行保留失败，完整性和任务正确性分别统计。最新跨轮数量和路径以 [数据集说明](dataset_card.md) 及 [50 母题清单](../results/prediction/dataset_inventory_50parents_2026-10-31.json) 为准，旧轮与模型结果保持原版本。

四轮合并后的 [训练 v2](../reports/prediction_v2_50parents_review.md) 已完成，入口为 `train_prediction_v2.py`，独立复核入口为 `verify_prediction_v2.py`。合并开发样本、折内模型和 OOF 保存于 `.artifacts/prediction/prediction_v2_50parents/`；6 份全量重拟合模型、指标和清单在 `results/prediction/prediction_v2_50parents/`。当前回归 **121 项通过**，日志在前者的 `pytest-regression.log`。训练仅使用 CPU `.venv`，不要求启动 Ascend 服务，也未新采集 Agent 任务；完整新目录复现命令见该 Review。

## 当前主线：Codex CLI + 本地 32B（2026-11-01）

按 [指导.md](../指导.md) 完成 [Codex/32B Review](../reports/real_development_stage0_codex32b_review_2026-11-01.md)。入口为 `scripts/run_codex32b.py`，协议实现为 `src/leasepath/codex_protocol.py`，实际 shell 观测在 `scripts/codex32b_shell.py`。配置 v1 为原生 Chat 工具转换，模型输出未带工具标签；最终 v2 为事先声明的严格整段 JSON 协议，Codex CLI 本身执行工具与反馈循环，网关仅转换。所有函数 schema 保留；未支持事件显式失败，不从任意文本提取命令。

两个不同 fixture 通过真实多轮和独立检查后，执行 Flask-5063 开发来源联调：24 次模型请求、空补丁，独立测试 55 passed / 2 failed，停止本轮。新五题、完整 JCT 和阶段 1 均未触发。新流 45 个后端的 token IDs 精确对齐、42 次实际 shell 有调用 ID、41 个结果进入模型；最后一个因预算拒绝没有转发。v2.1 独立核验通过（41 U / 4 D），只代表此次串行完整历史诊断，不能移植为全部 Codex 生命周期支持。

大文件在 `/home/ma-user/work/leasepath/real-development-v1/codex32b-20261101-a/` 和 `codex32b-20261101-service-a/`；[小型摘要与清单](../results/real_development/stage0_codex32b_2026-11-01/summary.json) 保留各次失败、预算、实现身份、独立验收和资源证据。180 项回归通过，218 个历史小产物哈希未变；本轮自有服务已停止、8 卡空闲。再次运行必须另建预算和 run，不能继续本轮调参。

## 阶段 0 修订 v2 诊断状态（2026-11-01）

最新 [诊断 Review](../reports/real_development_stage0_diagnosis_v2_review_2026-11-01.md) 记录新版 revise.md 的执行。旧第二轮 90 个实际后端 prompt 与固定 tokenizer/template 的 token IDs 一致；旧工具输出省略区间、错误编辑和恢复轨迹单独审查。新接口在 `real_tools_v3.py`，runner 为 `run_stage0_diagnostics_v3.py`，配置为 `real_development_diagnostic_v3.json`。L1 单题一次调用、4,096 输出 token、600 秒；后续 L2/L3 为预定义但未触发的 24 调用/900 秒。每次运行先验证私有环境，预请求计算 input+output≤16K；不压缩历史、不自动重试。

当前 Qwen2.5-Coder-32B 的 L1 为 0/3；条件触发本机已有 Qwen3.8-27B 对照，其 L1 也为 0/3，三份语法有效补丁仍不满足目标/回归。两分支都没有达到 ≥2/3 推进门槛。本轮结束，共使用 6/21 次、3 个来源组；L2/L3、预选新五题和完整 JCT 未运行，阶段 1 暂缓。没有将局部诊断写成自主任务或未见验收。当前回归 156 项通过，服务均已停止，8 卡无运行进程。

当前 32B 的新证据在 `/home/ma-user/work/leasepath/real-development-v1/stage0-diagnosis-v2-20261101-32b-a/`，3 个 backend 的普通流 v2.1 及独立检查通过。替代诊断在 `stage0-diagnosis-v2-20261101-alternative-a/`：其混合架构 probe=off，只作本地能力对照，不进入主 U/D/C。已有 Qwen2 的生命周期/容量通过不能移植到替代模型。替代的模板/结束 token 独立预检并冻结，源码和依赖仍用同一套私有环境。再次运行必须另建 run 和预算记录，不能覆盖本轮或自动追加条件。源码编辑接口、各层帮助范围、未覆盖工作负载和复核入口见诊断 Review。

## 上一轮阶段 0 补救状态（历史，2026-11-01）

当轮 [补救 Review](../reports/real_development_stage0_revise_review_2026-11-01.md) 与 [机器摘要](../results/real_development/stage0_revise_2026-11-01/summary.json) 记录了 revise.md 的两轮有界执行。私有环境消除了宿主 flask/Click 回退；一般请求投影 v2.1 已通过完整流、块生命周期、独立源引用和五项损坏边界核验。新服务目录是 `/home/ma-user/work/leasepath/real-development-v1/stage0-revise-20261101-service-a/`，原始流和两轮失败完整保留。

两轮独立正确性均为 0/3；第二轮三份候选生产补丁均含 SyntaxError。当前链按两轮停止条件结束，不再追加配置；预选两题和最终五题验收未执行，阶段 1 暂缓，完整 Agent on/off 与 JCT 非劣性未测量。当前回归 151 项通过。服务已完成 TP checkpoint 并停止，8 卡无运行进程，本轮 IPC 无新增残留。

运行时用 `preflight_real_development_tools.py` 验证每题私有解释器、源码和依赖导入，再用 `run_real_development_tasks_v2.py --agent-config <冻结配置>`；独立评测使用 `evaluate_real_development_pilot_v2.py`，候选语法错误作为任务质量失败。新版导出使用 `export_real_development_v2.py`，保留独立的 events/labels/coverage/protection/unresolved 文件。185/185 后端请求投影、306 次 lookup 全保留；第一轮 10 次失败前端尝试未进入引擎，仍单列不支持及原始重试清单缺陷。旧 B 运行缺映射的 33 个受控请求不通过猜测补全，原普通流 59 条标签保持一致。

请求前运行 `real_development_boundary.py inventory --run <新目录>`；全部请求排空后运行 `finish` 完成 rank/engine checkpoint 并停止自有服务。checkpoint 会关闭 observer，之后不能继续向该运行发送实验请求。完整离线复核命令与适用范围见补救 Review。

## 补救前阶段 0 实测状态（历史，2026-11-01）

当轮 [Review](../reports/real_development_stage0_review_2026-11-01.md) 与 [机器摘要](../results/real_development/stage0_2026-11-01/summary.json) 记录了 TP=2、32B BF16、16K 的真实服务验收。3 个 issue 使用 mini-swe-agent 回退链执行，独立正确性 0/3；Codex 工具循环未通过，阶段 1 暂缓。当前回归 139 项通过。实验服务已停止，8 张卡均无运行进程，每卡约 3.3 GiB 是系统保留；此前“不停止现有服务”的安排已被用户后续“释放显存”授权更新。

主实测目录是 `/home/ma-user/work/leasepath/real-development-v1/stage0-20261101-tp2-b/`，CPU 任务冻结和环境仍在 `stage0-20261031-a/`。新版探针按 TP 逻辑事件去重，rank 分别记录 KV 物理字节。原始探针包逐 run 固定在 `plugin/`；以对应 `service-manifest.json` 的哈希为准。v0.1 导出器只接受单 lookup、正常完成、无抢占的 Agent 请求；多 lookup/抢占证据保存在 raw，尚不导入训练。

启动新运行使用 `scripts/real_development_service.py start --run <全新数据盘目录> --devices <两张空闲卡>`，会检查显式设备、无用户进程、系统保留显存和已核验权重。受控压力配置另加 `--max-num-seqs 8 --num-gpu-blocks-override 192`；该容量不能当作默认服务上限。开销基线通过 `--probe-mode off` 在启动时完全跳过引擎钩子，使用独立目录。

结束采集时先排空请求，调用 `/collective_rpc` 的 `leasepath_checkpoint`，再用 service 脚本 stop；导出必须通过预先记录的 expected-streams 清单、终端哈希、rank/engine checkpoint 和独立块审计。单独 SIGTERM 无法保证多进程观察流完整，首次实测 A 的不完整日志已保留并排除主标签。完整复现入口见最新 review。

## 历史资源规划（2026-10-31）

[只读资源快照](../results/planning/real_development_2026-10-31/hardware_snapshot.json) 核查到 8 张 64 GiB HBM 的 Ascend 910B2、192 逻辑 CPU、约 1.5 TiB RAM。当时所有卡均有其他负载，每卡约占用 45 GiB；以下历史启动命令不意味着当前仍有足够可用资源。新实验需安排独占卡窗口，不直接停止已有服务。

下一轮候选是 Codex CLI＋Qwen2.5-Coder-32B-Instruct BF16＋vLLM-Ascend，TP=2 起步，**尚未通过协议、模型、多卡探针和任务验收**，不能把它当作已部署配置。现有单卡探针/导出器的请求 ID、单次 lookup、无抢占假设需独立扩展。资源、接入门槛与每阶段 review 见 [真实开发计划](../reports/real_development_lookup_plan_2026-10-31.md)。

checkout 所在 overlay 当时约剩 45 GiB，数据盘 `/home/ma-user/work` 约剩 3.6 TiB。新权重、原始 tokens、工具轨迹和运行快照使用 `/home/ma-user/work/leasepath/real-development-v1/`，配置使用显式路径和冻结 manifest。已有 `.artifacts/d2*` 和 `models/` 保持原路径与来源。

**阶段 0 已开始准备，整体尚未通过。** 最新资源核查仍为所有卡有负载；用户允许在现有负载退出后使用任意空闲卡，不停止现有服务。候选配置在 `configs/prediction/real_development_stage0.json`，`preflight_real_development.py` 只读检查资源，未满足门槛时返回 2；它不是资源预约器或新服务启动器。

本轮本地证据根目录为 `/home/ma-user/work/leasepath/real-development-v1/stage0-20261031-a/`。`tasks-b/` 是修正源码完整保存后的任务冻结，`environment-checks-d/` 为最终独立环境验收；最初三次尝试原样保留。Flask 使用独立 Python 3.12 环境，旧 pytest 任务使用独立 Python 3.9 环境；完整依赖锁在本轮 `manifests/`。32B 权重已下载并通过来源哈希核验，3 个真实 issue 的参考修复共通过 131 项相关文件测试，不计为 Agent 成功。项目回归 130 项通过。模型准备、Codex 协议实测范围、未实现探针和复现入口见 [阶段 0 Review](../reports/real_development_stage0_review_2026-10-31.md)。尚未采集新的 Agent/lookup 主数据或进入阶段 1。

## 环境与安装

本机系统为 Huawei Cloud EulerOS 2.0（aarch64），Python 3.12.0。CPU 工具依赖安装在仓库内的 `.venv/`，Agent 使用 `.venv-agent/`，Ascend 服务复用本机独立推理环境；不使用原主机的虚拟环境。

```bash
cd /home/ma-user/takaya/leasepath
python3 -m venv .venv
source .venv/bin/activate
# 本机启动环境会注入 Ascend/CANN 包，CPU 验收不需要该注入。
unset PYTHONPATH
python -m pip install -e '.[test,measurement]'
python -m pip check
# 无需外部数据即可运行的检查
python -m pytest -q -k 'not full_pinned_audit'
```

每次新开终端后，重新 `cd`、激活 `.venv` 并 `unset PYTHONPATH`。也可单次执行 `PYTHONPATH= .venv/bin/python ...`。清除操作只影响当前进程/终端，不修改系统 Ascend 配置。

已验证依赖：jsonschema 4.26.0、numpy 2.5.4、pytest 9.2.0、matplotlib 3.11.2、mpmath 1.4.1。项目尚无依赖锁文件，版本范围以 `pyproject.toml` 为准。

## GitHub 访问

本机 GitHub CLI 为 `/home/ma-user/.local/bin/gh`，已通过设备授权登录 `takayaxu` 并配置 HTTPS Git 凭据助手。可检查：

```bash
/home/ma-user/.local/bin/gh auth status
/home/ma-user/.local/bin/gh repo view takayaxu/leasepath
```

认证保存在用户配置目录，不进入仓库。其他机器需自行登录；克隆地址为 `https://github.com/takayaxu/leasepath.git`。

## 恢复 CacheWise 固定快照

Git 只保留 `data/sources/cachewise/provenance.json` 和说明，`upstream/` 被忽略。本机已恢复固定 commit `181c435a090d328d00bbbee4c8eeb27d32f3abd2` 的 320 个文件，压缩包与逐文件 SHA-256 均与仓库中的 provenance 一致，没有改写来源记录。

在新的 checkout 中，可下载到一个尚不存在的临时子目录，再复制原始快照：

```bash
cd /home/ma-user/takaya/leasepath
source .venv/bin/activate
unset PYTHONPATH
snapshot_stage=$(mktemp -d /tmp/leasepath-cachewise.XXXXXX)
python scripts/collect_cachewise.py \
  --source "$snapshot_stage/snapshot" \
  --output "$snapshot_stage/audit"
cp -a "$snapshot_stage/snapshot/upstream" data/sources/cachewise/
python -m pytest -q
```

`collect_cachewise.py` 的目标目录已存在时只做校验；首次克隆已有说明目录，因此不能直接向默认目录下载缺失快照。不要以临时下载生成的 provenance 覆盖仓库中的固定来源记录。

## 中文字体与绘图

绘图使用 Agg 后端，不需要桌面。本机将 Debian `fonts-wqy-zenhei` 包内的字体安装到用户目录 `~/.local/share/fonts/wqy-zenhei.ttc`，字体族为 `WenQuanYi Zen Hei`。Ubuntu 也可使用系统包管理器安装 `fonts-wqy-zenhei`。

无管理员权限时可从 Debian 软件源的华为云镜像下载并提取字体：

```bash
font_stage=$(mktemp -d /tmp/leasepath-font.XXXXXX)
curl -fL --retry 3 --max-time 300 \
  https://mirrors.huaweicloud.com/debian/pool/main/f/fonts-wqy-zenhei/fonts-wqy-zenhei_0.9.45-8_all.deb \
  -o "$font_stage/font.deb"
(cd "$font_stage" && ar x font.deb && tar -xf data.tar.xz)
mkdir -p "$HOME/.local/share/fonts"
cp "$font_stage/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc" "$HOME/.local/share/fonts/"
fc-cache -f
```

字体应在首次绘图前安装；若 Matplotlib 已缓存缺字体的列表，可以只删除其可再生的 `fontlist-v*.json` 缓存再启动绘图进程。

## 本机验收与输出

本次输出写入被 Git 忽略的 `.artifacts/deployment-2026-10-30/`，避免覆盖仓库内已有历史验收产物：

```bash
cd /home/ma-user/takaya/leasepath
source .venv/bin/activate
unset PYTHONPATH
python -m pip check
python -m pytest -q
python scripts/check_prediction_d1.py \
  --output .artifacts/deployment-2026-10-30/d1_contract
python scripts/check_event_contract.py \
  --output .artifacts/deployment-2026-10-30/event_contract
python scripts/plot_prediction_d1.py \
  --input .artifacts/deployment-2026-10-30/d1_contract
python scripts/environment_probe.py \
  --output .artifacts/deployment-2026-10-30/environment.json
```

已通过：依赖完整性检查、67 项 pytest、D1 50 项检查、事件契约 8 个正例接受及 16 个反例拒绝。已生成两张中文验收图的 PNG/PDF，保存在上述 `d1_contract/figures/`，验收导出哈希校验通过。D1 为人工合成契约验收，不代表真实工作负载预测或 GPU 性能结论。

以上是最初 CPU 部署的记录。首轮 D2 为 76 项测试通过，5 个任务修复 0/5，详见 [首轮报告](../reports/d2_first_round_acceptance.md)。MBPP 轮为 81 项 pytest 通过，D1 50 项、事件契约 8 个正例和 16 个反例继续通过；CPU 与 Agent 环境均通过 `pip check`，证据保存在 `.artifacts/d2-next/pytest-final.log` 和 `.artifacts/d2-next/regression/`。加入经验竞争风险基线后为 **91 项 pytest 通过**，日志为 `.artifacts/d2-next/pytest-empirical-review-1.log`，拟合步骤与输出见 [第一部分 Review](../reports/empirical_baseline_review_1.md)。

## 迁移范围

| 资源 | 本机状态 / 路径 |
|---|---|
| 项目与 Python 环境 | `/home/ma-user/takaya/leasepath`、`.venv/` |
| CacheWise 固定快照 | 已恢复到 `data/sources/cachewise/upstream/`，不提交原始数据 |
| TraceLab 原始数据与大型派生文件 | 尚未下载，按 README 的 `prepare_tracelab.py` 入口另行获取 |
| 原主机归档 | 未迁移；历史位置见 `project_cleanup.md`，当前运行链不依赖归档 |
| Qwen GGUF / llama.cpp / 原生动态库 | 未迁移；预期路径分别为 `models/`、`.artifacts/llama-src/`、`.artifacts/llama-build/` 和 `.artifacts/d6-native/liblp_native.so` |
| NVIDIA GPU 后端 | 本机未提供 `nvidia-smi`，未执行 CUDA/GPU 验收 |
| D2 模型与 Ascend 后端 | Qwen2.5-Coder-7B-Instruct BF16，卡 0，端口 8201；部署与运行清单见 [D2 说明](d2_first_round.md) |
| D2 本轮执行数据 | `.artifacts/d2-next/` 保存轨迹、独立评测、冻结 raw、完整/合格数据集和风险区间；小型清单位于 `results/prediction/d2_mbpp_round/` |

代码和脚本中的资源根目录已由 `__file__` 或调用参数确定，不依赖 `/home/liuxv/LeasePath`。历史报告中的原主机数据保留历史含义；没有将缺失的归档或模型路径替换成虚构的本机资源。

## GBDT 离线训练环境（2026-10-31）

CPU `.venv/` 已安装 LightGBM 4.6.0、SciPy 1.18.1；项目增加可选依赖组 `prediction`，安装命令为：

```bash
cd /home/ma-user/takaya/leasepath
PYTHONPATH= .venv/bin/python -m pip install -e '.[test,measurement,prediction]'
PYTHONPATH= .venv/bin/python -m pip check
```

最初十题训练时全套 **102 项 pytest 通过**，日志为 `.artifacts/d2-next/pytest-gbdt-review-2.log`，历史方法见 [第二部分 Review](../reports/gbdt_review_2.md)。最新四轮合并结果为 [训练 v2](../reports/prediction_v2_50parents_review.md)，当前回归 121 项通过。CPU 训练不依赖 Ascend 推理服务。
