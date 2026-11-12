# ISCA 2027 主轨摘要注册材料

状态：用户提供的 HotCRP 确认截图显示 **Registered submission as #1850**，摘要注册已成功。当前为 draft / not ready for review，全文投稿尚未完成。注册邮箱为 2493419176@sjtu.edu.cn，属于联系作者 Xu Liu。用户已确认标题及摘要不写具体数字的方向；下文为准备材料，尚未逐项比对线上保存内容。

投稿页面：https://isca2027.hotcrp.com/paper/1850/edit

## 注册确认与剩余事项（2026-11-10）

依据用户提供的注册结果截图：

- 注册编号：**1850**。
- 明确阻止完成全文投稿的缺项：**Submission PDF**、**Reserve reviewer (name / affiliation / email or "exempt")**。
- 其他待处理提醒：部分作者个人资料缺少 ORCID iD；填写作者的外部利益冲突；复核 PC conflicts 与作者信息。
- Reserve reviewer 的资格与豁免条件尚未看到实际字段说明，不得自行认定可填 exempt。
- 完成截止：北京时间 **2026-11-17 19:59**。截止前需补齐必填信息、提交全文，并确认系统状态已可送审；仅摘要注册成功不代表全文投稿完成。

- 官方投稿入口：https://isca2027.hotcrp.com/
- 官方 CFP：https://iscaconf.org/isca2027/submit/callforpapers.php
- 投稿要求：https://iscaconf.org/isca2027/submit/guidelines.php
- 摘要注册截止：2026-11-09 23:59 AoE，即北京时间 **2026-11-10 19:59**。
- 全文截止：2026-11-16 23:59 AoE，即北京时间 **2026-11-17 19:59**。
- 官方要求注册时标题、摘要、完整作者名单已确定；不接受占位摘要。作者后续变更仅可为纠正诚实错误并获主席批准。

## Title（可直接粘贴）

LeasePath: Distribution-Aware KV Cache Retention for Tool-Augmented LLM Agents

## Abstract（按用户要求，不含具体收益数字；可直接粘贴）

Tool-augmented large language model agents alternate between neural inference and external tool execution. These pauses create uncertain intervals before cached key-value (KV) prefixes are needed again, complicating cache retention under limited accelerator memory. We present LeasePath, a distribution-aware KV retention policy that estimates the residual time to the next compatible prefix demand and converts reuse probability mass into a finite-horizon, time-discounted retention score. The prediction target distinguishes demand from explicit retirement and observation censoring; a cache miss remains a demand event. The policy uses equal recomputation costs to isolate the contribution of reuse-time distributions, and selects legal evictions while respecting active references, prefix dependencies, and shared physical blocks. We implement LeasePath in a native inference backend with real model execution and tool feedback, and separately measure reused tokens, prefill time, and end-to-end job completion time. Preliminary experiments on a small development workload show improved prefix reuse and lower prefill and mean job completion times relative to a retired-first LRU baseline. These initial results motivate distribution-aware retention while highlighting the need to evaluate cache decisions through end-to-end latency rather than cache hits alone.

## 完整作者资料（不属于摘要正文）

已确认的作者顺序：

| 顺序 | 英文姓名 | 邮箱 | 英文单位 |
|---|---|---|---|
| 1 | Xu Liu | 2493419176@sjtu.edu.cn | Shanghai Jiao Tong University |
| 2 | Yuan Luo | yuanluo@sjtu.edu.cn | Shanghai Jiao Tong University |
| 3 | Chenhao Ying | yingchenhao@sjtu.edu.cn | Shanghai Jiao Tong University |
| 4 | Siyu Wu | 22616167@zju.edu.cn | Zhejiang University |

投稿账号邮箱：2493419176@sjtu.edu.cn。所属作者及联系作者：Xu Liu。不得用账号邮箱代填其他作者邮箱，也不得根据邮箱域名推断所有作者单位。

登录后的实际表单尚不可见。主题选项、利益冲突和声明以实际表单为准；这些信息须由作者确认。

## 提交和验收

1. 在官方 HotCRP 页面登录，或通过 Create an account 创建账号并完成邮箱验证。密码在网页输入。
2. 登录后打开新建投稿入口，录入最终标题、摘要、完整作者名单、联系作者，以及页面要求的其他字段。
3. 在摘要截止前完成页面规定的注册操作；仅创建账号不算注册投稿。
4. 检查投稿详情中的 submission ID、保存的标题/摘要/作者及注册状态，保存页面确认或回执。全文未上传时不能声称全文投稿完成。

## 证据与后续写作

摘要中的定性预实验结论来自 reports/kv_distribution_natural_2026-11-09.md 及 results/cache_policy/kv_distribution_natural_2026-11-09/natural-comparison.json，仅为三个已知开发任务的预实验，不能表述为独立留出集或完整压力矩阵结果。全文阶段扩展实验须保持已注册的研究主题和实质贡献一致。

正式全文上限为 11 页（参考文献除外），双盲，ACM sigconf 模板。官方要求对生成式 AI 参与论文准备按规定披露；本材料的摘要由 AI 辅助起草，作者需审核并承担内容责任。
