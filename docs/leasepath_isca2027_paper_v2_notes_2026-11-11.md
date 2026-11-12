# LeasePath 论文 v2 更新说明与 review 入口

日期：2026-11-11。此版为扩写审阅稿；原 PDF、上传 TeX、本地旧稿和原实验数据保留。没有在本次论文更新中启动新的 GPU 性能实验。

## 文件

- [新版 PDF](leasepath_isca2027_paper_v2_2026-11-11.pdf)
- [新版 TeX](leasepath_isca2027_paper_v2_2026-11-11.tex)
- [更新后大纲](leasepath_iclr_outline_v2_2026-11-11.md)
- [后续实验计划](leasepath_followup_experiments_2026-11-11.md)
- [表图与源数据哈希](leasepath_isca_v2_assets/manifest.json)
- [本轮实测原报告](../reports/continuous_e2e_development_2026-11-11.md)

## 基线与改动

以用户上传的 `leasepath_iclr_paper_v1_2026-11-10.tex` 为基线；它实际使用 acmart 双栏，与服务器上同名的旧单栏 article 源文件不同。基线完整副本保存在 `leasepath_isca_v2_assets/source/uploaded_manuscript_v1.tex`，SHA256 为 `44f61945376582cd1cee34aeef3fa0059b97c27c01ba33b5ffa1981a2bf10432`。

新版更新 Abstract、Introduction、预测模型/静态动态定义、实验设置、结果、限制和结论；新增六项目持续对话的五臂表、时间分解表、时间诊断表和真实 KV 收益图。修正旧稿“经验在线尚未测”“全部预测器无队列特征”等过时现状，同时保留旧三题结果与成本负结果的历史适用范围。

新增表图由 `verified-e2e-metrics.json`、采集审计与初始训练摘要生成，核验每臂48/48、244请求、1,227,620输入tokens、hit+prefill恒等式、逐owner prompt一致性及六次更新的因果证明。旧图保留；原上传源文件引用但服务器缺失的Agent示意图，从上传PDF第2页裁回矢量版本，出处/裁剪参数另存。

旧表格标题曾位于浮动体外，新版将标题与表格放入同一浮动体；过长公式换行，避免跨栏。论文正文使用匿名投稿元数据，沿用原稿 `#NaN`、ISBN/DOI占位符，不猜测真实投稿号，也不在本轮发布/替换外部投稿文件。

## 本轮主要数字与解释

动态GBDT分布相对LRU：多复用329,344 tokens，prefill累计减少718.835秒（44.86%），平均用户轮次响应减少77.927秒（19.76%）。平均响应含真实排队、工具、模型、预测调度与独立验收；加载与用户到达前思考时间单列。

静态GBDT分布、动态GBDT窗口、动态GBDT分布的平均响应分别316.997、316.236、316.368秒。动态分布比静态约快0.20%，比动态窗口约慢0.04%。论文明确不据此选赢家；混合方案只出现在待实验方向。

关键限制：六个 authored 开发项目也用于初始拟合，E2E不是未见项目测试；每臂48轮相关用户需求，不是48独立任务；新1079个快照全部U，未验证真实D/C风险；五臂仅一个固定顺序周期；共享主机加载时间30–179秒，不能称环境完全恒定。新实验6并发指会话/工具并发，模型仍FCFS串行服务。

## 后续实验安排

计划优先级为：五臂位置平衡重复 → 来源隔离的持续项目扩展 → 评分/预测器/更新分离消融 → 合法容量/并发压力 → 漂移/混合及完整外部方法对照。详细编号E10–E15、D7–D8见实验计划。容量150/192/224块仅为当前长历史候选，需先核验扩展项目与物理资源再冻结。

## 构建与审阅

在服务器执行：

```bash
bash /home/liuxv/LeasePath-new-server-data/docs/leasepath_isca_v2_assets/build_paper.sh
```

脚本只编译现有TeX，不覆盖人工正文。若需要重新从上传基线生成整版，另行运行 `python -B docs/leasepath_isca_v2_assets/generate_update.py`；**该命令会覆盖v2 TeX及自动表图**，人工修改正文后不应直接运行。

服务器缺少全局acmart，制品包含CTAN acmart 2.21（2026-11-01）及来源dtx/ins；上传PDF使用1.83。当前环境未安装libertine/zi4/newtxmath，acmart自动使用默认字体。因此此PDF适合内容review，版面不等于上传稿的逐页复刻，最终模板字体、页数、元数据与参考文献仍需投稿前检查。该说明不会改变或削弱实测数字。

编译日志保存在 `leasepath_isca_v2_assets/build/`。正式投稿前还需独立核对引用原文与当前会议格式规则；本轮没有把已有参考文献自动标为全部核验。
