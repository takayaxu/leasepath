# 本机部署与路径说明

部署日期：2026-10-30（Asia/Shanghai）。仓库为 `takayaxu/leasepath`，本机根目录为 `/home/ma-user/takaya/leasepath`。

本项目是 Python 研究工具，CPU 环境支持事件契约、状态快照、缓存回放与数据审计。D2 已增加本地 Ascend 推理服务，接口为 `http://127.0.0.1:8201/v1`；模型、Agent 和插桩安装见 [D2 首轮部署](d2_first_round.md)。当前任务采集与独立评测入口为 [MBPP 派生执行轮](d2_mbpp_round.md)，实际质量和数据规模见 [本轮报告](../reports/d2_mbpp_round_acceptance.md)；[首轮报告](../reports/d2_first_round_acceptance.md) 保留历史结果。

跨轮的数据目录、清单、字段与恢复边界统一见 [数据集说明](dataset_card.md)。`.artifacts/` 不随 Git 分发，仅克隆仓库不会恢复完整训练样本。

2026-10-31 新增 [秒级任务扩展 v1](d2_expansion_v1.md)，沿用相同服务和两个虚拟环境；准备、执行、独立复测命令见该文。数据另存 `.artifacts/d2-expansion-v1/`，小型结果在 `results/prediction/d2_expansion_v1/`，没有覆盖原 MBPP 或 GBDT 结果。该轮回归 105 项通过。

同日继续完成 [3–10 秒/失败后重测 v2](d2_repair_v2.md)：主数据在 `.artifacts/d2-repair-v2-guided/`，最初不成功的诊断在 `.artifacts/d2-repair-v2/`，小型结果在 `results/prediction/d2_repair_v2/`。使用 `run_d2_repair_guided.py`，复现命令见该文。该轮回归 **114 项通过**，日志为 `.artifacts/d2-repair-v2-guided/pytest-regression.log`；采集阶段未重训，旧模型结果保持原版本。

[母题扩展 v3](d2_diversity_v3.md) 沿用同一服务与环境，新增 34 个 MBPP 母题。任务、轨迹、独立评测及冻结事件在 `.artifacts/d2-diversity-v3/`，可审阅清单在 `results/prediction/d2_diversity_v3/`；从准备、参考验证到采集/导出/核验的命令见该文。全量运行保留失败，完整性和任务正确性分别统计。最新跨轮数量和路径以 [数据集说明](dataset_card.md) 及 [50 母题清单](../results/prediction/dataset_inventory_50parents_2026-10-31.json) 为准，旧轮与模型结果保持原版本。

四轮合并后的 [训练 v2](../reports/prediction_v2_50parents_review.md) 已完成，入口为 `train_prediction_v2.py`，独立复核入口为 `verify_prediction_v2.py`。合并开发样本、折内模型和 OOF 保存于 `.artifacts/prediction/prediction_v2_50parents/`；6 份全量重拟合模型、指标和清单在 `results/prediction/prediction_v2_50parents/`。当前回归 **121 项通过**，日志在前者的 `pytest-regression.log`。训练仅使用 CPU `.venv`，不要求启动 Ascend 服务，也未新采集 Agent 任务；完整新目录复现命令见该 Review。

## 下一轮资源规划（2026-10-31）

[只读资源快照](../results/planning/real_development_2026-10-31/hardware_snapshot.json) 核查到 8 张 64 GiB HBM 的 Ascend 910B2、192 逻辑 CPU、约 1.5 TiB RAM。当时所有卡均有其他负载，每卡约占用 45 GiB；以下历史启动命令不意味着当前仍有足够可用资源。新实验需安排独占卡窗口，不直接停止已有服务。

下一轮候选是 Codex CLI＋Qwen2.5-Coder-32B-Instruct BF16＋vLLM-Ascend，TP=2 起步，**尚未通过协议、模型、多卡探针和任务验收**，不能把它当作已部署配置。现有单卡探针/导出器的请求 ID、单次 lookup、无抢占假设需独立扩展。资源、接入门槛与每阶段 review 见 [真实开发计划](../reports/real_development_lookup_plan_2026-10-31.md)。

checkout 所在 overlay 当时约剩 45 GiB，数据盘 `/home/ma-user/work` 约剩 3.6 TiB。新权重、原始 tokens、工具轨迹和运行快照规划放在 `/home/ma-user/work/leasepath/real-development-v1/`，配置使用显式路径和冻结 manifest；该目录是计划位置，本次未生成新实验数据。已有 `.artifacts/d2*` 和 `models/` 保持原路径与来源。

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
