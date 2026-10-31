# D2 首轮运行

本机根目录：`/home/ma-user/takaya/leasepath`。配置文件为 [`configs/prediction/d2_first_round.json`](../configs/prediction/d2_first_round.json)，实测结论见 [首轮报告](../reports/d2_first_round_acceptance.md)。本轮采用真实推理和真实任务，受控缓存检查单独导出。

## 环境分工

| 环境 | 用途 |
|---|---|
| `.venv/` | LeasePath、事件校验、数据导出、任务 pytest；Python 3.12 下安装 `setuptools==80.9.0` 提供旧 SymPy 需要的 distutils |
| `.venv-agent/` | mini-swe-agent 2.4.6、requests、pyarrow |
| `/home/ma-user/work/qwen-services/runtime_env.sh` | 已有 Ascend 推理环境，启动脚本在子进程中加载 |
| `.artifacts/d2/runtime-plugin/` | 单独安装的 LeasePath vLLM 插件，不修改已有推理环境的包 |

固定 mini-swe-agent commit 为 `04d809ceab9df28f9adaed044884180159172930`，模型 revision 为 `c03e6d358207e414f1eca0bb1891e29f1db0e242`。运行环境使用 vLLM `0.23.0+empty`、vLLM-Ascend `0.1.dev4229+g7495e9d69`、torch `2.10.0+cpu`、torch-npu `2.10.0.post4`、transformers `5.5.4`、CANN toolkit `8.5.2`、Ascend driver package `25.5.1`。这里的 torch 版本字符串包含 cpu，实际通过 torch-npu 使用 Ascend。

模型逻辑路径为 `models/Qwen2.5-Coder-7B-Instruct`，本机链接到 `/home/ma-user/work/leasepath/models/Qwen2.5-Coder-7B-Instruct`。`source-manifest.json` 和 `download-complete.json` 保存下载来源及逐文件校验。权重与固定 HF commit 的 LFS SHA256 比较，小文件另核对 Git blob SHA1。换机须准备相同权重及支持这些插桩接口的运行环境，并修改配置中的本机路径。

## 启动与查看

```bash
cd /home/ma-user/takaya/leasepath
PYTHONPATH= .venv/bin/python scripts/d2_service.py status
# 服务未运行时启动；模型加载需要时间，随后再次检查 status。
PYTHONPATH= .venv/bin/python scripts/d2_service.py start
tail -n 30 .artifacts/d2/server.log
curl --noproxy '*' -fsS http://127.0.0.1:8201/health
# 停止本项目启动的服务
PYTHONPATH= .venv/bin/python scripts/d2_service.py stop
```

OpenAI 兼容接口为 `http://127.0.0.1:8201/v1`，模型名 `leasepath-qwen25-coder7b`。使用卡 0、BF16、TP=1、16,384 上下文、128 token/block、最多 4 个并行序列、每批 2,048 tokens、0.5 显存比例；同步调度、eager、prefix caching、chunked prefill。服务仅监听本机；为受控检查启用 dev cache reset。已有 Qwen3.5 服务继续使用自己的环境与端口。

服务清单 `.artifacts/d2/run-manifest.json` 记录完整参数、权重哈希、运行库版本及兼容配置 ID。**保存一次实验的 raw、manifest 和导出结果后再重启服务**；重启会重写活动 manifest，跨配置日志不能用新的 manifest 重新标注。

本次已将原始日志和配置冻结到 `.artifacts/d2/frozen-first-round/`，自主任务数据在 `.artifacts/d2/pilot-dataset/`，受控检查数据在 `.artifacts/d2/backend-dataset/`。重导出冻结数据时显式使用 `--raw .artifacts/d2/frozen-first-round/raw.jsonl --manifest .artifacts/d2/frozen-first-round/run-manifest.json`，并选择新的输出目录。

## 首次准备 Agent、模型与任务

本机已经完成准备；以下命令供新环境使用，已有任务目录时准备脚本会拒绝覆盖。

```bash
git clone https://github.com/SWE-agent/mini-swe-agent.git .artifacts/mini-swe-agent
git -C .artifacts/mini-swe-agent checkout 04d809ceab9df28f9adaed044884180159172930
python3 -m venv .venv-agent
PYTHONPATH= .venv-agent/bin/python -m pip install -e .artifacts/mini-swe-agent pyarrow
PYTHONPATH= .venv/bin/python -m pip install 'setuptools==80.9.0'

# 固定 HF revision 的 blobs 元数据与 ModelScope 逐文件 revision 交叉校验。
curl -fL 'https://huggingface.co/api/models/Qwen/Qwen2.5-Coder-7B-Instruct/revision/c03e6d358207e414f1eca0bb1891e29f1db0e242?blobs=true' -o /tmp/leasepath-d2-hf-model.json
curl -fL 'https://modelscope.cn/api/v1/models/Qwen/Qwen2.5-Coder-7B-Instruct/repo/files?Revision=master&Recursive=true' -o /tmp/leasepath-d2-ms-model.json
PYTHONPATH= .venv/bin/python scripts/prepare_d2_model.py \
  --metadata /tmp/leasepath-d2-ms-model.json --hf-metadata /tmp/leasepath-d2-hf-model.json

curl -fL --retry 3 'https://huggingface.co/datasets/princeton-nlp/SWE-bench_Lite/resolve/main/data/test-00000-of-00001.parquet' -o /tmp/leasepath-swe-test.parquet
PYTHONPATH= .venv-agent/bin/python scripts/prepare_d2_tasks.py --parquet /tmp/leasepath-swe-test.parquet
```

