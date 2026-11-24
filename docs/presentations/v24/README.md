# v24 · 快速决策与 AgentCore GPU 运行方案

[PPTX](./Strands-Decider-v24-快速决策与AgentCore-GPU方案-41页-20261026.pptx) · [PDF](./Strands-Decider-v24-快速决策与AgentCore-GPU方案-41页-20261026.pdf) · [全篇预览](./slides-overview.png) · [更新页预览](./updated-overview.png) · [讲稿与来源](./讲稿与来源.md) · [核验记录](./render-audit.json)

**基于 v23，共 41 页。** 原 34 页正文与图片保留；首页和资料页重写，新增 5 页。

| 页码 | 内容 |
| --- | --- |
| P1 | Jev 官方 70–500 ms、40–200× 数据；决策模型补充大模型 |
| P2–7 | 趋势、原理、BERT / 决策模型 / GPT、JevBench |
| P8 | 已实现：Jev 智能驾驶意图识别微调；潜在：智能家居固定动作与流程 |
| P9–23 | 演示架构、支付、模型路由、图片及其他场景结果 |
| P24–28 | Decider 领域微调过程与效果 |
| P29–31 | vLLM 部署与并发实测 |
| P32–35 | EC2 对比、AgentCore GPU 架构、A2A 协作目标、Evaluation 实测 |
| P36–38 | Jev、EC2 On-Demand 与一年 RI 成本 |
| P39–41 | 选型、资料与 internal only 附录 |

首页数字明确为 Jev 厂商发布数据，并保留可点击来源；客户案例的 90% / 100 ms 来自用户提供的客户项目结果，未在本项目重测。AgentCore 已验证 GPU Runtime、8 个业务会话、48 spans 与 4 条 Correct 评估；A2A、Memory、Gateway 等为待接入方案。不把不同模型、硬件或调用路径的耗时混算。

[完整 AgentCore 架构图](./assets/agentcore-gpu-architecture.svg) · [新增事实与归属](./sources/new-claims.json) · [构建清单](./build-manifest.json)

本版没有新增模型调用、微调任务、云资源或 GitHub 推送。原 v23 保留不变。P41 延续 internal only 标注。
