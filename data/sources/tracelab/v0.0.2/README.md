# TraceLab 数据来源

本目录保存 [TraceLab](https://github.com/uw-syfi/TraceLab) 的固定发布版本 v0.0.2，commit 61fea8f97277d0aa247cc2e4c7e31f90ed2bea1c。

- syfi_coding_trace.jsonl.gz：官方 JSONL 压缩数据，约 101 MB；SHA-256 见 provenance.json。
- reference/：用于审计字段和语义的上游说明、schema 与辅助脚本副本。代码只阅读，没有导入或执行。
- provenance.json：固定来源、校验时间、许可证、原始数据及参考文件哈希。

数据集许可为 **CC BY 4.0**。使用时请署名 TraceLab（SyFI Lab, University of Washington），链接 [TraceLab](https://tracelab.cs.washington.edu) 和 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，并说明 LeasePath 做过改编。

此处保留上游原始文件，不混放 LeasePath 派生数据。可训练的代理数据位于 data/processed/tracelab/v0.0.2/；重建命令见项目根目录 README。