任务来源是 SWE-bench Lite test 中预先固定的五个 SymPy issue（20590、21614、21847、23262、24909）。来源 split 名为 test，**本项目仍将这五个任务全部视为 pilot development**，不作为独立评测集。`tasks.json` 保存 parquet 哈希、upstream base commit、源码 archive 哈希和测试补丁哈希。Agent 只能通过提示词获得 issue，官方修复补丁不进入任务输入，公开测试补丁仅在独立评测目录中应用。任务以本机 shell 执行，未使用官方 SWE-bench 容器。

## 执行、评测与导出

```bash
PYTHONPATH= .venv/bin/python scripts/check_d2_backend.py
PYTHONPATH= .venv/bin/python scripts/evaluate_d2_tasks.py --mode baseline
PYTHONPATH= .venv-agent/bin/python scripts/run_d2_pilot.py
PYTHONPATH= .venv/bin/python scripts/evaluate_d2_tasks.py --mode agent
PYTHONPATH= .venv/bin/python scripts/export_d2_dataset.py \
  --results .artifacts/d2/pilot/results.json --output .artifacts/d2/pilot-dataset
PYTHONPATH= .venv/bin/python -m pytest -q
```

这些命令为一次新实验的执行顺序；本次已存在的 pilot/评测目录会被拒绝覆盖。复跑应准备独立任务工作区：prepare 使用 `--output`；runner 使用 `--tasks`、`--output`；评测使用 `--tasks`、`--pilot`、`--output` 指定相应目录。不要接着已修改的代码冒充相同初始状态。采样为 temperature=0、top_p=1、seed=23、最多输出 768 tokens；每任务最多 20 步、900 秒，每条工具命令最多 60 秒。保留失败、格式错误及预算退出；上下文不静默截断，超长输入会由服务报错，缺少后端观测的请求无法通过严格导出。

## 数据含义

原始事件保存在 `.artifacts/d2/raw.jsonl`，包含实际 token IDs。模型和 tokenizer 已固定，每条请求可追到客户端 UUID、引擎请求、真实 lookup 和正常 release。idle 的时间取自引擎释放活动使用之后，前缀选择该请求**输入内最长的完整 block 对齐前缀**，不会回看未来请求选对象。对应块记录 slot/hash/refcount，未伪装成完整物理生命周期日志。

同一 owner 后续真正 lookup 的兼容 token 前缀生成 U，物理 miss 也算 U。runner 明确丢弃该上下文时生成 D，步数耗尽本身不是 D。完整观测区间结束时仍开放的关系才生成 C。受控 cold/warm/reset 检查与自主任务分开，不能为凑齐标签把两者混合训练。

窗口内尚无事件对应已知等待 `h0`，不自动成为 C。固定 H 的行政删失可以另建派生视图，不能覆盖原始完整标签；详见 [数据契约](prediction_contract.md#时间窗口内等待与右删失) 与 [窗口统计](../results/prediction/d2_first_round/horizon-summary.json)。本轮 C=0 本身不构成导出错误。

| 本地文件 | 内容 |
|---|---|
| `pilot/<task>/trajectory.json`、`agent.patch`、`result.json` | Agent 消息、修改和退出状态 |
| `evaluation-runs/{baseline,agent}/` | 独立源码与公开 issue 测试结果 |
| `pilot-dataset/events.jsonl` | 统一契约事件及后端证据 |
| `pilot-dataset/episode_labels.jsonl` | U/D/C 与起止时间 |
| `pilot-dataset/snapshots.jsonl` | 截至 t0 可见的特征，episode 权重合计 1 |
| `pilot-dataset/targets.jsonl` | 与特征分开的结局和剩余时间 |
| `pilot-dataset/summary.json` | 完整性、覆盖、命中与输出哈希 |

导出器拒绝序号缺口、配置不一致、缺少请求阶段、token 变化、prefill 计数不一致、抢占或异常中断，不会把这些情况改成 C。全局队列长度和 retry 等未知特征保持 unknown；命令类别记录在事件中，尚未扩展进入预测特征白名单。

当前只提供请求边界块快照与调度 token 计数，未完成全部分配/淘汰轨迹、设备 kernel 计时、完整探针开销对照或在线策略收益验证。首轮只能证明采集链能运行；扩大到 60–100 workflows、并发与独立来源切分仍需后续验收。
