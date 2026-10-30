# 项目清理记录

日期：2026-10-28（Asia/Shanghai）。主目录现以 KV 复用时间预测研究为入口。

## 清理结果

旧文件已移出到项目外归档：

`/home/liuxv/LeasePath-archive/2026-10-28-before-prediction-cleanup/`

归档根目录的 `cleanup_manifest.json` 逐项记录原路径、新路径和修改前备份。本次移出 **77 个文件或目录项**，包括：

- 旧研究路线图、Q-Bridge PDF、W1 执行计划和旧文献比较材料。
- `configs/w1/`、`results/w1/`、空的 `data/w1/`。
- 旧 D2–D6 实验入口、测量/绘图/调参脚本、旧阶段测试及报告。
- 不再使用的 `replay.py`、`lease_replay.py`、`measured_cost.py`、`evaluation.py`。
- 重复的预测计划/调研 Word、PDF 导出版（当前保留 Markdown 源文档）。
- 旧虚拟环境、下载轮子、旧日志、压缩包、测量临时文件。

Python 缓存（`__pycache__`、`.pytest_cache`）作为可再生文件直接删除。

没有永久删除历史实验；也没有删除当前可选原生执行需要的 Qwen 权重、llama.cpp 源码/构建和动态库。迁移后的旧虚拟环境可能含原目录绝对路径，需要恢复原路径或重建，不作为当前项目环境使用。

## 重命名与解除依赖

| 原路径 | 当前路径 |
|---|---|
| `scripts/check_d2.py` | `scripts/check_event_contract.py` |
| `data/fixtures/d2_acceptance.json` | `data/fixtures/event_contract.json` |
| `tests/test_d2_acceptance.py` | `tests/test_event_contract.py` |
| `tests/test_source_d6.py` | `tests/test_source_replay.py` |
| `scripts/build_d6_native.py` | `scripts/build_native.py` |
| `patches/d6_native_bridge.cpp` | `patches/native_bridge.cpp` |
| `patches/llama-b7017-d4-kv.patch` | `patches/llama-kv.patch` |
| `patches/llama-b7017-d4-timing.patch` | `patches/llama-timing.patch` |

人工样例展开、预期比对和 JSONL 写入函数提取至 `src/leasepath/contract_fixtures.py`。D1 验收与测试直接导入模块，已不再通过旧 D2 脚本读取函数。

验收样例内部仍保留原始 `run_id=d2` 等不透明标识，避免没有必要地改写人工预期；这些 ID 不表示当前研究阶段。已有 BFCL 来源快照与许可也保持原样。

原始 D1 产物在归档的 `before-edit/results/prediction/d1_contract/`，当前产物按新模块路径重跑。新哈希与旧实验不同是预期行为，不能覆盖历史原始证据后宣称哈希未变。

## 核验入口

```bash
python3 -m pytest -q
python3 scripts/check_event_contract.py
python3 scripts/check_prediction_d1.py
python3 scripts/plot_prediction_d1.py
```

完整结构与保留资源用途见 [项目架构](architecture.md)。没有从归档路径导入当前测试所需的代码；当前运行链以主目录中的模块和固定来源为准。

本次实际核验：当前全部 **53 项 pytest 通过**；D1 **50 项检查通过**；事件契约 **8 个正例接受、16 个反例拒绝**；两张中文图已重新生成。主 README、docs 和 reports 的本地链接均有效，当前结果的输入/输出哈希与文件一致，归档清单中的目标均存在。
